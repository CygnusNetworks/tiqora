"""AgentRuntime — the per-ticket agent run (plan §3.4 steps 1-12).

Entry point: :func:`run_ticket_agent`. Phase B wires this up fully for the
**manual** trigger (Manual Assist, always the draft path — plan §3.4:
"Manual Assist ist immer Draft-Pfad"); the **auto** trigger's autonomy →
draft/send mapping is implemented and unit-tested here too (plan requires
the mapping logic to exist now), but nothing calls it with ``trigger="auto"``
until the outbox-driven worker lands in Phase D.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import drafts as draft_service
from tiqora.ai import usage as usage_service
from tiqora.ai.acl import AclLimitExceededError as AiAclLimitExceededError
from tiqora.ai.acl import check_feature_access, check_feature_limits
from tiqora.ai.attachment_context import build_attachment_context, mask_attachment_block
from tiqora.ai.audit import FEATURE_AUTO_REPLY as AUDIT_FEATURE_AUTO_REPLY
from tiqora.ai.audit import FEATURE_DRAFT as AUDIT_FEATURE_DRAFT
from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.capabilities import resolve_capabilities
from tiqora.ai.context import (
    ArticleSnapshot,
    TicketNotFoundError,
    TicketSnapshot,
    collect_known_names,
    get_or_create_state,
    latest_customer_article_id,
    load_articles,
    ner_source_texts,
    render_ticket_header,
    ticket_snapshot,
)
from tiqora.ai.gate import AiGateError, require_feature_allowed
from tiqora.ai.identity import (
    MAX_IDENTITY_ATTEMPTS,
    get_customer_id_for_login,
    is_identified,
    parse_clarify_schema,
    record_identity_attempt,
    verify_identity_claim,
)
from tiqora.ai.kb_wiring import build_vision_llm_factory
from tiqora.ai.listfields import parse_int_list
from tiqora.ai.llm import (
    LlmClient,
    LlmEmptyOutputError,
    LlmError,
    LlmMessage,
    LlmResponse,
    LlmUsage,
)
from tiqora.ai.models import (
    AUTONOMY_CLARIFY_ONLY,
    AUTONOMY_FULL,
    AUTONOMY_OFF,
    DRAFT_KIND_CLARIFY,
    DRAFT_KIND_REPLY,
    FEATURE_AUTO_REPLY,
    FEATURE_MANUAL_ASSIST,
    IDENTITY_CLARIFY_SCHEMA,
    REPLY_LANGUAGE_AUTO,
    REPLY_LANGUAGE_FIXED,
    SOURCE_AUTO,
    SOURCE_MANUAL,
    TiqoraAiArticleOrigin,
    TiqoraAiPromptPart,
    TiqoraAiQueuePolicy,
    TiqoraAiTicketState,
    TiqoraMcpClient,
    TiqoraMcpToolPolicy,
)
from tiqora.ai.output_guards import CustomerMessageGuardError, validate_customer_message
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue, load_prompt_parts
from tiqora.ai.prompt_safety import UNTRUSTED_CONTENT_SYSTEM_BLOCK
from tiqora.ai.reply_language import (
    LANGUAGE_PROFILES,
    detect_reply_language,
    detect_reply_language_detailed,
)
from tiqora.ai.tool_chain import analyze_tool_chain
from tiqora.ai.tools import (
    TOOL_ESCALATE_TO_HUMAN,
    TOOL_PROPOSE_CUSTOMER_MESSAGE,
    McpToolSpec,
    ToolArgumentError,
    ToolExecutor,
    ToolOutcome,
    ToolRegistry,
    UnknownToolError,
)
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.settings_store import (
    KEY_AI_DISCLOSURE_DEFAULT,
    KEY_AI_LLM_MAX_COMPLETION_TOKENS,
    get_setting,
    get_setting_int,
)
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.domain.ticket_write_service import set_customer as domain_set_customer
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_LOCK_MAX_AGE = timedelta(minutes=15)
DEFAULT_MAX_TOOL_ROUNDS = 8
# Fallback for KEY_AI_LLM_MAX_COMPLETION_TOKENS when the setting row is
# unset. Reasoning models need real headroom beyond the OpenAI wire default
# of 1024 — see settings_store.KEY_AI_LLM_MAX_COMPLETION_TOKENS.
DEFAULT_MAX_COMPLETION_TOKENS = 8192

# Reasoning models occasionally emit the finished customer reply as plain
# assistant text instead of calling the terminal propose_customer_message
# tool. Plain text has no delivery path, so the tool loop corrects the model
# at most this many times per run before giving up as "no proposal".
_MAX_PLAIN_TEXT_NUDGES = 2
_PLAIN_TEXT_NUDGE = (
    "Your previous message was plain text, which is never shown to the "
    "customer or the agent — it has no effect at all. You MUST finish with a "
    "tool call: if that text was your reply for the customer, call "
    "propose_customer_message with it (kind='reply'); if it was internal "
    "analysis, call add_internal_note; if you cannot help, call "
    "escalate_to_human. Do not answer in plain text again."
)
_TERMINAL_FORCE_PROMPT = (
    "Your research budget is exhausted — this is the final step. You MUST now "
    "call exactly one tool: propose_customer_message with your best answer or "
    "clarifying question based on everything gathered so far, or "
    "escalate_to_human if you genuinely cannot help. Do not answer in plain "
    "text."
)
_TELEGRAM_CHANNEL = "telegram"
_TYPING_INTERVAL_SECONDS = 4.0

TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"

STATUS_DRAFTED = "drafted"
STATUS_SENT = "sent"
STATUS_ESCALATED = "escalated"
STATUS_SUPERSEDED = "superseded"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"


class AgentRunError(Exception):
    """Base class for run-abort conditions (all mapped to a clear HTTP status
    by the caller — see ``tiqora.api.v1.ai``)."""


class LockHeldError(AgentRunError):
    """Another run currently holds the per-ticket lock (not yet expired)."""


class PolicyDisabledError(AgentRunError):
    """The queue has no AI policy, or the requested feature is disabled."""


class AclDeniedError(AgentRunError):
    """The acting user/subject is not allowed to use this feature."""


class AclLimitExceededError(AgentRunError):
    """An ACL request/token limit (plan §3.6) is already reached."""


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    status: str
    draft_id: int | None = None
    article_id: int | None = None
    notes: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _map_customer_message(*, trigger: str, autonomy: str, kind: str) -> str:
    """Plan §3.4 autonomy matrix. Returns ``"draft"`` or ``"send"``.

    Manual is *always* draft, regardless of queue autonomy — a human clicking
    "AI draft" must never trigger a customer-visible send.
    """
    if trigger == TRIGGER_MANUAL:
        return "draft"
    if autonomy == AUTONOMY_OFF:
        return "draft"
    if autonomy == AUTONOMY_CLARIFY_ONLY:
        # Hard code-level block: a factual reply is never auto-sent in
        # clarify_only, no matter what the model/prompt intended.
        return "send" if kind == DRAFT_KIND_CLARIFY else "draft"
    if autonomy == AUTONOMY_FULL:
        return "send"
    return "draft"


async def _resolve_completion_budget(session: AsyncSession) -> int:
    """Read ``KEY_AI_LLM_MAX_COMPLETION_TOKENS`` once per run (plan: LLM
    budget). Every agent ``chat()`` call in this run — tool loop/final
    answer and the identity exchange — uses this same budget."""
    return await get_setting_int(
        session, KEY_AI_LLM_MAX_COMPLETION_TOKENS, DEFAULT_MAX_COMPLETION_TOKENS
    )


def _is_empty_length_output(response: LlmResponse) -> bool:
    return (
        response.finish_reason == "length"
        and not response.tool_calls
        and not (response.content or "").strip()
    )


async def _chat_with_budget_retry(
    llm: LlmClient,
    *,
    messages: list[LlmMessage],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    max_tokens: int,
    temperature: float = 0.2,
) -> LlmResponse:
    """One agent ``chat()`` call, hardened against a reasoning model that
    burns its whole completion-token budget on hidden reasoning and returns
    ``finish_reason == "length"`` with empty content and no tool_calls (the
    prod incident this guards against).

    Exactly one immediate retry with a doubled budget; if that also comes
    back empty, raise :class:`LlmEmptyOutputError` instead of silently
    treating it as "the model had nothing to say" (the previous behaviour,
    which ended the run with a bare "no proposal" skip and no diagnostic).
    """
    response = await llm.chat(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not _is_empty_length_output(response):
        return response

    logger.warning(
        "ai_llm_empty_output_retry", max_tokens=max_tokens, retry_max_tokens=max_tokens * 2
    )
    retry_response = await llm.chat(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        max_tokens=max_tokens * 2,
        temperature=temperature,
    )
    combined_usage = LlmUsage(
        prompt_tokens=response.usage.prompt_tokens + retry_response.usage.prompt_tokens,
        completion_tokens=(
            response.usage.completion_tokens + retry_response.usage.completion_tokens
        ),
    )
    if _is_empty_length_output(retry_response):
        raise LlmEmptyOutputError(
            "LLM returned finish_reason='length' with empty content and no tool_calls "
            f"twice in a row (budgets {max_tokens}, then {max_tokens * 2})."
        )
    return replace(retry_response, usage=combined_usage)


async def _acquire_lock(session: AsyncSession, ticket_id: int, owner: str) -> TiqoraAiTicketState:
    state = await get_or_create_state(session, ticket_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    if state.run_lock_owner and state.run_lock_at:
        age = now - state.run_lock_at
        if age < _LOCK_MAX_AGE:
            raise LockHeldError(
                f"Ticket {ticket_id} run lock held by {state.run_lock_owner} ({age} ago)"
            )
        logger.warning(
            "ai_run_lock_stolen",
            ticket_id=ticket_id,
            previous_owner=state.run_lock_owner,
            age_seconds=age.total_seconds(),
        )
    state.run_lock_owner = owner
    state.run_lock_at = now
    await session.commit()
    return state


async def _release_lock(session: AsyncSession, ticket_id: int) -> None:
    state = await session.get(TiqoraAiTicketState, ticket_id)
    if state is not None:
        state.run_lock_owner = None
        state.run_lock_at = None
        await session.commit()


async def _load_mcp_tools(
    session: AsyncSession, policy: TiqoraAiQueuePolicy, *, settings: Settings
) -> list[McpToolSpec]:
    client_ids = parse_int_list(policy.mcp_client_ids)
    if not client_ids:
        return []
    clients = (
        (await session.execute(select(TiqoraMcpClient).where(TiqoraMcpClient.id.in_(client_ids))))
        .scalars()
        .all()
    )
    specs: list[McpToolSpec] = []
    for client in clients:
        auth_token = (
            decrypt_secret(settings.secret_key, client.auth_token_enc)
            if client.auth_token_enc
            else None
        )
        policies = (
            (
                await session.execute(
                    select(TiqoraMcpToolPolicy).where(
                        TiqoraMcpToolPolicy.mcp_client_id == client.id,
                        TiqoraMcpToolPolicy.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        for tp in policies:
            params_schema: dict[str, Any] | None = None
            if tp.parameters_snapshot:
                try:
                    parsed = json.loads(tp.parameters_snapshot)
                    if isinstance(parsed, dict):
                        params_schema = parsed
                except (TypeError, ValueError):
                    params_schema = None
            specs.append(
                McpToolSpec(
                    client_name=client.name,
                    client_url=client.url,
                    auth_token=auth_token,
                    tool_name=tp.tool_name,
                    mutating=bool(tp.mutating),
                    description=tp.description_snapshot,
                    parameters_schema=params_schema,
                )
            )
    return specs


async def _resolve_telegram_tone_prompt(
    session: AsyncSession,
    *,
    source_channel: str | None,
    based_on_channel: str | None,
) -> str | None:
    """The Telegram chat-tone system-prompt addendum (Task: Telegram-Chat-UX),
    or ``None`` when this run isn't Telegram-sourced.

    ``source_channel`` covers the auto-trigger (outbox event payload);
    ``trigger=manual`` (AI draft in the agent UI) never sets it, so that path
    is decided by the based-on/latest customer article's channel instead —
    see the call site in :func:`run_ticket_agent`. One cheap setting lookup,
    only when actually Telegram.
    """
    is_telegram = (source_channel or "").strip().lower() == _TELEGRAM_CHANNEL or (
        based_on_channel or ""
    ).strip().lower() == _TELEGRAM_CHANNEL
    if not is_telegram:
        return None
    from tiqora.channels.common import channel_setting
    from tiqora.channels.telegram.service import DEFAULT_TONE_PROMPT

    return (
        await channel_setting(session, _TELEGRAM_CHANNEL, "tone_prompt", DEFAULT_TONE_PROMPT)
    ) or DEFAULT_TONE_PROMPT


def _build_system_prompt(
    policy: TiqoraAiQueuePolicy,
    *,
    trigger: str,
    kind_hint: str | None,
    reply_language_binding: bool = False,
    prompt_parts: list[TiqoraAiPromptPart] | None = None,
    tone_prompt: str | None = None,
) -> str:
    # Kernel safety block first — not admin-editable, always present.
    parts = [UNTRUSTED_CONTENT_SYSTEM_BLOCK, policy.system_prompt or ""]
    ordered_parts = sorted(prompt_parts or [], key=lambda p: p.position)
    parts.extend(p.content for p in ordered_parts if p.enabled)
    if trigger == TRIGGER_MANUAL:
        parts.append(
            "You are assisting a human agent (Manual Assist). Whatever you propose via "
            "propose_customer_message will ALWAYS become a draft for the agent to review "
            "and edit — it is never sent automatically."
        )
    elif policy.autonomy == AUTONOMY_OFF:
        parts.append(
            "Any customer message you propose will be kept as a draft for a human to "
            "review and send — nothing you write reaches the customer directly."
        )
    elif policy.autonomy == AUTONOMY_CLARIFY_ONLY:
        parts.append(
            "A clarifying question (kind=clarify) you propose will be sent to the "
            "customer directly. A factual reply (kind=reply) will always be kept as a "
            "draft for a human to review."
        )
    else:
        parts.append(
            "Any customer message you propose (reply or clarify) will be sent to the "
            "customer directly — write as if you are the final responder."
        )
    if kind_hint:
        parts.append(f"Hint: this run is expected to produce a '{kind_hint}' message.")
    if reply_language_binding:
        parts.append("The reply language stated in the ticket header is binding.")
    if tone_prompt:
        parts.append(tone_prompt)
    return "\n\n".join(p for p in parts if p)


def _resolve_reply_language_line(
    policy: TiqoraAiQueuePolicy, ticket: TicketSnapshot, customer_articles: list[ArticleSnapshot]
) -> str | None:
    """Plan block 3: at most one binding reply-language line, resolved once
    per run — never per article. ``off`` (default) reproduces today's
    behaviour exactly (no line at all)."""
    if policy.reply_language_mode == REPLY_LANGUAGE_FIXED:
        if not policy.reply_language_fixed:
            return None
        return f"Reply language (binding): {policy.reply_language_fixed}"
    if policy.reply_language_mode == REPLY_LANGUAGE_AUTO:
        latest_body = customer_articles[-1].body if customer_articles else None
        if policy.reply_language_default:
            lang = detect_reply_language(
                ticket.title,
                latest_body,
                candidates=list(LANGUAGE_PROFILES),
                default=policy.reply_language_default,
            )
            return f"Reply language (binding): {lang}"
        # No configured default: only trust the detector when it actually
        # reached the minimum stopword-match score — otherwise emit no line
        # at all rather than silently defaulting to some language the
        # customer never wrote in (the prod bug this fixes).
        detection = detect_reply_language_detailed(
            ticket.title, latest_body, candidates=list(LANGUAGE_PROFILES), default=""
        )
        if detection.used_fallback:
            return None
        return f"Reply language (binding): {detection.language}"
    return None


def _build_user_message(
    ticket: TicketSnapshot,
    articles: list[ArticleSnapshot],
    *,
    pii: PiiMapper,
    mask: bool,
    kb_bundle: str | None,
    attachment_blocks: dict[int, str] | None = None,
    reply_language_line: str | None = None,
) -> str:
    lines = [render_ticket_header(ticket)]
    if reply_language_line:
        lines.append(reply_language_line)
    lines.append("")
    for a in articles:
        label = "agent" if a.sender_type == "agent" else a.sender_type
        if a.is_ai_origin:
            label += " (AI, previous own action)"
        body = a.body or ""
        subject_line = f"Subject: {a.subject}" if a.subject else None
        if mask:
            body = pii.mask(body)
            if subject_line:
                subject_line = pii.mask(subject_line)
        attach_text = (attachment_blocks or {}).get(a.id)
        if attach_text:
            # Masked separately so the "[Anhang: …]" label lines stay intact
            # (see mask_attachment_block).
            if mask:
                attach_text = mask_attachment_block(pii, attach_text)
            body = f"{body}\n\n{attach_text}" if body else attach_text
        # Untrusted content delimiter (plan §3.8) — the article body is
        # customer/agent free text, never instructions to the model.
        lines.append(f"--- article {a.id} [{label}] ---")
        if subject_line:
            lines.append(subject_line)
        lines.append(body)
        lines.append("")
    if kb_bundle:
        lines.append("--- knowledge base ---")
        lines.append(kb_bundle)
    return "\n".join(lines)


def _disclosure_footer(default_text: str, override_text: str | None) -> str:
    return (override_text or default_text or "").strip()


def _build_identity_system_prompt(fields: list[Any], *, tone_prompt: str | None = None) -> str:
    """System prompt for the identity-check mini-exchange (Task 6). Replaces
    the normal system prompt entirely — the model must not see the queue's
    configured prompt/tools while identity is unconfirmed.

    ``tone_prompt`` (Task: Telegram-Chat-UX) is always passed by the caller —
    this exchange only ever runs for a Telegram-sourced run (see the guard in
    :func:`run_ticket_agent`)."""
    field_lines = "\n".join(f"- {f.label} (internal key: {f.column})" for f in fields)
    parts = [
        UNTRUSTED_CONTENT_SYSTEM_BLOCK,
        (
            "This customer's identity is NOT yet confirmed. You may NOT answer any "
            "support question, and you may NOT reveal any ticket, account, or "
            "personal information. Your only job right now is identity "
            "verification via propose_customer_message:\n"
            "- If the customer's latest message does not (yet) contain all of the "
            "fields below, propose kind='clarify' politely asking for them.\n"
            "- If the customer's latest message already states them, extract the "
            "values into 'identity_claim' (an object keyed by the internal key "
            "below) AND still propose kind='clarify' with a short acknowledgement "
            "body (e.g. 'Thank you, checking that now.') — never a factual answer.\n\n"
            f"Required fields:\n{field_lines}"
        ),
    ]
    if tone_prompt:
        parts.append(tone_prompt)
    return "\n\n".join(parts)


def _build_identity_user_message(articles: list[ArticleSnapshot]) -> str:
    """Minimal, structurally-safe context for the identity mini-exchange.

    Deliberately NOT :func:`_build_user_message` / ``render_ticket_header`` —
    those include the full article history (incl. internal, agent-only
    notes) and the ticket's current CustomerID/CustomerUser. An unidentified
    Telegram user must not be able to get any of that echoed back via
    prompt injection; the only thing the model needs to do its job (ask for
    the configured fields, or extract them from what the customer just
    wrote) is the latest customer message — nothing else is included, so
    there is nothing else for a compromised model to leak."""
    latest_customer_body = next(
        (a.body or "" for a in reversed(articles) if a.sender_type == "customer"),
        "",
    )
    return f"--- latest customer message ---\n{latest_customer_body}"


def _identity_tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_PROPOSE_CUSTOMER_MESSAGE,
                "description": (
                    "Deliver an identity-check message to the customer, and/or extract "
                    "identity_claim values from their latest message. This is the ONLY "
                    "tool available right now — no support answer, no other action."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["reply", "clarify"]},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "identity_claim": {
                            "type": "object",
                            "description": (
                                "Field values the customer already provided, keyed by "
                                "the internal key given in the system prompt. Omit "
                                "entirely if none were provided yet."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["kind", "body"],
                },
            },
        }
    ]


async def _dispatch_identity_message(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket: TicketSnapshot,
    actor_user_id: int,
    trigger: str,
    autonomy: str,
    kind: str,
    subject: str,
    body: str,
    based_on_article_id: int | None,
    created_by_user_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> AgentRunResult:
    """Draft or send the identity-flow's proposed message via Telegram (the
    identity block only ever runs for a Telegram-sourced run — see the guard
    in :func:`run_ticket_agent`)."""
    destination = _map_customer_message(trigger=trigger, autonomy=autonomy, kind=kind)
    source = SOURCE_MANUAL if trigger == TRIGGER_MANUAL else SOURCE_AUTO

    if destination == "draft":
        draft = await draft_service.create_draft(
            session,
            ticket_id=ticket.ticket_id,
            queue_id=ticket.queue_id,
            kind=kind,
            body=body,
            subject=subject or None,
            based_on_article_id=based_on_article_id,
            tool_trace_json=None,
            created_by_user_id=created_by_user_id,
            source=source,
            actor_user_id=actor_user_id,
        )
        return AgentRunResult(
            status=STATUS_DRAFTED,
            draft_id=draft.id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    from tiqora.channels.telegram.outbound import deliver_agent_telegram_reply

    article_id = await deliver_agent_telegram_reply(
        session,
        sysconfig,
        ticket_id=ticket.ticket_id,
        user_id=actor_user_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=True,
            subject=subject or ticket.title,
            body=body,
            channel=_TELEGRAM_CHANNEL,
        ),
    )
    session.add(
        TiqoraAiArticleOrigin(
            article_id=article_id,
            source=SOURCE_AUTO,
            queue_id=ticket.queue_id,
            service_user_id=actor_user_id,
        )
    )
    return AgentRunResult(
        status=STATUS_SENT,
        article_id=article_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def _typing_loop(gateway: Any, chat_id: int) -> None:
    """Send a Telegram "typing" chat action roughly every
    :data:`_TYPING_INTERVAL_SECONDS`, forever, until the task is cancelled by
    the caller (:func:`run_ticket_agent`'s ``finally``). Any error from the
    gateway call is only debug-logged — a failed typing indicator must never
    fail (or even affect) the agent run."""
    while True:
        try:
            await gateway.send_chat_action(chat_id, "typing")
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            logger.debug("ai_typing_indicator_send_failed", chat_id=chat_id, error=str(exc))
        await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


async def _maybe_start_typing_indicator(
    session: AsyncSession, ticket_id: int, *, gateway: Any = None
) -> asyncio.Task[None] | None:
    """Resolve the Telegram chat_id + gateway and start the background
    typing-indicator task, or return ``None`` (no indicator) on any failure —
    e.g. the channel is disabled, has no bot_token, or the chat_id can't be
    resolved (see :func:`tiqora.channels.telegram.outbound.resolve_chat_id`).
    Never raises: a missing typing indicator is cosmetic, not a run failure.
    """
    from tiqora.channels.telegram.outbound import (
        TelegramDeliveryError,
        build_gateway,
        resolve_chat_id,
    )

    try:
        chat_id = await resolve_chat_id(session, ticket_id)
        gw = gateway if gateway is not None else await build_gateway(session)
    except TelegramDeliveryError as exc:
        logger.debug("ai_typing_indicator_unavailable", ticket_id=ticket_id, error=str(exc))
        return None
    return asyncio.ensure_future(_typing_loop(gw, chat_id))


async def _run_identity_exchange(
    session: AsyncSession,
    *,
    settings: Settings,
    llm: LlmClient,
    sysconfig: SysConfig,
    policy: TiqoraAiQueuePolicy,
    ticket: TicketSnapshot,
    articles: list[ArticleSnapshot],
    based_on_article_id: int | None,
    trigger: str,
    actor_user_id: int,
    audit_context: AuditContext,
    created_by_user_id: int | None,
    completion_budget: int,
) -> AgentRunResult | None:
    """Run one identity-check LLM exchange (Task 6). Returns an
    :class:`AgentRunResult` when the run ends here (drafted/sent/skipped),
    or ``None`` when identity was just confirmed and the caller should
    continue into the normal run (re-loading the ticket snapshot first)."""
    from tiqora.channels.telegram.outbound import resolve_chat_id

    ticket_id = ticket.ticket_id
    source = SOURCE_MANUAL if trigger == TRIGGER_MANUAL else SOURCE_AUTO
    state = await get_or_create_state(session, ticket_id)

    fields = parse_clarify_schema(policy)
    if not fields:
        # Misconfigured policy (clarify_schema mode but no usable schema) —
        # fail safe to a human draft rather than looping on a check that can
        # never succeed.
        draft = await draft_service.create_draft(
            session,
            ticket_id=ticket_id,
            queue_id=ticket.queue_id,
            kind=DRAFT_KIND_CLARIFY,
            body="Identity verification is misconfigured for this queue (clarify_schema_json).",
            based_on_article_id=based_on_article_id,
            created_by_user_id=created_by_user_id,
            source=source,
            actor_user_id=actor_user_id,
        )
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return AgentRunResult(status=STATUS_DRAFTED, draft_id=draft.id)

    identity_llm = AuditingLlmClient(
        llm, settings=settings, context=audit_context, session=session, pii_mapper=PiiMapper()
    )
    # This exchange is only ever entered for a Telegram-sourced run (guard in
    # run_ticket_agent), so the tone addendum is unconditional here.
    identity_tone_prompt = await _resolve_telegram_tone_prompt(
        session, source_channel=_TELEGRAM_CHANNEL, based_on_channel=None
    )
    system_prompt = _build_identity_system_prompt(fields, tone_prompt=identity_tone_prompt)
    user_message = _build_identity_user_message(articles)
    messages: list[LlmMessage] = [
        LlmMessage(role="system", content=system_prompt),
        LlmMessage(role="user", content=user_message),
    ]
    response: LlmResponse = await _chat_with_budget_retry(
        identity_llm,
        messages=messages,
        tools=_identity_tool_schema(),
        max_tokens=completion_budget,
    )
    prompt_tokens = response.usage.prompt_tokens
    completion_tokens = response.usage.completion_tokens

    tool_call = next(
        (tc for tc in (response.tool_calls or []) if tc.name == TOOL_PROPOSE_CUSTOMER_MESSAGE),
        None,
    )
    if tool_call is None:
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return AgentRunResult(
            status=STATUS_SKIPPED,
            notes="Identity check produced no proposal.",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    args = tool_call.arguments or {}
    kind = args.get("kind")
    body = args.get("body")
    subject_raw = args.get("subject")
    subject = subject_raw if isinstance(subject_raw, str) else ""
    if kind not in ("reply", "clarify") or not isinstance(body, str) or not body.strip():
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return AgentRunResult(
            status=STATUS_SKIPPED,
            notes="Identity check proposal was malformed.",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    try:
        validate_customer_message(kind=kind, subject=subject, body=body)
    except CustomerMessageGuardError as exc:
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return AgentRunResult(
            status=STATUS_SKIPPED,
            notes=f"Identity check proposal rejected: {exc}",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    identity_claim_raw = args.get("identity_claim")
    claim_values: dict[str, str] = {}
    if isinstance(identity_claim_raw, dict):
        claim_values = {k: v for k, v in identity_claim_raw.items() if isinstance(v, str)}

    if claim_values:
        login = await verify_identity_claim(session, fields, claim_values)
        if login is not None:
            chat_id = await resolve_chat_id(session, ticket_id)
            contact = (
                await session.execute(
                    select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == chat_id)
                )
            ).scalar_one_or_none()
            if contact is not None:
                contact.customer_user_login = login
            customer_id = await get_customer_id_for_login(session, login)
            await domain_set_customer(
                session,
                ticket_id=ticket_id,
                customer_id=customer_id,
                customer_user_id=login,
                user_id=actor_user_id,
            )
            state.identity_attempts = 0
            await session.commit()
            return None  # identified — caller continues the normal run

        attempts = await record_identity_attempt(session, state)
        await session.commit()
        if attempts >= MAX_IDENTITY_ATTEMPTS:
            draft = await draft_service.create_draft(
                session,
                ticket_id=ticket_id,
                queue_id=ticket.queue_id,
                kind=DRAFT_KIND_CLARIFY,
                body=(
                    "Identity could not be confirmed after multiple attempts — "
                    "please review manually."
                ),
                based_on_article_id=based_on_article_id,
                created_by_user_id=created_by_user_id,
                source=source,
                actor_user_id=actor_user_id,
            )
            state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return AgentRunResult(
                status=STATUS_DRAFTED,
                draft_id=draft.id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

    result = await _dispatch_identity_message(
        session,
        sysconfig,
        ticket=ticket,
        actor_user_id=actor_user_id,
        trigger=trigger,
        autonomy=policy.autonomy,
        kind=kind,
        subject=subject,
        body=body,
        based_on_article_id=based_on_article_id,
        created_by_user_id=created_by_user_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    return result


async def run_ticket_agent(
    session: AsyncSession,
    *,
    settings: Settings,
    llm: LlmClient,
    ticket_id: int,
    trigger: str,
    acting_user_id: int | None,
    kind_hint: str | None = None,
    run_id: str,
    worker_instance: str = "manual",
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    mcp_caller: Any = None,
    kb_search_fn: Any = None,
    kb_get_article_fn: Any = None,
    kb_bundle: str | None = None,
    vision_llm_factory: Any = None,
    source_channel: str | None = None,
    telegram_gateway: Any = None,
) -> AgentRunResult:
    """Run the agent once for one ticket (plan §3.4 steps 1-12).

    ``run_id``/``worker_instance`` form the lock owner (``worker:run_id``).
    ``mcp_caller``/``kb_search_fn``/``kb_get_article_fn`` are injectable seams
    for tests; production omits them (real fastmcp/KB calls). ``vision_llm_factory``
    (a sync ``() -> LlmClient``) is an injectable seam for the attachment
    vision pre-pass — production omits it and the queue policy's
    ``vision_provider_id`` is resolved automatically; tests inject a fake to
    assert on the vision prompt without a real endpoint. ``source_channel``
    is the triggering article's channel (from the outbox event payload,
    auto-worker only) — passed through to the gate re-check below so a
    Telegram-sourced run is exempt from the ``operation_mode`` check (see
    ``tiqora.ai.gate``). ``telegram_gateway`` is an injectable seam for the
    typing-indicator task (tests); production omits it and a gateway is
    built lazily from the channel config only when actually needed.
    """
    # 1. Readiness gate — auto-reply only (plan §3.0 v1.1 relaxation, Phase
    # E). Manual Assist always runs regardless of operation_mode: it only
    # ever produces a draft (see _map_customer_message below), never a
    # customer-visible send.
    if trigger == TRIGGER_AUTO:
        try:
            await require_feature_allowed(
                session, FEATURE_AUTO_REPLY, source_channel=source_channel
            )
        except AiGateError as exc:
            raise AgentRunError(str(exc)) from exc

    # 2. Per-ticket lock
    lock_owner = f"{worker_instance}:{run_id}"
    await _acquire_lock(session, ticket_id, lock_owner)

    typing_task: asyncio.Task[None] | None = None

    try:
        try:
            ticket = await ticket_snapshot(session, ticket_id)
        except TicketNotFoundError as exc:
            raise AgentRunError(str(exc)) from exc

        # 3. Policy + feature + ACL
        policy = await get_queue_policy_by_queue(session, ticket.queue_id)
        if policy is None:
            raise PolicyDisabledError(f"No AI policy configured for queue {ticket.queue_id}")
        prompt_parts = await load_prompt_parts(session, policy.id)

        feature = FEATURE_MANUAL_ASSIST if trigger == TRIGGER_MANUAL else FEATURE_AUTO_REPLY
        if trigger == TRIGGER_MANUAL and not policy.enabled_manual_assist:
            raise PolicyDisabledError("Manual Assist is disabled for this queue")
        if trigger == TRIGGER_AUTO and not policy.enabled_auto_reply:
            raise PolicyDisabledError("Auto-reply is disabled for this queue")

        if trigger == TRIGGER_MANUAL:
            if acting_user_id is None:
                raise AgentRunError("Manual Assist requires an acting user")
            if not await check_feature_access(session, acting_user_id, feature):
                raise AclDeniedError(f"User {acting_user_id} is not allowed to use {feature}")
            try:
                await check_feature_limits(session, acting_user_id, feature)
            except AiAclLimitExceededError as exc:
                raise AclLimitExceededError(str(exc)) from exc
            actor_user_id = acting_user_id
        else:
            if policy.service_user_id is None:
                raise PolicyDisabledError("Auto-reply enabled but no service_user_id configured")
            actor_user_id = policy.service_user_id

        sysconfig = SysConfig(session)

        # Completion-token budget (plan: LLM budget) — resolved once per run,
        # used by every agent chat() call below (identity exchange, tool
        # loop/final answer).
        completion_budget = await _resolve_completion_budget(session)

        # 4. Load ticket + articles; based_on_article_id = latest customer article
        articles = await load_articles(session, ticket_id)
        customer_articles = [a for a in articles if a.sender_type == "customer"]
        based_on_article_id = customer_articles[-1].id if customer_articles else None

        # Typing indicator (auto-trigger, Telegram source only): resolved
        # synchronously (chat_id + gateway) via the *same* session before the
        # background task starts, because AsyncSession is not safe for
        # concurrent use across tasks — the loop below never touches the
        # session again, only the gateway. Best-effort: any failure here
        # (channel disabled, no bot_token, chat_id unresolvable) just means
        # no typing indicator, never a fatal run error.
        if trigger == TRIGGER_AUTO and (source_channel or "").strip().lower() == _TELEGRAM_CHANNEL:
            typing_task = await _maybe_start_typing_indicator(
                session, ticket_id, gateway=telegram_gateway
            )

        # 6/7. Prompts — document/image attachments are rendered into the
        # per-article text before masking (see build_attachment_context).
        audit_feature = (
            AUDIT_FEATURE_DRAFT if trigger == TRIGGER_MANUAL else AUDIT_FEATURE_AUTO_REPLY
        )
        audit_context = AuditContext(
            feature=audit_feature,
            run_id=run_id,
            ticket_id=ticket_id,
            queue_id=ticket.queue_id,
            acting_user_id=actor_user_id,
            trigger=trigger,
            provider_id=policy.llm_provider_id,
            model=policy.model_override,
        )

        # Identity check (Task 6, plan: identity verification) — wired ONLY
        # for Telegram-sourced runs (guard is the outermost condition; every
        # other channel/identity_mode combination falls straight through
        # unchanged). clarify_schema means the customer's Telegram chat has
        # not yet been matched to a customer_user login: the model may not
        # produce a real answer until that happens.
        if (
            (source_channel or "").strip().lower() == _TELEGRAM_CHANNEL
            and policy.identity_mode == IDENTITY_CLARIFY_SCHEMA
            and not await is_identified(
                session, ticket_id, source_channel=source_channel, policy=policy
            )
        ):
            identity_result = await _run_identity_exchange(
                session,
                settings=settings,
                llm=llm,
                sysconfig=sysconfig,
                policy=policy,
                ticket=ticket,
                articles=articles,
                based_on_article_id=based_on_article_id,
                trigger=trigger,
                actor_user_id=actor_user_id,
                audit_context=audit_context,
                created_by_user_id=(acting_user_id if trigger == TRIGGER_MANUAL else None),
                completion_budget=completion_budget,
            )
            if identity_result is not None:
                return identity_result
            # Identified during this run (identity_claim verified): the
            # ticket's customer was just re-pointed — reload the snapshot
            # before building the normal prompt/tools below.
            ticket = await ticket_snapshot(session, ticket_id)

        # 5. AI-content filter is applied when rendering (labels own AI output,
        # see _build_user_message) — nothing is physically removed.

        effective_vision_factory = vision_llm_factory
        if effective_vision_factory is None and policy.vision_provider_id is not None:
            effective_vision_factory = await build_vision_llm_factory(
                session, settings, policy.vision_provider_id, audit=audit_context
            )
        attachment_context = await build_attachment_context(
            session,
            articles,
            vision_enabled=policy.vision_provider_id is not None,
            vision_llm_factory=effective_vision_factory,
        )

        never_mask = {v for v in (ticket.customer_id, ticket.customer_user_id) if v}
        ner_texts = (
            ner_source_texts(articles, attachment_context.blocks)
            if policy.pii_masking and policy.pii_ner_enabled
            else None
        )
        known_names = await collect_known_names(session, ticket, articles, extra_texts=ner_texts)
        pii = PiiMapper(never_mask=never_mask or None, known_names=known_names or None)
        # Kept before the AuditingLlmClient wrap below so record_usage() can
        # read active_provider_id/active_model off it after the run — set by
        # FallbackLlmClient (tiqora.ai.llm_fallback) once a fallback entry
        # actually served a response; absent on a plain OpenAiCompatLlmClient.
        raw_llm = llm
        llm = AuditingLlmClient(
            llm, settings=settings, context=audit_context, session=session, pii_mapper=pii
        )
        reply_language_line = _resolve_reply_language_line(policy, ticket, customer_articles)
        # trigger=manual (AI draft in the agent UI) never sets source_channel,
        # so a Telegram ticket is instead recognized off the based-on/latest
        # customer article's channel (Task: Telegram-Chat-UX).
        based_on_channel = next((a.channel for a in articles if a.id == based_on_article_id), None)
        tone_prompt = await _resolve_telegram_tone_prompt(
            session, source_channel=source_channel, based_on_channel=based_on_channel
        )
        system_prompt = _build_system_prompt(
            policy,
            trigger=trigger,
            kind_hint=kind_hint,
            reply_language_binding=reply_language_line is not None,
            prompt_parts=prompt_parts,
            tone_prompt=tone_prompt,
        )
        user_message = _build_user_message(
            ticket,
            articles,
            pii=pii,
            mask=bool(policy.pii_masking),
            kb_bundle=kb_bundle,
            attachment_blocks=attachment_context.blocks,
            reply_language_line=reply_language_line,
        )

        # 8. Tools
        mcp_tools = await _load_mcp_tools(session, policy, settings=settings)
        kb_enabled = bool(policy.kb_tags or policy.kb_category_ids)
        capabilities = resolve_capabilities(
            policy.autonomy, capabilities_json=policy.capabilities_json
        )
        registry = ToolRegistry(
            capabilities=capabilities, mcp_tools=mcp_tools, kb_enabled=kb_enabled
        )
        escalation_rules = json.loads(policy.escalation_rules) if policy.escalation_rules else None
        executor = ToolExecutor(
            session=session,
            sysconfig=sysconfig,
            registry=registry,
            ticket_id=ticket_id,
            acting_user_id=actor_user_id,
            pii=pii,
            escalation_rules=escalation_rules,
            mcp_caller=mcp_caller,
            kb_search_fn=kb_search_fn,
            kb_get_article_fn=kb_get_article_fn,
            allowed_state_types_raw=policy.allowed_state_types,
            mask_results=bool(policy.pii_masking),
            ticket_customer_id=ticket.customer_id,
            ticket_customer_user_id=ticket.customer_user_id,
        )

        messages: list[LlmMessage] = [
            LlmMessage(role="system", content=system_prompt),
            LlmMessage(role="user", content=user_message),
        ]
        schemas = registry.build_schemas()

        prompt_tokens = attachment_context.vision_usage.prompt_tokens
        completion_tokens = attachment_context.vision_usage.completion_tokens
        outcome: ToolOutcome | None = None
        executed_tool_names: list[str] = []

        # 9. Tool loop
        nudges_left = _MAX_PLAIN_TEXT_NUDGES
        for _round in range(max_tool_rounds):
            response: LlmResponse = await _chat_with_budget_retry(
                llm, messages=messages, tools=schemas, max_tokens=completion_budget
            )
            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens

            if not response.tool_calls:
                # Plain text never reaches anyone (no send-tool exists) — but
                # reasoning models sometimes write the finished customer reply
                # as content instead of calling propose_customer_message.
                # Nudge them back onto a terminal tool call instead of ending
                # the run as "no proposal".
                if (response.content or "").strip() and nudges_left > 0:
                    nudges_left -= 1
                    logger.info("ai_plain_text_nudge", ticket_id=ticket_id, nudges_left=nudges_left)
                    messages.append(LlmMessage(role="assistant", content=response.content))
                    messages.append(LlmMessage(role="user", content=_PLAIN_TEXT_NUDGE))
                    continue
                break

            messages.append(
                LlmMessage(
                    role="assistant", content=response.content, tool_calls=response.tool_calls
                )
            )
            terminal_hit = False
            for tc in response.tool_calls:
                try:
                    result = await executor.execute(tc.name, tc.arguments)
                except (UnknownToolError, ToolArgumentError) as exc:
                    messages.append(
                        LlmMessage(
                            role="tool", tool_call_id=tc.id, name=tc.name, content=f"Error: {exc}"
                        )
                    )
                    continue
                executed_tool_names.append(tc.name)
                messages.append(
                    LlmMessage(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=result.content_for_model,
                    )
                )
                if result.terminal:
                    outcome = result
                    terminal_hit = True
                    break
            if terminal_hit:
                break

        # 9b. Terminal-force: the loop ended without any terminal tool call —
        # models sometimes burn every round on research (kb_search/MCP) and
        # never deliver. Give exactly one closing call restricted to the two
        # terminal tools so the run produces a proposal/escalation instead of
        # silently ending as "no proposal". No tool_choice forcing — not every
        # OpenAI-compatible provider accepts "required", the schema restriction
        # plus instruction is provider-agnostic.
        if outcome is None:
            terminal_schemas = [
                s
                for s in schemas
                if s.get("function", {}).get("name")
                in (TOOL_PROPOSE_CUSTOMER_MESSAGE, TOOL_ESCALATE_TO_HUMAN)
            ]
            messages.append(LlmMessage(role="user", content=_TERMINAL_FORCE_PROMPT))
            logger.info("ai_terminal_force", ticket_id=ticket_id, trigger=trigger)
            response = await _chat_with_budget_retry(
                llm, messages=messages, tools=terminal_schemas, max_tokens=completion_budget
            )
            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens
            if response.tool_calls:
                messages.append(
                    LlmMessage(
                        role="assistant", content=response.content, tool_calls=response.tool_calls
                    )
                )
                for tc in response.tool_calls:
                    try:
                        result = await executor.execute(tc.name, tc.arguments)
                    except (UnknownToolError, ToolArgumentError) as exc:
                        messages.append(
                            LlmMessage(
                                role="tool",
                                tool_call_id=tc.id,
                                name=tc.name,
                                content=f"Error: {exc}",
                            )
                        )
                        continue
                    executed_tool_names.append(tc.name)
                    messages.append(
                        LlmMessage(
                            role="tool",
                            tool_call_id=tc.id,
                            name=tc.name,
                            content=result.content_for_model,
                        )
                    )
                    if result.terminal:
                        outcome = result
                        break

        chain_alerts = analyze_tool_chain(executed_tool_names)
        for alert in chain_alerts:
            logger.warning(
                "ai_tool_chain_alert",
                ticket_id=ticket_id,
                code=alert.code,
                message=alert.message,
                tools=list(alert.tools),
                trigger=trigger,
            )

        await usage_service.record_usage(
            session,
            user_id=acting_user_id if trigger == TRIGGER_MANUAL else None,
            queue_id=ticket.queue_id,
            ticket_id=ticket_id,
            feature=feature,
            provider_id=getattr(raw_llm, "active_provider_id", None) or policy.llm_provider_id,
            model=getattr(raw_llm, "active_model", None) or policy.model_override,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            extra_json=json.dumps(
                {
                    "tool_trace": "masked_in_messages",
                    "tools_executed": executed_tool_names,
                    "tool_chain_alerts": [
                        {"code": a.code, "message": a.message} for a in chain_alerts
                    ],
                }
            ),
        )

        # 12 (ticket state bookkeeping happens below, after we know the outcome)
        state = await get_or_create_state(session, ticket_id)

        if outcome is None:
            state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return AgentRunResult(
                status=STATUS_SKIPPED,
                notes="No terminal tool call produced (no proposal, no escalation).",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        if outcome.escalate_reason is not None:
            state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return AgentRunResult(
                status=STATUS_ESCALATED,
                notes=outcome.escalate_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        # propose_customer_message is the only other terminal path.
        assert outcome.proposal is not None

        # 10. Freshness check
        latest_customer = await latest_customer_article_id(session, ticket_id)
        if (
            based_on_article_id is not None
            and latest_customer is not None
            and latest_customer != based_on_article_id
        ):
            await _release_lock(session, ticket_id)
            return AgentRunResult(
                status=STATUS_SUPERSEDED,
                notes="A newer customer article arrived during this run.",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        # 11. Autonomy mapping
        destination = _map_customer_message(
            trigger=trigger, autonomy=policy.autonomy, kind=outcome.proposal["kind"]
        )
        source = SOURCE_MANUAL if trigger == TRIGGER_MANUAL else SOURCE_AUTO
        created_by_user_id = acting_user_id if trigger == TRIGGER_MANUAL else None

        if destination == "draft":
            draft = await draft_service.create_draft(
                session,
                ticket_id=ticket_id,
                queue_id=ticket.queue_id,
                kind=outcome.proposal["kind"],
                body=outcome.proposal["body"],
                subject=outcome.proposal.get("subject") or None,
                based_on_article_id=based_on_article_id,
                tool_trace_json=json.dumps([m.to_wire() for m in messages if m.role == "tool"]),
                created_by_user_id=created_by_user_id,
                source=source,
                actor_user_id=actor_user_id,
            )
            state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
            state.last_customer_article_id = based_on_article_id
            await session.commit()
            return AgentRunResult(
                status=STATUS_DRAFTED,
                draft_id=draft.id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        # destination == "send": auto path only (manual always drafts above)
        footer = ""
        if policy.ai_disclosure_enabled:
            default_text = await get_setting(session, KEY_AI_DISCLOSURE_DEFAULT) or ""
            footer = _disclosure_footer(default_text, policy.ai_disclosure_text)
        body = outcome.proposal["body"]
        if footer:
            body = f"{body}\n\n{footer}"

        src = next((a for a in articles if a.id == based_on_article_id), None)
        # Channel dispatch: the based_on article's channel decides where the
        # reply goes. Telegram is checked FIRST and unconditionally — its
        # a_from is a synthetic "<chat_id>@telegram.invalid" address that
        # would otherwise parseaddr-match the email branch below and blow up
        # an SMTP send to a fake address (the central guard this dispatch
        # exists for).
        if src is not None and src.channel.lower() == _TELEGRAM_CHANNEL:
            from tiqora.channels.telegram.outbound import deliver_agent_telegram_reply

            article_id = await deliver_agent_telegram_reply(
                session,
                sysconfig,
                ticket_id=ticket_id,
                user_id=actor_user_id,
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject=outcome.proposal.get("subject") or ticket.title,
                    body=body,
                    channel=_TELEGRAM_CHANNEL,
                ),
            )
        else:
            to_address = None
            if src is not None and src.from_address:
                to_address = parseaddr(src.from_address)[1] or None

            if to_address:
                from tiqora.channels.email.outbound_reply import deliver_agent_email_reply

                article_id = await deliver_agent_email_reply(
                    session,
                    sysconfig,
                    None,
                    ticket_id=ticket_id,
                    queue_id=ticket.queue_id,
                    user_id=actor_user_id,
                    article=ArticleIn(
                        sender_type="agent",
                        is_visible_for_customer=True,
                        subject=outcome.proposal.get("subject") or ticket.title,
                        body=body,
                        to_address=to_address,
                        channel="email",
                    ),
                )
            else:
                article_id = await add_article(
                    session,
                    ticket_id=ticket_id,
                    article=ArticleIn(
                        sender_type="agent",
                        is_visible_for_customer=True,
                        subject=outcome.proposal.get("subject") or ticket.title,
                        body=body,
                        channel="note",
                    ),
                    user_id=actor_user_id,
                    sysconfig=sysconfig,
                )

        session.add(
            TiqoraAiArticleOrigin(
                article_id=article_id,
                source=SOURCE_AUTO,
                queue_id=ticket.queue_id,
                service_user_id=actor_user_id,
                tool_trace_json=json.dumps([m.to_wire() for m in messages if m.role == "tool"]),
                run_id=run_id,
            )
        )
        state.last_run_at = datetime.now(UTC).replace(tzinfo=None)
        state.last_customer_article_id = based_on_article_id
        if outcome.proposal["kind"] == DRAFT_KIND_REPLY:
            state.auto_reply_count = (state.auto_reply_count or 0) + 1
        else:
            state.clarification_count = (state.clarification_count or 0) + 1
        await session.commit()
        return AgentRunResult(
            status=STATUS_SENT,
            article_id=article_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except (AgentRunError, LlmError) as exc:
        # LlmError (incl. LlmEmptyOutputError/LlmTimeoutError/LlmHttpError)
        # isn't an AgentRunError — it propagates from llm.chat() itself, not
        # from a run-abort check — but it deserves the same last_error
        # bookkeeping before the caller (tiqora.api.v1.ai) maps it to a
        # structured HTTP error.
        try:
            error_state = await session.get(TiqoraAiTicketState, ticket_id)
            if error_state is not None:
                error_state.last_error = str(exc)
                await session.commit()
        except Exception:  # noqa: BLE001 — best-effort bookkeeping only
            logger.exception("ai_runtime_error_bookkeeping_failed", ticket_id=ticket_id)
        raise
    finally:
        if typing_task is not None:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
        await _release_lock(session, ticket_id)


__all__ = [
    "DEFAULT_MAX_TOOL_ROUNDS",
    "STATUS_DRAFTED",
    "STATUS_ERROR",
    "STATUS_ESCALATED",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "STATUS_SUPERSEDED",
    "TRIGGER_AUTO",
    "TRIGGER_MANUAL",
    "AclDeniedError",
    "AclLimitExceededError",
    "AgentRunError",
    "AgentRunResult",
    "LockHeldError",
    "PolicyDisabledError",
    "run_ticket_agent",
]
