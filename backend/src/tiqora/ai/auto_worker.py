"""Auto-reply + auto-summary tick (plan §3.4/§3.5/§3.9, Phase D).

Runs inside ``tiqora-ai-worker`` (:mod:`tiqora.ai.worker`), never inside the
main takeover worker. Consumes ``tiqora_event_outbox`` with its **own**
watermark cursor (``KEY_AI_OUTBOX_WATERMARK``) — deliberately separate from
the main worker's outbox-drain cursor (indexer/webhooks) so the two
consumers never interfere.

At-most-once semantics: the watermark always advances past a processed
batch, even if an individual event failed (its error is logged and recorded
on ``tiqora_ai_ticket_state.last_error``; the batch is not replayed). Losing
a single event to a transient error does not lose the ticket forever: the
next customer article on that ticket produces a new ``ArticleCreate`` event,
and the per-ticket loop guard (``ticket_state.last_customer_article_id``,
enforced inside :func:`tiqora.ai.runtime.run_ticket_agent`) only prevents
*reprocessing the same* article — it does not skip newer ones.

Caps are checked **before** invoking the agent runtime (the runtime itself
has no notion of hourly/daily budgets — plan §3.6 keeps that queue-budget
enforcement here, separate from the per-user ACL limits the manual path
uses, so the two paths never cross-charge each other).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai import summary as summary_service
from tiqora.ai.context import (
    TicketNotFoundError,
    get_or_create_state,
    ticket_snapshot,
)
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.kb_wiring import build_llm_client, kb_bundle, kb_get_article_fn, kb_search_fn
from tiqora.ai.models import FEATURE_AUTO_REPLY, TiqoraAiQueuePolicy, TiqoraAiUsage
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.ai.runtime import TRIGGER_AUTO, AgentRunError, AgentRunResult, run_ticket_agent
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_AI_GLOBAL_REPLIES_PER_HOUR,
    KEY_AI_OUTBOX_WATERMARK,
    get_setting,
    get_setting_int,
    set_setting,
)

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class _OutboxEvent:
    id: int
    event_type: str
    ticket_id: int
    payload: dict[str, Any]


async def _next_outbox_batch(
    session: AsyncSession, after_id: int, batch_size: int
) -> list[_OutboxEvent]:
    rows = (
        await session.execute(
            text(
                "SELECT id, event_type, ticket_id, payload FROM tiqora_event_outbox"
                " WHERE id > :after ORDER BY id ASC LIMIT :n"
            ),
            {"after": after_id, "n": batch_size},
        )
    ).fetchall()
    out: list[_OutboxEvent] = []
    for row in rows:
        payload: dict[str, Any] = {}
        if row[3]:
            try:
                payload = json.loads(row[3])
            except (TypeError, ValueError):
                payload = {}
        out.append(
            _OutboxEvent(
                id=int(row[0]), event_type=str(row[1]), ticket_id=int(row[2]), payload=payload
            )
        )
    return out


async def _article_sender_type(session: AsyncSession, article_id: int) -> str | None:
    row = (
        await session.execute(
            text(
                "SELECT st.name FROM article a"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " WHERE a.id = :aid LIMIT 1"
            ),
            {"aid": article_id},
        )
    ).first()
    return str(row[0]) if row else None


async def _global_replies_per_hour_cap(session: AsyncSession) -> int | None:
    raw = await get_setting(session, KEY_AI_GLOBAL_REPLIES_PER_HOUR)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


async def _auto_replies_last_hour(session: AsyncSession, *, queue_id: int | None) -> int:
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    filters = [
        TiqoraAiUsage.feature == FEATURE_AUTO_REPLY,
        TiqoraAiUsage.success.is_(True),
        TiqoraAiUsage.ts >= since,
    ]
    if queue_id is not None:
        filters.append(TiqoraAiUsage.queue_id == queue_id)
    return int(
        (
            await session.execute(select(func.count()).select_from(TiqoraAiUsage).where(*filters))
        ).scalar_one()
    )


async def _tokens_used_today(session: AsyncSession, queue_id: int) -> int:
    now = datetime.now(UTC).replace(tzinfo=None)
    day_start = datetime(now.year, now.month, now.day)
    total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(TiqoraAiUsage.prompt_tokens + TiqoraAiUsage.completion_tokens), 0
                )
            ).where(
                TiqoraAiUsage.feature == FEATURE_AUTO_REPLY,
                TiqoraAiUsage.queue_id == queue_id,
                TiqoraAiUsage.ts >= day_start,
            )
        )
    ).scalar_one()
    return int(total)


async def _cap_reason(
    session: AsyncSession, *, ticket_id: int, queue_id: int, policy: TiqoraAiQueuePolicy, state: Any
) -> str | None:
    """Return a short reason string if any Phase D cap blocks this run, else None."""
    if state.auto_reply_count >= policy.max_auto_replies:
        return "max_auto_replies"
    if state.clarification_count >= policy.max_clarifications:
        return "max_clarifications"
    if policy.max_replies_per_hour is not None:
        recent = await _auto_replies_last_hour(session, queue_id=queue_id)
        if recent >= policy.max_replies_per_hour:
            return "queue_rate_limit"
    global_cap = await _global_replies_per_hour_cap(session)
    if global_cap is not None:
        recent_global = await _auto_replies_last_hour(session, queue_id=None)
        if recent_global >= global_cap:
            return "global_rate_limit"
    if policy.budget_tokens_day is not None:
        tokens_today = await _tokens_used_today(session, queue_id)
        if tokens_today >= policy.budget_tokens_day:
            return "budget_tokens_day"
    return None


async def _process_customer_article_event(
    session: AsyncSession, settings: Settings, event: _OutboxEvent
) -> AgentRunResult | None:
    """Handle one ``ArticleCreate`` outbox event. Returns the agent run
    result iff the auto-reply runtime was actually invoked (``None`` for
    every skip — irrelevant event, disabled policy, cap hit, loop guard)."""
    article_id = event.payload.get("article_id")
    if article_id is None:
        return None
    sender_type = await _article_sender_type(session, int(article_id))
    if sender_type != "customer":
        return None

    ticket_id = event.ticket_id
    try:
        ticket = await ticket_snapshot(session, ticket_id)
    except TicketNotFoundError:
        return None

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or not policy.enabled_auto_reply or policy.service_user_id is None:
        return None

    state = await get_or_create_state(session, ticket_id)

    # Loop guard (plan §3.9): a run already handled this or a newer
    # customer article on this ticket.
    already_processed = (
        state.last_customer_article_id is not None
        and int(article_id) <= state.last_customer_article_id
    )
    if already_processed:
        return None

    reason = await _cap_reason(
        session, ticket_id=ticket_id, queue_id=ticket.queue_id, policy=policy, state=state
    )
    if reason is not None:
        logger.info(
            "ai_auto_worker_cap_skip", ticket_id=ticket_id, queue_id=ticket.queue_id, reason=reason
        )
        return None

    llm = await build_llm_client(session, settings, policy.llm_provider_id, policy.model_override)
    bundle = await kb_bundle(session, settings, policy.service_user_id, policy)

    return await run_ticket_agent(
        session,
        settings=settings,
        llm=llm,
        ticket_id=ticket_id,
        trigger=TRIGGER_AUTO,
        acting_user_id=None,
        run_id=uuid.uuid4().hex,
        worker_instance="ai-worker",
        kb_bundle=bundle,
        kb_search_fn=kb_search_fn(session, settings, policy.service_user_id),
        kb_get_article_fn=kb_get_article_fn(session, settings, policy.service_user_id),
    )


async def _maybe_auto_summarize(session: AsyncSession, settings: Settings, ticket_id: int) -> bool:
    if not await summary_service.auto_summary_due(session, ticket_id):
        return False
    try:
        ticket = await ticket_snapshot(session, ticket_id)
    except TicketNotFoundError:
        return False
    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or policy.llm_provider_id is None:
        return False
    llm = await build_llm_client(session, settings, policy.llm_provider_id, policy.model_override)
    await summary_service.summarize_ticket(
        session,
        llm=llm,
        ticket_id=ticket_id,
        trigger=summary_service.TRIGGER_AUTO,
        acting_user_id=None,
    )
    return True


async def run_auto_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """One tick: drain new ``tiqora_event_outbox`` rows, run auto-reply for
    relevant customer-article events, and auto-summarize tickets touched by
    this batch whose queue thresholds are now exceeded (plan §3.5's
    "einfachste robuste Variante": only tickets seen in this batch, not a
    full-table scan)."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()

    async with factory() as session:
        if not await is_tiqora_primary(session):
            logger.info("ai_auto_worker_gate_closed")
            return {"gate_open": 0}
        watermark = await get_setting_int(session, KEY_AI_OUTBOX_WATERMARK, 0)
        batch = await _next_outbox_batch(session, watermark, _BATCH_SIZE)

    totals = {"events": 0, "auto_replies": 0, "summaries": 0, "errors": 0}
    last_id = watermark
    touched_ticket_ids: set[int] = set()

    for event in batch:
        last_id = event.id
        totals["events"] += 1
        if event.event_type != "ArticleCreate":
            continue
        touched_ticket_ids.add(event.ticket_id)
        try:
            async with factory() as session:
                result = await _process_customer_article_event(session, cfg, event)
            if result is not None and result.status == "sent":
                totals["auto_replies"] += 1
        except AgentRunError:
            # Expected abort conditions (lock held, policy disabled between
            # read and write, ACL, gate regression mid-batch) — not a bug,
            # just a skip. run_ticket_agent already persisted last_error.
            logger.info("ai_auto_worker_run_aborted", event_id=event.id, ticket_id=event.ticket_id)
        except Exception:  # noqa: BLE001 — one broken event must not stop the batch
            logger.exception(
                "ai_auto_worker_event_failed", event_id=event.id, ticket_id=event.ticket_id
            )
            totals["errors"] += 1

    for ticket_id in touched_ticket_ids:
        try:
            async with factory() as session:
                if await _maybe_auto_summarize(session, cfg, ticket_id):
                    totals["summaries"] += 1
        except Exception:  # noqa: BLE001 — one broken ticket must not stop the batch
            logger.exception("ai_auto_worker_summary_failed", ticket_id=ticket_id)
            totals["errors"] += 1

    if last_id != watermark:
        async with factory() as session:
            await set_setting(session, KEY_AI_OUTBOX_WATERMARK, str(last_id))

    logger.info("ai_auto_worker_tick", **totals)
    return totals


__all__ = ["run_auto_tick"]
