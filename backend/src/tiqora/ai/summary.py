"""SummaryService (plan §3.5) — state-only ticket summaries.

Canonical storage is **only** ``tiqora_ai_ticket_state.summary_body`` (+
``last_summary_upto_article_id`` / ``last_summary_hash``) — never an
internal note, never a second copy anywhere else (plan §3.5: "Summaries:
State-only"). The summary is a plain LLM completion (no tool loop): the
model is asked to produce updated summary text and nothing else.

AI-authored articles are labeled (not removed) in the rendered input,
reusing the same convention as :mod:`tiqora.ai.runtime` (via the shared
:mod:`tiqora.ai.context` loader) so the model can see its own previous
replies were already accounted for without re-summarizing them as "new
customer information".

Not gated by the Readiness-Gate (plan §3.0 v1.1 relaxation, Phase E):
summaries are state-only (``tiqora_ai_ticket_state.summary_body``), never an
article, and pull-based — they write nothing Sync-relevant, so both the
manual "Zusammenfassen" trigger and the worker's auto-summary scan run
regardless of ``system.operation_mode``. Note that the auto-summary scan
itself only ever sees tickets touched by the outbox batch in
:mod:`tiqora.ai.auto_worker`, which stays empty until mail ingestion moves to
Tiqora — so in practice auto-summary has no effect until then anyway.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import usage as usage_service
from tiqora.ai.acl import AclLimitExceededError as AiAclLimitExceededError
from tiqora.ai.acl import check_feature_access, check_feature_limits
from tiqora.ai.attachment_context import build_attachment_context
from tiqora.ai.context import (
    ArticleSnapshot,
    TicketNotFoundError,
    get_or_create_state,
    load_articles,
    ticket_snapshot,
)
from tiqora.ai.kb_wiring import build_vision_llm_factory
from tiqora.ai.llm import LlmClient, LlmMessage
from tiqora.ai.models import FEATURE_SUMMARY, TiqoraAiQueuePolicy
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.config import Settings

logger = structlog.get_logger(__name__)

TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"

STATUS_UPDATED = "updated"
STATUS_UP_TO_DATE = "up_to_date"
STATUS_SKIPPED = "skipped"

_SYSTEM_PROMPT = (
    "You maintain a concise, agent-facing internal summary of a support "
    "ticket. Given the previous summary (if any) and the article(s) since "
    "then, write an updated summary covering: current status, key facts/"
    "identifiers, what the customer wants, what has already been tried, and "
    "any open action items. Output ONLY the updated summary text - no "
    "preamble, no headings, no meta-commentary about what you changed."
)


class SummaryError(Exception):
    """Base class for summary-abort conditions (mirrors ``AgentRunError``)."""


class SummaryPolicyDisabledError(SummaryError):
    """The queue has no AI policy, or ``enabled_summary`` is off."""


class SummaryAclDeniedError(SummaryError):
    """The acting user is not allowed to use the ``summary`` feature."""


class SummaryAclLimitExceededError(SummaryError):
    """An ACL request/token limit (plan §3.6) is already reached."""


@dataclass(frozen=True, slots=True)
class SummaryResult:
    status: str
    summary_body: str | None = None
    upto_article_id: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _label(article: ArticleSnapshot) -> str:
    label = "agent" if article.sender_type == "agent" else article.sender_type
    if article.is_ai_origin:
        label += " (AI, previous own action)"
    return label


def _render_articles(
    articles: list[ArticleSnapshot],
    *,
    pii: PiiMapper,
    mask: bool,
    attachment_blocks: dict[int, str] | None = None,
) -> str:
    lines: list[str] = []
    for a in articles:
        body = a.body or ""
        attach_text = (attachment_blocks or {}).get(a.id)
        if attach_text:
            body = f"{body}\n\n{attach_text}" if body else attach_text
        if mask:
            body = pii.mask(body)
        lines.append(f"--- article {a.id} [{_label(a)}] ---")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def _new_articles(
    articles: list[ArticleSnapshot], *, since_article_id: int | None
) -> list[ArticleSnapshot]:
    if since_article_id is None:
        return articles
    return [a for a in articles if a.id > since_article_id]


def _is_incremental_no_op(
    new_articles: list[ArticleSnapshot],
    policy: TiqoraAiQueuePolicy,
    *,
    trigger: str,
) -> bool:
    """Plan §3.5 no-op rule: below the incremental threshold, auto-trigger
    skips; manual always proceeds as long as at least one new article
    exists (already guaranteed by the caller before this is checked)."""
    if trigger != TRIGGER_AUTO:
        return False
    # Own AI output is not "new customer information" for threshold purposes.
    countable = [a for a in new_articles if not a.is_ai_origin]
    min_articles = policy.summary_incremental_min_articles or 0
    min_chars = policy.summary_incremental_min_chars or 0
    if min_articles <= 0 and min_chars <= 0:
        return False
    total_chars = sum(len(a.body or "") for a in countable)
    return len(countable) < min_articles and total_chars < min_chars


async def summarize_ticket(
    session: AsyncSession,
    *,
    llm: LlmClient,
    ticket_id: int,
    trigger: str,
    acting_user_id: int | None,
    settings: Settings | None = None,
    vision_llm_factory: Any = None,
) -> SummaryResult:
    """Run the summary service once for one ticket (plan §3.5).

    ``acting_user_id`` is required for ``trigger="manual"`` (ACL check);
    ``None`` for ``trigger="auto"`` (queue-driven, no per-user ACL — mirrors
    the auto-reply path's "keine Kreuz-Anrechnung" rule, plan §3.6).

    ``settings``/``vision_llm_factory`` support the same attachment vision
    pre-pass as :func:`tiqora.ai.runtime.run_ticket_agent` (see
    :mod:`tiqora.ai.attachment_context`); ``settings`` defaults to
    :func:`tiqora.config.get_settings` when a ``vision_provider_id`` is
    configured and no factory was injected.

    Not gated by the Readiness-Gate (plan §3.0 v1.1 / Phase E) — see the
    module docstring.
    """
    try:
        ticket = await ticket_snapshot(session, ticket_id)
    except TicketNotFoundError as exc:
        raise SummaryError(str(exc)) from exc

    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or not policy.enabled_summary:
        raise SummaryPolicyDisabledError(f"Summary is disabled for queue {ticket.queue_id}")

    if trigger == TRIGGER_MANUAL:
        if acting_user_id is None:
            raise SummaryError("Manual summarize requires an acting user")
        if not await check_feature_access(session, acting_user_id, FEATURE_SUMMARY):
            raise SummaryAclDeniedError(
                f"User {acting_user_id} is not allowed to use {FEATURE_SUMMARY}"
            )
        try:
            await check_feature_limits(session, acting_user_id, FEATURE_SUMMARY)
        except AiAclLimitExceededError as exc:
            raise SummaryAclLimitExceededError(str(exc)) from exc

    state = await get_or_create_state(session, ticket_id)
    articles = await load_articles(session, ticket_id)

    new_articles = _new_articles(articles, since_article_id=state.last_summary_upto_article_id)
    if not new_articles:
        return SummaryResult(
            status=STATUS_UP_TO_DATE,
            summary_body=state.summary_body,
            upto_article_id=state.last_summary_upto_article_id,
        )

    if _is_incremental_no_op(new_articles, policy, trigger=trigger):
        return SummaryResult(
            status=STATUS_UP_TO_DATE,
            summary_body=state.summary_body,
            upto_article_id=state.last_summary_upto_article_id,
        )

    pii = PiiMapper(never_mask={ticket.customer_id} if ticket.customer_id else None)
    mask = bool(policy.pii_masking)

    effective_vision_factory = vision_llm_factory
    if effective_vision_factory is None and policy.vision_provider_id is not None:
        from tiqora.config import get_settings

        effective_settings = settings or get_settings()
        effective_vision_factory = await build_vision_llm_factory(
            session, effective_settings, policy.vision_provider_id
        )
    attachment_context = await build_attachment_context(
        session,
        articles,
        vision_enabled=policy.vision_provider_id is not None,
        vision_llm_factory=effective_vision_factory,
    )
    attachment_blocks = attachment_context.blocks

    has_previous = state.summary_body is not None and state.last_summary_upto_article_id is not None
    if has_previous:
        user_message = (
            f"Ticket #{ticket.ticket_id}: {ticket.title}\n\n"
            f"--- previous summary ---\n{state.summary_body}\n\n"
            "--- new articles since then ---\n"
            + _render_articles(
                new_articles, pii=pii, mask=mask, attachment_blocks=attachment_blocks
            )
        )
    else:
        user_message = (
            f"Ticket #{ticket.ticket_id}: {ticket.title}\n\n"
            "--- full conversation ---\n"
            f"{_render_articles(articles, pii=pii, mask=mask, attachment_blocks=attachment_blocks)}"
        )

    messages = [
        LlmMessage(role="system", content=_SYSTEM_PROMPT),
        LlmMessage(role="user", content=user_message),
    ]
    response = await llm.chat(messages=messages, tools=None)

    upto_article_id = new_articles[-1].id

    # Vision-pass usage is added to this run's usage record (see
    # tiqora.ai.attachment_context.AttachmentContextResult).
    prompt_tokens = attachment_context.vision_usage.prompt_tokens + response.usage.prompt_tokens
    completion_tokens = (
        attachment_context.vision_usage.completion_tokens + response.usage.completion_tokens
    )

    await usage_service.record_usage(
        session,
        user_id=acting_user_id if trigger == TRIGGER_MANUAL else None,
        queue_id=ticket.queue_id,
        ticket_id=ticket_id,
        feature=FEATURE_SUMMARY,
        provider_id=policy.llm_provider_id,
        model=policy.model_override,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        success=bool(response.content),
    )

    summary_text = (response.content or "").strip()
    if not summary_text:
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return SummaryResult(
            status=STATUS_SKIPPED,
            summary_body=state.summary_body,
            upto_article_id=state.last_summary_upto_article_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    if mask:
        summary_text = pii.unmask(summary_text)

    state.summary_body = summary_text
    state.last_summary_upto_article_id = upto_article_id
    state.last_summary_hash = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()
    state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()

    return SummaryResult(
        status=STATUS_UPDATED,
        summary_body=summary_text,
        upto_article_id=upto_article_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def auto_summary_due(session: AsyncSession, ticket_id: int) -> bool:
    """Plan §3.5 auto-trigger condition (worker-side, Phase D): article count
    or total content chars since the last summary exceed the queue's
    threshold. ``NULL`` threshold columns mean "no auto-summary" (plan §3.1:
    "NULL = kein Auto-Summary")."""
    try:
        ticket = await ticket_snapshot(session, ticket_id)
    except TicketNotFoundError:
        return False
    policy = await get_queue_policy_by_queue(session, ticket.queue_id)
    if policy is None or not policy.enabled_summary:
        return False
    if policy.summary_article_threshold is None and policy.summary_char_threshold is None:
        return False

    state = await get_or_create_state(session, ticket_id)
    articles = await load_articles(session, ticket_id)
    new_articles = _new_articles(articles, since_article_id=state.last_summary_upto_article_id)
    if not new_articles:
        return False

    countable = [a for a in new_articles if not a.is_ai_origin]
    article_threshold = policy.summary_article_threshold
    if article_threshold is not None and len(countable) >= article_threshold:
        return True
    if policy.summary_char_threshold is not None:
        total_chars = sum(len(a.body or "") for a in countable)
        if total_chars >= policy.summary_char_threshold:
            return True
    return False


__all__ = [
    "STATUS_SKIPPED",
    "STATUS_UP_TO_DATE",
    "STATUS_UPDATED",
    "TRIGGER_AUTO",
    "TRIGGER_MANUAL",
    "SummaryAclDeniedError",
    "SummaryAclLimitExceededError",
    "SummaryError",
    "SummaryPolicyDisabledError",
    "SummaryResult",
    "auto_summary_due",
    "summarize_ticket",
]
