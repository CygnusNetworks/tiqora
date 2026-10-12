"""Shared pieces of an automatic AI run that more than one caller needs.

The auto worker (e-mail/Telegram outbox) and the web-chat bot both decide whether a queue may
still answer on its own. The queue-level caps live here; both callers import this module and
it imports neither of them (dependencies point one way).

- ``global_replies_per_hour_cap`` / ``auto_replies_last_hour`` / ``tokens_used_today`` are the
  counters behind the hourly, global and daily-budget limits.
- ``auto_reply_queue_cap_reason`` is the part of the cap check that needs no ticket, so a
  chat can ask it before its ticket exists.
- ``auto_reply_cap_reason`` is the full per-ticket check the auto worker runs.
- ``prepare_auto_run`` resolves the agent chain and the knowledge-base wiring for a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.customer_pause import SKIP_REASON as CUSTOMER_PAUSE_SKIP_REASON
from tiqora.ai.customer_pause import active_pause_for_ticket
from tiqora.ai.kb_wiring import kb_bundle, kb_get_article_fn, kb_search_fn
from tiqora.ai.limits import queue_tokens_used_today
from tiqora.ai.llm import LlmClient
from tiqora.ai.llm_routing import TASK_AGENT, ChainModel, build_task_llm
from tiqora.ai.models import FEATURE_AUTO_REPLY, TiqoraAiQueuePolicy, TiqoraAiUsage
from tiqora.config import Settings
from tiqora.domain.settings_store import KEY_AI_GLOBAL_REPLIES_PER_HOUR, get_setting

#: Caps that end automation for one ticket for good (unlike rate limits and budgets, which lift
#: again on their own) -- hitting one is a handoff.
TICKET_CAP_REASONS: Final = frozenset({"max_clarifications", "max_auto_replies", "grant_used"})
#: The queue-level limits :func:`auto_reply_queue_cap_reason` can return.
QUEUE_CAP_REASONS: Final = frozenset({"queue_rate_limit", "global_rate_limit", "budget_tokens_day"})


class NoAgentProfile(Exception):  # noqa: N818 -- name fixed by the plan
    """The queue policy resolves no agent LLM profile at all (not even an unusable one)."""


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


async def auto_reply_cap_reason(
    session: AsyncSession, *, ticket_id: int, queue_id: int, policy: TiqoraAiQueuePolicy, state: Any
) -> str | None:
    """Return a short reason string if anything blocks this run, else None."""
    # A handoff has to actually hand off. `escalate_to_human` sets
    # `ai_escalated_at` and writes an internal note, but until this check
    # existed nothing consumed the flag: the next customer message started a
    # fresh run, and under `full` autonomy that answer went straight back to
    # the customer -- right after the AI had said a human would take over.
    # The flag stays until someone switches the autopilot back on (or the
    # ticket is closed/merged/removed, see domain.ticket_write_service); an
    # agent's reply does not lift it.
    # Manual Assist is unaffected: it never runs through this worker.
    if getattr(state, "ai_escalated_at", None) is not None:
        return "escalated_to_human"
    # An agent paused all automatic AI actions for this ticket. Unlike the
    # handoff flag above, nothing clears this on its own.
    if getattr(state, "ai_paused_at", None) is not None:
        return "ai_paused"
    # The same for the customer: an agent switched the automation off for
    # this customer user / company (indefinitely or until a date).
    if await active_pause_for_ticket(session, ticket_id) is not None:
        return CUSTOMER_PAUSE_SKIP_REASON
    # A release an agent granted by hand replaces the per-ticket caps until
    # it is used up; rate limits and budgets below still apply.
    grant = getattr(state, "ai_grant_remaining", None)
    if grant is not None:
        if grant <= 0:
            return "grant_used"
    elif state.auto_reply_count >= policy.max_auto_replies:
        return "max_auto_replies"
    elif state.clarification_count >= policy.max_clarifications:
        return "max_clarifications"
    return await auto_reply_queue_cap_reason(session, queue_id=queue_id, policy=policy)


@dataclass(frozen=True, slots=True)
class AutoRunInputs:
    """What ``run_ticket_agent`` needs besides the ticket: the LLM chain and the KB wiring.

    The KB callables are bound to the session they were prepared on, so the caller has to
    run the agent on that same session.
    """

    llm: LlmClient
    llm_models: list[ChainModel]
    kb_bundle: str | None
    kb_search_fn: Any
    kb_get_article_fn: Any

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "llm": self.llm,
            "llm_models": self.llm_models,
            "kb_bundle": self.kb_bundle,
            "kb_search_fn": self.kb_search_fn,
            "kb_get_article_fn": self.kb_get_article_fn,
        }


async def prepare_auto_run(
    session: AsyncSession, settings: Settings, policy: TiqoraAiQueuePolicy
) -> AutoRunInputs:
    """Resolve the agent chain (also the provider-budget pre-check) and the KB wiring.

    Raises :class:`tiqora.ai.llm_routing.NoUsableModel` when every chain entry is skipped and
    :class:`NoAgentProfile` when the policy has no agent profile.
    """
    task_llm = await build_task_llm(session, settings, policy, TASK_AGENT)
    if task_llm is None:
        raise NoAgentProfile(TASK_AGENT)
    service_user_id = policy.service_user_id
    assert service_user_id is not None  # the caller gates on a service user
    return AutoRunInputs(
        llm=task_llm.client,
        llm_models=task_llm.models,
        kb_bundle=await kb_bundle(session, settings, service_user_id, policy),
        kb_search_fn=kb_search_fn(session, settings, service_user_id),
        kb_get_article_fn=kb_get_article_fn(session, settings, service_user_id),
    )


__all__ = [
    "QUEUE_CAP_REASONS",
    "TICKET_CAP_REASONS",
    "AutoRunInputs",
    "NoAgentProfile",
    "auto_replies_last_hour",
    "auto_reply_cap_reason",
    "auto_reply_queue_cap_reason",
    "global_replies_per_hour_cap",
    "prepare_auto_run",
    "tokens_used_today",
]
