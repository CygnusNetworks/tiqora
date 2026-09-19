"""AI triage tick — routes a brand-new ticket into the right queue.

Runs inside ``tiqora-ai-worker`` (:mod:`tiqora.ai.worker`) **before**
:func:`tiqora.ai.auto_worker.run_auto_tick` in the same tick. That ordering
is load-bearing, not cosmetic: triage commits the move before the reply
agent looks up ``get_queue_policy_by_queue(ticket.queue_id)`` for the same
``ArticleCreate`` event, so the answer is written under the *destination*
queue's policy.

Own watermark, deliberately separate from the auto-reply consumer. Like
that one, the batch is drained and the watermark advanced even while
``operation_mode`` is not ``tiqora_primary`` — otherwise cutting over
would suddenly triage a backlog of tickets humans dealt with weeks ago.

**The watermark is never seeded to 0.** ``get_setting_int(..., 0)`` cannot
tell a missing row from a stored zero, and that fallback would replay the
entire outbox. The first tick reads the raw ``str | None``, seeds from
``MAX(tiqora_event_outbox.id)`` and processes nothing.

A row in ``tiqora_ai_triage`` is written for every ticket that gets this
far, including ``no_action`` and ``error`` — it is the permanent
"already triaged" guard (``UNIQUE(ticket_id)``) as well as the record that
later calibrates the thresholds.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai import triage as triage_service
from tiqora.ai import usage as usage_service
from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.context import (
    TicketNotFoundError,
    collect_known_names,
    get_or_create_state,
    load_articles,
    pii_never_mask,
    ticket_snapshot,
)
from tiqora.ai.gate import is_tiqora_primary
from tiqora.ai.kb_wiring import build_llm_client
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.models import (
    FEATURE_TRIAGE,
    TRIAGE_STATUS_APPLIED,
    TRIAGE_STATUS_ERROR,
    TRIAGE_STATUS_NO_ACTION,
    TRIAGE_STATUS_OPEN,
    TiqoraAiQueuePolicy,
    TiqoraAiTriage,
)
from tiqora.ai.outbox import (
    OUTBOX_BATCH_SIZE,
    OutboxEvent,
    article_sender_type,
    max_outbox_id,
    next_outbox_batch,
)
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.ai.senders import matches_ignored
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_AI_TRIAGE_WATERMARK,
    get_setting,
    set_setting,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# A ticket older than this is never triaged, whatever the outbox says. Pure
# backstop: if the watermark is ever reset (deleted setting row, restored
# backup), this stops the worker re-routing months of history.
MAX_TICKET_AGE = timedelta(hours=24)

# Znuny's "no agent has locked this" lock id.
_UNLOCKED_LOCK_ID = 1


async def _first_article_id(session: AsyncSession, ticket_id: int) -> int | None:
    """Lowest article id on the ticket.

    ``MIN(id)``, never ``ORDER BY create_time`` — Znuny's create_time has
    second resolution and genuine ties exist.
    """
    row = (
        await session.execute(
            text("SELECT MIN(id) FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
        )
    ).first()
    return int(row[0]) if row and row[0] is not None else None


async def _already_triaged(session: AsyncSession, ticket_id: int) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM tiqora_ai_triage WHERE ticket_id = :tid LIMIT 1"),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None


async def _has_later_article(session: AsyncSession, ticket_id: int, article_id: int) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM article WHERE ticket_id = :tid AND id > :aid LIMIT 1"),
            {"tid": ticket_id, "aid": article_id},
        )
    ).first()
    return row is not None


async def _has_move_history(session: AsyncSession, ticket_id: int) -> bool:
    """True once anybody — human, GenericAgent, process engine — has moved
    this ticket. Their routing decision outranks ours."""
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM ticket_history th"
                " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                " WHERE th.ticket_id = :tid AND tht.name = 'Move' LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None


async def _ticket_guard_row(session: AsyncSession, ticket_id: int) -> Any:
    return (
        (
            await session.execute(
                text(
                    "SELECT t.ticket_lock_id, t.create_time,"
                    " (SELECT s.ai_escalated_at FROM tiqora_ai_ticket_state s"
                    "    WHERE s.ticket_id = t.id) AS ai_escalated_at"
                    " FROM ticket t WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )


async def _skip_reason(session: AsyncSession, *, ticket_id: int, article_id: int) -> str | None:
    """Everything that disqualifies this ticket, cheapest check first.

    Four of these (first article, no earlier triage row, no Move history,
    age cutoff) independently prevent queue ping-pong. That redundancy is
    the point — see the regression test.
    """
    first_id = await _first_article_id(session, ticket_id)
    if first_id is None or first_id != article_id:
        return "not_first_article"
    if await _already_triaged(session, ticket_id):
        return "already_triaged"
    if await _has_later_article(session, ticket_id, article_id):
        return "ticket_continued"
    if await _has_move_history(session, ticket_id):
        return "already_moved"
    row = await _ticket_guard_row(session, ticket_id)
    if row is None:
        return "ticket_gone"
    if int(row["ticket_lock_id"]) != _UNLOCKED_LOCK_ID:
        return "ticket_locked"
    if row["ai_escalated_at"] is not None:
        return "escalated_to_human"
    created = row["create_time"]
    if isinstance(created, datetime):
        age = datetime.now(UTC).replace(tzinfo=None) - created.replace(tzinfo=None)
        if age > MAX_TICKET_AGE:
            return "ticket_too_old"
    return None


def _policy_skip_reason(policy: TiqoraAiQueuePolicy | None) -> str | None:
    if policy is None or policy.valid_id != 1:
        return "no_policy"
    if not policy.enabled_triage:
        return "triage_disabled"
    if policy.service_user_id is None:
        return "no_service_user"
    if policy.triage_llm_provider_id is None and policy.llm_provider_id is None:
        return "no_provider"
    return None


async def _build_pii_mapper(
    session: AsyncSession, policy: TiqoraAiQueuePolicy, ticket: Any
) -> PiiMapper | None:
    """Masking for the subject/body only. Returns ``None`` when the queue has
    masking off, in which case the raw text goes to the model.

    spaCy NER is deliberately *not* run here even when ``pii_ner_enabled``
    is on: triage fires on every new ticket, NER is the most expensive part
    of the reply path, and a routing decision does not need person names —
    the From-header/customer_user candidates from ``collect_known_names``
    cover the same ground for a fraction of the cost.
    """
    if not policy.pii_masking:
        return None
    articles = await load_articles(session, ticket.ticket_id)
    known_names = await collect_known_names(session, ticket, articles)
    never_mask = pii_never_mask(ticket)
    return PiiMapper(never_mask=never_mask, known_names=known_names)


async def _process_event(session: AsyncSession, settings: Settings, event: OutboxEvent) -> str:
    """Handle one ``ArticleCreate`` event. Returns a short outcome label."""
    article_id = event.payload.get("article_id")
    if article_id is None:
        return "no_article_id"
    if event.payload.get("auto_generated"):
        return "auto_generated"
    if await article_sender_type(session, int(article_id)) != "customer":
        return "not_customer"

    ticket_id = event.ticket_id
    try:
        ticket = await ticket_snapshot(session, ticket_id)
    except TicketNotFoundError:
        return "ticket_gone"

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    policy_reason = _policy_skip_reason(policy)
    if policy_reason is not None:
        return policy_reason
    assert policy is not None  # narrowed by _policy_skip_reason

    reason = await _skip_reason(session, ticket_id=ticket_id, article_id=int(article_id))
    if reason is not None:
        logger.info("ai_triage_skip", ticket_id=ticket_id, reason=reason)
        return reason

    articles = await load_articles(session, ticket_id)
    article = next((a for a in articles if a.id == int(article_id)), None)
    if article is None:
        return "article_gone"

    ignored = parse_str_list(policy.ignored_senders)
    if ignored and matches_ignored(article.from_address, ignored):
        logger.info("ai_triage_sender_skip", ticket_id=ticket_id)
        return "ignored_sender"

    # _policy_skip_reason already rejected "neither provider set"; this
    # re-states it for the type checker and guards against the row changing
    # between the two reads.
    effective_provider_id = policy.triage_llm_provider_id or policy.llm_provider_id
    if effective_provider_id is None:
        return "no_provider"
    window = await usage_service.provider_budget_exceeded(session, effective_provider_id)
    if window is not None:
        return f"provider_budget_{window}"

    candidates = await triage_service.load_candidates(session, policy)
    if not candidates:
        return "no_candidates"

    run_id = uuid.uuid4().hex
    raw_llm = await build_llm_client(
        session,
        settings,
        effective_provider_id,
        policy.triage_model_override or policy.model_override,
        policy.llm_fallback_json,
    )
    pii = await _build_pii_mapper(session, policy, ticket)
    llm = AuditingLlmClient(
        raw_llm,
        settings=settings,
        context=AuditContext(
            feature=FEATURE_TRIAGE,
            run_id=run_id,
            ticket_id=ticket_id,
            queue_id=ticket.queue_id,
            trigger="auto",
        ),
        session=session,
        pii_mapper=pii,
    )

    decision = await triage_service.decide(
        session,
        llm=llm,
        raw_llm=raw_llm,
        policy=policy,
        ticket=ticket,
        article=article,
        candidates=candidates,
        run_id=run_id,
        mask=pii.mask if pii is not None else None,
    )

    outcome = await _persist_and_apply(
        session,
        settings=settings,
        policy=policy,
        ticket=ticket,
        article_id=int(article_id),
        decision=decision,
    )

    await usage_service.record_usage(
        session,
        queue_id=ticket.queue_id,
        ticket_id=ticket_id,
        feature=FEATURE_TRIAGE,
        provider_id=decision.provider_id or effective_provider_id,
        model=decision.model,
        prompt_tokens=decision.prompt_tokens,
        completion_tokens=decision.completion_tokens,
        success=decision.error is None,
        error=decision.error,
        extra_json=json.dumps({"samples": policy.triage_samples, "outcome": outcome}),
    )
    return outcome


async def _persist_and_apply(
    session: AsyncSession,
    *,
    settings: Settings,
    policy: TiqoraAiQueuePolicy,
    ticket: Any,
    article_id: int,
    decision: triage_service.TriageDecision,
) -> str:
    """Write the triage row and, above the auto threshold, apply it."""
    queue = decision.queue
    customer = decision.customer

    if decision.error is not None:
        status = TRIAGE_STATUS_ERROR
    elif queue.queue_id is not None and queue.confidence >= policy.triage_suggest_threshold:
        status = TRIAGE_STATUS_OPEN
    elif customer.login is not None:
        # No queue proposal worth showing, but a real forwarded-sender fix is
        # still actionable on its own.
        status = TRIAGE_STATUS_OPEN
    else:
        status = TRIAGE_STATUS_NO_ACTION

    row = TiqoraAiTriage(
        ticket_id=ticket.ticket_id,
        article_id=article_id,
        source_queue_id=ticket.queue_id,
        status=status,
        suggested_queue_id=queue.queue_id,
        queue_confidence=queue.confidence,
        queue_reason=queue.reason or None,
        candidates_json=json.dumps(queue.candidates) if queue.candidates else None,
        extracted_email=customer.email,
        suggested_customer_user_id=customer.login,
        suggested_customer_id=customer.customer_id or None,
        customer_confidence=customer.confidence,
        customer_source=customer.source,
        run_id=decision.run_id,
        error=decision.error,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # UNIQUE(ticket_id): another worker instance got here first.
        await session.rollback()
        logger.info("ai_triage_race_lost", ticket_id=ticket.ticket_id)
        return "already_triaged"

    apply_queue = (
        queue.queue_id is not None
        and decision.error is None
        and queue.confidence >= policy.triage_auto_threshold
    )
    apply_customer = (
        policy.triage_customer_fix_enabled
        and customer.login is not None
        and customer.confidence >= policy.triage_customer_fix_auto_threshold
    )

    if apply_queue or apply_customer:
        applied = await triage_service.apply_decision(
            session,
            sysconfig=SysConfig(session),
            row=row,
            acting_user_id=int(policy.service_user_id or 0),
            apply_queue=apply_queue,
            apply_customer=apply_customer,
        )
        if applied:
            row.status = TRIAGE_STATUS_APPLIED
            if "queue" in applied and row.suggested_queue_id is not None:
                await _maybe_delay_reply(
                    session,
                    ticket_id=ticket.ticket_id,
                    queue_id=row.suggested_queue_id,
                    article_id=article_id,
                )
    await session.commit()
    return row.status


async def _maybe_delay_reply(
    session: AsyncSession, *, ticket_id: int, queue_id: int, article_id: int
) -> None:
    """Honour the *destination* queue's ``triage_delay_reply``.

    Setting ``last_customer_article_id`` is exactly the reply path's loop
    guard, so the moved ticket's first message gets no AI answer and the
    agent waits for the customer's next one. Off by default: with it on the
    customer's opening mail is never answered automatically.
    """
    destination = await get_queue_policy_by_queue(session, queue_id)
    if destination is None or not destination.triage_delay_reply:
        return
    state = await get_or_create_state(session, ticket_id)
    if state.last_customer_article_id is None or state.last_customer_article_id < article_id:
        state.last_customer_article_id = article_id
        logger.info("ai_triage_reply_delayed", ticket_id=ticket_id, queue_id=queue_id)


async def run_triage_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """One triage pass over the outbox. Keys are prefixed ``triage_`` so the
    caller can merge them flatly into the AI worker's tick status."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()

    async with factory() as session:
        raw = await get_setting(session, KEY_AI_TRIAGE_WATERMARK)
        if raw is None or not raw.strip():
            start = await max_outbox_id(session)
            await set_setting(session, KEY_AI_TRIAGE_WATERMARK, str(start))
            logger.info("ai_triage_watermark_seeded", watermark=start)
            return {"triage_seeded": start, "triage_events": 0, "triage_applied": 0}
        watermark = int(raw.strip())
        primary = await is_tiqora_primary(session)
        batch = await next_outbox_batch(session, watermark, OUTBOX_BATCH_SIZE)

    totals = {
        "triage_events": 0,
        "triage_applied": 0,
        "triage_suggested": 0,
        "triage_errors": 0,
        "triage_enabled": int(primary),
    }
    last_id = watermark

    for event in batch:
        last_id = event.id
        totals["triage_events"] += 1
        if event.event_type != "ArticleCreate":
            continue
        # Gated: the batch is still drained (watermark below) so cutting
        # over to tiqora_primary never triages a backlog.
        if not primary:
            continue
        try:
            async with factory() as session:
                outcome = await _process_event(session, cfg, event)
            if outcome == TRIAGE_STATUS_APPLIED:
                totals["triage_applied"] += 1
            elif outcome == TRIAGE_STATUS_OPEN:
                totals["triage_suggested"] += 1
            elif outcome == TRIAGE_STATUS_ERROR:
                totals["triage_errors"] += 1
        except Exception:  # noqa: BLE001 — one broken event must not stop the batch
            logger.exception("ai_triage_event_failed", event_id=event.id, ticket_id=event.ticket_id)
            totals["triage_errors"] += 1

    if last_id != watermark:
        async with factory() as session:
            await set_setting(session, KEY_AI_TRIAGE_WATERMARK, str(last_id))

    logger.info("ai_triage_tick", **totals)
    return totals


__all__ = ["MAX_TICKET_AGE", "run_triage_tick"]
