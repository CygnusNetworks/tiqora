"""AgentRuntime — the per-ticket agent run (plan §3.4 steps 1-12).

Entry point: :func:`run_ticket_agent`. Phase B wires this up fully for the
**manual** trigger (Manual Assist, always the draft path — plan §3.4:
"Manual Assist ist immer Draft-Pfad"); the **auto** trigger's autonomy →
draft/send mapping is implemented and unit-tested here too (plan requires
the mapping logic to exist now), but nothing calls it with ``trigger="auto"``
until the outbox-driven worker lands in Phase D.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
from tiqora.ai.kb_wiring import build_vision_llm_factory
from tiqora.ai.listfields import parse_int_list
from tiqora.ai.llm import LlmClient, LlmMessage, LlmResponse
from tiqora.ai.models import (
    AUTONOMY_CLARIFY_ONLY,
    AUTONOMY_FULL,
    AUTONOMY_OFF,
    DRAFT_KIND_CLARIFY,
    DRAFT_KIND_REPLY,
    FEATURE_AUTO_REPLY,
    FEATURE_MANUAL_ASSIST,
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
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue, load_prompt_parts
from tiqora.ai.reply_language import (
    LANGUAGE_PROFILES,
    detect_reply_language,
    detect_reply_language_detailed,
)
from tiqora.ai.tools import (
    McpToolSpec,
    ToolArgumentError,
    ToolExecutor,
    ToolOutcome,
    ToolRegistry,
    UnknownToolError,
)
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.domain.settings_store import KEY_AI_DISCLOSURE_DEFAULT, get_setting
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_LOCK_MAX_AGE = timedelta(minutes=15)
DEFAULT_MAX_TOOL_ROUNDS = 8

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
            specs.append(
                McpToolSpec(
                    client_name=client.name,
                    client_url=client.url,
                    auth_token=auth_token,
                    tool_name=tp.tool_name,
                    mutating=bool(tp.mutating),
                    description=tp.description_snapshot,
                )
            )
    return specs


def _build_system_prompt(
    policy: TiqoraAiQueuePolicy,
    *,
    trigger: str,
    kind_hint: str | None,
    reply_language_binding: bool = False,
    prompt_parts: list[TiqoraAiPromptPart] | None = None,
) -> str:
    parts = [policy.system_prompt or ""]
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
) -> AgentRunResult:
    """Run the agent once for one ticket (plan §3.4 steps 1-12).

    ``run_id``/``worker_instance`` form the lock owner (``worker:run_id``).
    ``mcp_caller``/``kb_search_fn``/``kb_get_article_fn`` are injectable seams
    for tests; production omits them (real fastmcp/KB calls). ``vision_llm_factory``
    (a sync ``() -> LlmClient``) is an injectable seam for the attachment
    vision pre-pass — production omits it and the queue policy's
    ``vision_provider_id`` is resolved automatically; tests inject a fake to
    assert on the vision prompt without a real endpoint.
    """
    # 1. Readiness gate — auto-reply only (plan §3.0 v1.1 relaxation, Phase
    # E). Manual Assist always runs regardless of operation_mode: it only
    # ever produces a draft (see _map_customer_message below), never a
    # customer-visible send.
    if trigger == TRIGGER_AUTO:
        try:
            await require_feature_allowed(session, FEATURE_AUTO_REPLY)
        except AiGateError as exc:
            raise AgentRunError(str(exc)) from exc

    # 2. Per-ticket lock
    lock_owner = f"{worker_instance}:{run_id}"
    await _acquire_lock(session, ticket_id, lock_owner)

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

        # 4. Load ticket + articles; based_on_article_id = latest customer article
        articles = await load_articles(session, ticket_id)
        customer_articles = [a for a in articles if a.sender_type == "customer"]
        based_on_article_id = customer_articles[-1].id if customer_articles else None

        # 5. AI-content filter is applied when rendering (labels own AI output,
        # see _build_user_message) — nothing is physically removed.

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
        llm = AuditingLlmClient(
            llm, settings=settings, context=audit_context, session=session, pii_mapper=pii
        )
        reply_language_line = _resolve_reply_language_line(policy, ticket, customer_articles)
        system_prompt = _build_system_prompt(
            policy,
            trigger=trigger,
            kind_hint=kind_hint,
            reply_language_binding=reply_language_line is not None,
            prompt_parts=prompt_parts,
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
        registry = ToolRegistry(
            autonomy=policy.autonomy, mcp_tools=mcp_tools, kb_enabled=kb_enabled
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
        )

        messages: list[LlmMessage] = [
            LlmMessage(role="system", content=system_prompt),
            LlmMessage(role="user", content=user_message),
        ]
        schemas = registry.build_schemas()

        prompt_tokens = attachment_context.vision_usage.prompt_tokens
        completion_tokens = attachment_context.vision_usage.completion_tokens
        outcome: ToolOutcome | None = None

        # 9. Tool loop
        for _round in range(max_tool_rounds):
            response: LlmResponse = await llm.chat(messages=messages, tools=schemas)
            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens

            if not response.tool_calls:
                # Model produced plain text with no terminal tool call: it
                # never gets a customer-facing path (no send-tool exists), so
                # the run ends without a proposal.
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

        await usage_service.record_usage(
            session,
            user_id=acting_user_id if trigger == TRIGGER_MANUAL else None,
            queue_id=ticket.queue_id,
            ticket_id=ticket_id,
            feature=feature,
            provider_id=policy.llm_provider_id,
            model=policy.model_override,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            extra_json=json.dumps({"tool_trace": "masked_in_messages"}),
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

        to_address = None
        if based_on_article_id is not None:
            src = next((a for a in articles if a.id == based_on_article_id), None)
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
    except AgentRunError as exc:
        try:
            error_state = await session.get(TiqoraAiTicketState, ticket_id)
            if error_state is not None:
                error_state.last_error = str(exc)
                await session.commit()
        except Exception:  # noqa: BLE001 — best-effort bookkeeping only
            logger.exception("ai_runtime_error_bookkeeping_failed", ticket_id=ticket_id)
        raise
    finally:
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
