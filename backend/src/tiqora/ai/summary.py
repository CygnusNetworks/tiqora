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
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import usage as usage_service
from tiqora.ai.acl import AclLimitExceededError as AiAclLimitExceededError
from tiqora.ai.acl import check_feature_access, check_feature_limits
from tiqora.ai.attachment_context import build_attachment_context, mask_attachment_block
from tiqora.ai.audit import FEATURE_SUMMARY as AUDIT_FEATURE_SUMMARY
from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.context import (
    ArticleSnapshot,
    TicketNotFoundError,
    collect_known_names,
    get_or_create_state,
    load_articles,
    ner_source_texts,
    render_ticket_header,
    ticket_snapshot,
)
from tiqora.ai.kb_wiring import build_vision_llm_factory
from tiqora.ai.llm import LlmClient, LlmMessage
from tiqora.ai.models import (
    DETAIL_DETAILED,
    FEATURE_SUMMARY,
    TiqoraAiQueuePolicy,
    TiqoraAiTicketState,
)
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.config import Settings, get_settings

logger = structlog.get_logger(__name__)

TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"

STATUS_UPDATED = "updated"
STATUS_UP_TO_DATE = "up_to_date"
STATUS_SKIPPED = "skipped"

_SYSTEM_PROMPT = (
    "You maintain a concise, agent-facing internal summary of a support "
    "ticket. Given the previous summary (if any) and the article(s) since "
    "then, write an updated summary as plain text, structured into short "
    "paragraphs separated by a blank line, in this order: (1) current "
    "status, (2) key facts/identifiers, (3) what the customer wants, (4) "
    "what has already been tried, (5) open action items. Do not use "
    "headings, bullet points, or markdown formatting. Scale the "
    "conversation part with the length of the actual message text: for a "
    "short mail thread, collapse these points into 2-4 sentences total and "
    "silently skip points with nothing to say; only a genuinely long "
    "conversation justifies full paragraphs.\n\n"
    "Attached documents are usually the substantive content of the "
    "request - the mail text often just transmits them. If the input "
    "contains one or more attachment blocks labeled "
    "'[Anhang: <filename> — ca. <n> Zeichen]' with extracted document "
    "text, you MUST add a paragraph starting with 'Dokumente:' - never "
    "omit it, even when the rest of the summary is short. For each "
    "document: the filename, then a substantial summary of its content - "
    "1-2 sentences for small documents (under ~2000 characters), at least "
    "4-6 sentences covering the key contents for larger ones. If the "
    "previous summary already has a 'Dokumente:' paragraph and no new "
    "attachment blocks are present, carry that paragraph over unchanged - "
    "documents never disappear from the summary. Omit the paragraph only "
    "when neither the input nor the previous summary mentions any "
    "document.\n\n"
    "The input starts with a ticket metadata header (ticket number, queue, "
    "state, customer). That header is context only - the agent reading the "
    "summary already sees all of it in the UI. Never restate state, queue "
    "name, ticket number, customer address, or submission metadata in the "
    "summary; write 'der Kunde'/'the customer' instead of the address, and "
    "mention an identifier only when it is itself what the conversation is "
    "about.\n\n"
    "Output ONLY the updated summary text - no preamble, no meta-commentary "
    "about what you changed."
)

# "detailed" — same structure as _SYSTEM_PROMPT; the switch primarily deepens
# the DOCUMENT summaries. The conversation part still scales with the mail
# text (a short mail stays a short summary even in detailed mode).
_SYSTEM_PROMPT_DETAILED = (
    "You maintain a thorough, agent-facing internal summary of a support "
    "ticket. Given the previous summary (if any) and the article(s) since "
    "then, write an updated summary as plain text, structured into "
    "paragraphs separated by a blank line, in this order: (1) current "
    "status, (2) key facts/identifiers, (3) what the customer wants, (4) "
    "what has already been tried, (5) open action items. Do not use "
    "headings, bullet points, or markdown formatting. Even in this "
    "thorough mode, scale the conversation part with the length of the "
    "actual message text: a short mail thread still collapses into 2-4 "
    "sentences total (silently skipping points with nothing to say) - only "
    "a genuinely long conversation justifies full, detailed paragraphs.\n\n"
    "Attached documents are usually the substantive content of the "
    "request, and this thorough mode is primarily about them. If the input "
    "contains one or more attachment blocks labeled "
    "'[Anhang: <filename> — ca. <n> Zeichen]' with extracted document "
    "text, you MUST add a paragraph starting with 'Dokumente:' - never "
    "omit it, even when the rest of the summary is short. For each "
    "document: the filename, then a thorough summary of its key contents "
    "including concrete data, decisions, names of items, and figures - up "
    "to a full paragraph per document for larger ones (over ~2000 "
    "characters), 2-3 sentences for small ones. If the previous summary "
    "already has a 'Dokumente:' paragraph and no new attachment blocks are "
    "present, carry that paragraph over unchanged - documents never "
    "disappear from the summary. Omit the paragraph only when neither the "
    "input nor the previous summary mentions any document.\n\n"
    "The input starts with a ticket metadata header (ticket number, queue, "
    "state, customer). That header is context only - the agent reading the "
    "summary already sees all of it in the UI. Never restate state, queue "
    "name, ticket number, customer address, or submission metadata in the "
    "summary; write 'der Kunde'/'the customer' instead of the address, and "
    "mention an identifier only when it is itself what the conversation is "
    "about.\n\n"
    "Output ONLY the updated summary text - no preamble, no meta-commentary "
    "about what you changed."
)


def _system_prompt_for_detail(detail: str) -> str:
    return _SYSTEM_PROMPT_DETAILED if detail == DETAIL_DETAILED else _SYSTEM_PROMPT


_ATTACH_SIZE_RE = re.compile(r"^\[Anhang: (.+?) — ca\. (\d+) Zeichen\]", re.MULTILINE)


def _collect_docs(
    rendered_articles: list[ArticleSnapshot],
    attachment_blocks: dict[int, str],
) -> list[tuple[str, int]]:
    """The (filename, char-count) pairs of every attachment block that will be
    summarized this run — shared by the length guidance and the completion
    budget so both see the same set of documents."""
    docs: list[tuple[str, int]] = []
    for a in rendered_articles:
        block = attachment_blocks.get(a.id)
        if block:
            docs.extend((m.group(1), int(m.group(2))) for m in _ATTACH_SIZE_RE.finditer(block))
    return docs


def _completion_budget(
    docs: list[tuple[str, int]],
    *,
    detail: str,
) -> int:
    """max_tokens for the summary completion.

    The LLM client defaults to 1024 (:class:`tiqora.ai.llm.LlmClient`), which
    truncated real detailed summaries mid-sentence once several documents each
    demanded their own multi-sentence 'Dokumente:' entry (run
    172949646fd8492a9d85ebf64d3dfa4d). We ask the model for that depth, so we
    must also grant the tokens to produce it: a base budget for the
    conversation part plus a per-document allowance, deeper in detailed mode.
    """
    detailed = detail == DETAIL_DETAILED
    base = 1500 if detailed else 1024
    per_doc = 700 if detailed else 300
    ceiling = 8000 if detailed else 4000
    return min(base + per_doc * len(docs), ceiling)


def _length_guidance(
    rendered_articles: list[ArticleSnapshot],
    attachment_blocks: dict[int, str],
    *,
    detail: str,
) -> str:
    """Concrete, computed length instructions appended to the user message.

    The generic prose in the system prompt is not enough for mid-size models
    (observed: short mails summarized at length while large documents got one
    sentence, runs 13563f69/49a074d1) — spelling out the actual numbers per
    run steers them far more reliably.
    """
    mail_chars = sum(len(a.body or "") for a in rendered_articles)
    docs = _collect_docs(rendered_articles, attachment_blocks)

    lines = ["--- length guidance (computed for this run) ---"]
    if mail_chars < 1500:
        lines.append(
            f"The mail text is short (~{mail_chars} characters): cover the "
            "conversation in AT MOST 3 sentences total."
        )
    elif mail_chars < 6000:
        lines.append(
            f"The mail text is moderate (~{mail_chars} characters): cover the "
            "conversation in at most 5 sentences total."
        )
    else:
        lines.append(
            f"The mail text is long (~{mail_chars} characters): use as many "
            "paragraphs for the conversation as it genuinely needs."
        )
    if docs:
        detailed = detail == DETAIL_DETAILED
        if detailed:
            lines.append(
                "The documents are the main content of this ticket - dedicate "
                "MOST of your output to the 'Dokumente:' paragraph."
            )
        for filename, size in docs:
            if size < 2000:
                depth = "1-2 sentences"
            elif detailed:
                depth = "a full paragraph of at least 6-10 sentences with concrete contents"
            else:
                depth = "4-6 sentences"
            lines.append(f"Document '{filename}' (~{size} characters): summarize it in {depth}.")
    return "\n".join(lines)


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
        subject_line = f"Subject: {a.subject}" if a.subject else None
        if mask:
            body = pii.mask(body)
            if subject_line:
                subject_line = pii.mask(subject_line)
        attach_text = (attachment_blocks or {}).get(a.id)
        if attach_text:
            # Masked separately so the "[Anhang: …]" label lines the system
            # prompt keys on stay intact (see mask_attachment_block).
            if mask:
                attach_text = mask_attachment_block(pii, attach_text)
            body = f"{body}\n\n{attach_text}" if body else attach_text
        lines.append(f"--- article {a.id} [{_label(a)}] ---")
        if subject_line:
            lines.append(subject_line)
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
    run_id: str | None = None,
    detail: str | None = None,
) -> SummaryResult:
    """Run the summary service once for one ticket (plan §3.5).

    ``detail`` (``"standard"``/``"detailed"``) overrides the queue policy's
    ``summary_detail`` for this run — the agent picks the scope per ticket.

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

    mask = bool(policy.pii_masking)

    audit_context = AuditContext(
        feature=AUDIT_FEATURE_SUMMARY,
        run_id=run_id or uuid.uuid4().hex,
        ticket_id=ticket_id,
        queue_id=ticket.queue_id,
        acting_user_id=acting_user_id,
        trigger=trigger,
        provider_id=policy.llm_provider_id,
        model=policy.model_override,
    )

    effective_vision_factory = vision_llm_factory
    if effective_vision_factory is None and policy.vision_provider_id is not None:
        effective_settings = settings or get_settings()
        effective_vision_factory = await build_vision_llm_factory(
            session, effective_settings, policy.vision_provider_id, audit=audit_context
        )
    attachment_context = await build_attachment_context(
        session,
        articles,
        vision_enabled=policy.vision_provider_id is not None,
        vision_llm_factory=effective_vision_factory,
    )
    attachment_blocks = attachment_context.blocks

    never_mask = {v for v in (ticket.customer_id, ticket.customer_user_id) if v}
    ner_texts = (
        ner_source_texts(articles, attachment_blocks)
        if policy.pii_masking and policy.pii_ner_enabled
        else None
    )
    known_names = await collect_known_names(session, ticket, articles, extra_texts=ner_texts)
    pii = PiiMapper(never_mask=never_mask or None, known_names=known_names or None)

    llm = AuditingLlmClient(
        llm,
        settings=settings or get_settings(),
        context=audit_context,
        session=session,
        pii_mapper=pii,
    )

    header = render_ticket_header(ticket)
    effective_detail = detail or policy.summary_detail
    has_previous = state.summary_body is not None and state.last_summary_upto_article_id is not None
    if has_previous:
        rendered_articles = new_articles
        user_message = (
            f"{header}\n\n"
            f"--- previous summary ---\n{state.summary_body}\n\n"
            "--- new articles since then ---\n"
            + _render_articles(
                new_articles, pii=pii, mask=mask, attachment_blocks=attachment_blocks
            )
        )
    else:
        rendered_articles = articles
        user_message = (
            f"{header}\n\n"
            "--- full conversation ---\n"
            f"{_render_articles(articles, pii=pii, mask=mask, attachment_blocks=attachment_blocks)}"
        )
    user_message += "\n\n" + _length_guidance(
        rendered_articles, attachment_blocks, detail=effective_detail
    )

    system_prompt = _system_prompt_for_detail(effective_detail)
    messages = [
        LlmMessage(role="system", content=system_prompt),
        LlmMessage(role="user", content=user_message),
    ]
    docs = _collect_docs(rendered_articles, attachment_blocks)
    response = await llm.chat(
        messages=messages,
        tools=None,
        max_tokens=_completion_budget(docs, detail=effective_detail),
    )

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
    state.summary_created_at = datetime.now(UTC).replace(tzinfo=None)
    state.last_run_at = state.summary_created_at
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


async def delete_summary(session: AsyncSession, ticket_id: int) -> bool:
    """Drop a ticket's stored summary (state columns only — summaries are
    never articles, see module docstring). Returns ``False`` when the ticket
    has no stored summary."""
    state = await session.get(TiqoraAiTicketState, ticket_id)
    if state is None or state.summary_body is None:
        return False
    state.summary_body = None
    state.last_summary_upto_article_id = None
    state.last_summary_hash = None
    state.summary_created_at = None
    await session.commit()
    return True


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
    "delete_summary",
    "summarize_ticket",
]
