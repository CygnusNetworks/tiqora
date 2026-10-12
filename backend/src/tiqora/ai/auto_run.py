"""Shared pieces of an automatic AI run that more than one caller needs.

The auto worker (e-mail/Telegram outbox) and the web-chat bot both decide whether a queue may
still answer on its own. The queue-level caps live here; both callers import this module and
it imports neither of them (dependencies point one way).

- ``global_replies_per_hour_cap`` / ``auto_replies_last_hour`` / ``tokens_used_today`` are the
  counters behind the hourly, global and daily-budget limits.
- ``auto_reply_queue_cap_reason`` is the part of the cap check that needs no ticket, so a
  chat can ask it before its ticket exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.limits import queue_tokens_used_today
from tiqora.ai.models import FEATURE_AUTO_REPLY, TiqoraAiQueuePolicy, TiqoraAiUsage
from tiqora.domain.settings_store import KEY_AI_GLOBAL_REPLIES_PER_HOUR, get_setting


async def global_replies_per_hour_cap(session: AsyncSession) -> int | None:
    raw = await get_setting(session, KEY_AI_GLOBAL_REPLIES_PER_HOUR)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


async def auto_replies_last_hour(session: AsyncSession, *, queue_id: int | None) -> int:
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


async def tokens_used_today(session: AsyncSession, queue_id: int) -> int:
    """Tokens this queue has spent today against ``budget_tokens_day`` —
    auto-reply and triage (see :func:`tiqora.ai.limits.queue_tokens_used_today`).
    ``auto_replies_last_hour`` deliberately stays auto-reply-only — that one
    rate-limits *messages to customers*, which triage never sends.
    """
    return await queue_tokens_used_today(session, queue_id)


async def auto_reply_queue_cap_reason(
    session: AsyncSession, *, queue_id: int, policy: TiqoraAiQueuePolicy
) -> str | None:
    """The queue-level limits only: hourly, global, daily token budget (in that order).

    Per-ticket caps (escalated, paused, grants, reply counts) are not part of it — they need
    a ticket and stay with the caller.
    """
    if policy.max_replies_per_hour is not None:
        recent = await auto_replies_last_hour(session, queue_id=queue_id)
        if recent >= policy.max_replies_per_hour:
            return "queue_rate_limit"
    global_cap = await global_replies_per_hour_cap(session)
    if global_cap is not None:
        recent_global = await auto_replies_last_hour(session, queue_id=None)
        if recent_global >= global_cap:
            return "global_rate_limit"
    if policy.budget_tokens_day is not None:
        tokens_today = await tokens_used_today(session, queue_id)
        if tokens_today >= policy.budget_tokens_day:
            return "budget_tokens_day"
    return None


__all__ = [
    "auto_replies_last_hour",
    "auto_reply_queue_cap_reason",
    "global_replies_per_hour_cap",
    "tokens_used_today",
]
