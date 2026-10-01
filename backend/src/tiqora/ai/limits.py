"""AI budget limits: what is spent against which cap, and who gets told.

Two kinds of cap exist, enforced in different places:

* ``queue_tokens_day`` — a queue policy's ``budget_tokens_day``, checked by
  the auto worker before every auto-reply (:mod:`tiqora.ai.auto_worker`).
  Counts the auto-reply *and* triage tokens of the queue since midnight UTC.
* ``provider_cost`` — a provider's ``budget_cost_day/week/month``, checked
  when a task's model chain is resolved (:mod:`tiqora.ai.llm_routing`); an
  exhausted provider is skipped for every feature.

Hitting either used to be visible only as a worker log line. This module
computes the current status for the admin UI and the unauthenticated
monitoring endpoint, and :func:`announce_exhausted_limits` tells admins once
per limit and window: an ``ai_limit_changed`` SSE message (the admin bell
re-reads the status) plus an ``AiLimitReached`` outbox row, which the outbox
drain fans out to webhooks like any ticket event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import (
    FEATURE_AUTO_REPLY,
    FEATURE_TRIAGE,
    TiqoraAiQueuePolicy,
    TiqoraAiUsage,
    TiqoraLlmProvider,
)
from tiqora.domain.settings_store import get_setting, set_setting

logger = structlog.get_logger(__name__)

KIND_QUEUE_TOKENS_DAY = "queue_tokens_day"
KIND_PROVIDER_COST = "provider_cost"

#: Outbox ``event_type`` of the webhook notification.
EVENT_AI_LIMIT_REACHED = "AiLimitReached"

#: ``tiqora_settings`` key prefix remembering the window an exhausted limit
#: was last announced for (value = window start, ISO).
_ANNOUNCED_KEY_PREFIX = "ai.limit_alert."

#: Features the queue token budget counts — see auto_worker._cap_reason.
QUEUE_BUDGET_FEATURES = (FEATURE_AUTO_REPLY, FEATURE_TRIAGE)


@dataclass(frozen=True, slots=True)
class LimitStatus:
    kind: str
    subject_id: int
    subject_name: str
    window: str  # "day" | "week" | "month"
    used: float
    limit: float
    window_start: datetime
    resets_at: datetime
    currency: str | None = None

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    @property
    def key(self) -> str:
        return f"{self.kind}.{self.subject_id}.{self.window}"


@dataclass(frozen=True, slots=True)
class _Window:
    name: str
    start: datetime
    end: datetime


def _windows(now: datetime) -> dict[str, _Window]:
    """Calendar windows in naive UTC, as the enforcement code counts them."""
    day = datetime(now.year, now.month, now.day)
    week = day - timedelta(days=day.weekday())
    month = datetime(now.year, now.month, 1)
    next_month = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)
    return {
        "day": _Window("day", day, day + timedelta(days=1)),
        "week": _Window("week", week, week + timedelta(days=7)),
        "month": _Window("month", month, next_month),
    }


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC).replace(tzinfo=None)


async def queue_tokens_used_today(
    session: AsyncSession, queue_id: int, *, now: datetime | None = None
) -> int:
    """Tokens this queue has spent today against ``budget_tokens_day``.

    Triage is counted even though it is not an auto-reply: it runs from the
    same worker, on the same queue's policy and provider, and costs
    ``triage_samples`` calls per new ticket. Filtering on auto-reply alone
    would let a queue quietly spend several times its daily budget.
    """
    day_start = _windows(_now(now))["day"].start
    total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(TiqoraAiUsage.prompt_tokens + TiqoraAiUsage.completion_tokens), 0
                )
            ).where(
                TiqoraAiUsage.feature.in_(QUEUE_BUDGET_FEATURES),
                TiqoraAiUsage.queue_id == queue_id,
                TiqoraAiUsage.ts >= day_start,
            )
        )
    ).scalar_one()
    return int(total)


async def _queue_limits(session: AsyncSession, now: datetime) -> list[LimitStatus]:
    policies = (
        (
            await session.execute(
                select(TiqoraAiQueuePolicy).where(
                    TiqoraAiQueuePolicy.valid_id == 1,
                    TiqoraAiQueuePolicy.budget_tokens_day.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not policies:
        return []
    day = _windows(now)["day"]
    queue_ids = [p.queue_id for p in policies]
    used_rows = (
        await session.execute(
            select(
                TiqoraAiUsage.queue_id,
                func.sum(TiqoraAiUsage.prompt_tokens + TiqoraAiUsage.completion_tokens),
            )
            .where(
                TiqoraAiUsage.feature.in_(QUEUE_BUDGET_FEATURES),
                TiqoraAiUsage.queue_id.in_(queue_ids),
                TiqoraAiUsage.ts >= day.start,
            )
            .group_by(TiqoraAiUsage.queue_id)
        )
    ).all()
    used = {int(qid): int(total or 0) for qid, total in used_rows}
    names = await _queue_names(session, queue_ids)
    return [
        LimitStatus(
            kind=KIND_QUEUE_TOKENS_DAY,
            subject_id=p.queue_id,
            subject_name=names.get(p.queue_id, str(p.queue_id)),
            window="day",
            used=float(used.get(p.queue_id, 0)),
            limit=float(p.budget_tokens_day or 0),
            window_start=day.start,
            resets_at=day.end,
        )
        for p in policies
    ]


async def _queue_names(session: AsyncSession, queue_ids: list[int]) -> dict[int, str]:
    if not queue_ids:
        return {}
    params = {f"q{i}": qid for i, qid in enumerate(queue_ids)}
    placeholders = ", ".join(f":{k}" for k in params)
    rows = await session.execute(
        text(f"SELECT id, name FROM queue WHERE id IN ({placeholders})"),  # noqa: S608 — bound params only
        params,
    )
    return {int(r[0]): str(r[1]) for r in rows.all()}


async def _provider_limits(session: AsyncSession, now: datetime) -> list[LimitStatus]:
    providers = (
        (await session.execute(select(TiqoraLlmProvider).where(TiqoraLlmProvider.valid_id == 1)))
        .scalars()
        .all()
    )
    out: list[LimitStatus] = []
    for window in _windows(now).values():
        capped = [p for p in providers if getattr(p, f"budget_cost_{window.name}") is not None]
        if not capped:
            continue
        spent_rows = (
            await session.execute(
                select(TiqoraAiUsage.provider_id, func.sum(TiqoraAiUsage.cost_hint))
                .where(
                    TiqoraAiUsage.provider_id.in_([p.id for p in capped]),
                    TiqoraAiUsage.ts >= window.start,
                )
                .group_by(TiqoraAiUsage.provider_id)
            )
        ).all()
        spent = {int(pid): float(total or 0.0) for pid, total in spent_rows}
        out.extend(
            LimitStatus(
                kind=KIND_PROVIDER_COST,
                subject_id=p.id,
                subject_name=p.name,
                window=window.name,
                used=spent.get(p.id, 0.0),
                limit=float(getattr(p, f"budget_cost_{window.name}")),
                window_start=window.start,
                resets_at=window.end,
                currency=p.price_currency,
            )
            for p in capped
        )
    return out


async def collect_limits(
    session: AsyncSession, *, now: datetime | None = None
) -> list[LimitStatus]:
    """Every configured cap with its current spend (exhausted or not)."""
    current = _now(now)
    return [*await _queue_limits(session, current), *await _provider_limits(session, current)]


async def exhausted_limits(
    session: AsyncSession, *, now: datetime | None = None
) -> list[LimitStatus]:
    return [s for s in await collect_limits(session, now=now) if s.exhausted]


def limit_payload(status: LimitStatus) -> dict[str, object]:
    """JSON shape shared by the webhook payload and the API."""
    return {
        "kind": status.kind,
        "subject_id": status.subject_id,
        "subject_name": status.subject_name,
        "window": status.window,
        "used": status.used,
        "limit": status.limit,
        "currency": status.currency,
        "window_start": status.window_start.isoformat() + "Z",
        "resets_at": status.resets_at.isoformat() + "Z",
    }


async def announce_exhausted_limits(
    session: AsyncSession, *, ticket_id: int | None = None, now: datetime | None = None
) -> list[LimitStatus]:
    """Announce every exhausted limit not yet announced in its window.

    Called after each recorded LLM call (:func:`tiqora.ai.usage.record_usage`),
    i.e. right when a call pushes a cap over its limit. Per newly exhausted
    limit: one ``AiLimitReached`` outbox row (webhooks; ``ticket_id`` is the
    ticket whose call crossed the line, ``0`` when the call had none) and one
    ``ai_limit_changed`` SSE message for the admin bell. The window start is
    remembered in ``tiqora_settings`` so the same limit is announced again
    only in its next window. Returns the newly announced limits.
    """
    fresh: list[LimitStatus] = []
    for status in await exhausted_limits(session, now=now):
        key = _ANNOUNCED_KEY_PREFIX + status.key
        marker = status.window_start.isoformat()
        if await get_setting(session, key) == marker:
            continue
        payload = limit_payload(status)
        await session.execute(
            text(
                "INSERT INTO tiqora_event_outbox"
                " (event_type, ticket_id, payload, created, processed)"
                " VALUES (:event, :tid, :payload, current_timestamp, :processed)"
            ),
            {
                "event": EVENT_AI_LIMIT_REACHED,
                "tid": ticket_id or 0,
                "payload": json.dumps(payload),
                "processed": False,
            },
        )
        await set_setting(session, key, marker)  # commits the outbox row with it
        logger.warning("ai_limit_reached", **payload)
        fresh.append(status)
    if fresh:
        from tiqora.events.pubsub import get_pubsub_redis, publish_ai_limit_changed

        await publish_ai_limit_changed(get_pubsub_redis())
    return fresh


__all__ = [
    "EVENT_AI_LIMIT_REACHED",
    "KIND_PROVIDER_COST",
    "KIND_QUEUE_TOKENS_DAY",
    "QUEUE_BUDGET_FEATURES",
    "LimitStatus",
    "announce_exhausted_limits",
    "collect_limits",
    "exhausted_limits",
    "limit_payload",
    "queue_tokens_used_today",
]
