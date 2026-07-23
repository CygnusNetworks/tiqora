"""Queue AI policy service (plan §3.1) — CRUD + Readiness-Gate enforcement.

The gate (``tiqora.ai.gate.require_feature_allowed``) is only checked on the
*enabling* transition of ``enabled_auto_reply``, and only for that flag —
plan §3.0 v1.1 relaxation (Phase E) allows ``enabled_summary`` /
``enabled_manual_assist`` to be enabled regardless of ``operation_mode``,
since drafts and summaries write nothing Sync-relevant (see
``tiqora.ai.gate`` module docstring). Turning ``enabled_auto_reply`` off, or
leaving it unchanged, never raises — regression to ``parallel`` must never be
blocked (plan §3.0).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.escalation import EscalationRuleError, validate_escalation_rules
from tiqora.ai.gate import require_feature_allowed
from tiqora.ai.models import (
    AUTONOMY_MODES,
    FEATURE_AUTO_REPLY,
    IDENTITY_MODES,
    PROMPT_PART_KINDS,
    REPLY_LANGUAGE_AUTO,
    REPLY_LANGUAGE_FIXED,
    REPLY_LANGUAGE_MODES,
    TiqoraAiPromptPart,
    TiqoraAiQueuePolicy,
    TiqoraLlmProvider,
)


class QueuePolicyValidationError(ValueError):
    """Raised for 422-shaped input errors (bad autonomy value, missing
    service_user_id/provider on auto-reply enable, etc.)."""


async def list_queue_policies(session: AsyncSession) -> list[TiqoraAiQueuePolicy]:
    rows = (
        (await session.execute(select(TiqoraAiQueuePolicy).order_by(TiqoraAiQueuePolicy.queue_id)))
        .scalars()
        .all()
    )
    return list(rows)


async def get_queue_policy(session: AsyncSession, policy_id: int) -> TiqoraAiQueuePolicy | None:
    return await session.get(TiqoraAiQueuePolicy, policy_id)


async def get_queue_policy_by_queue(
    session: AsyncSession, queue_id: int
) -> TiqoraAiQueuePolicy | None:
    return (
        await session.execute(
            select(TiqoraAiQueuePolicy).where(TiqoraAiQueuePolicy.queue_id == queue_id)
        )
    ).scalar_one_or_none()


async def load_prompt_parts(session: AsyncSession, policy_id: int) -> list[TiqoraAiPromptPart]:
    """All prompt parts for ``policy_id`` (enabled and disabled), ordered by
    ``position``. Callers filter for ``enabled`` where that matters (the
    runtime's effective-prompt composition); the admin CRUD/reorder views
    need the full set."""
    rows = (
        (
            await session.execute(
                select(TiqoraAiPromptPart)
                .where(TiqoraAiPromptPart.policy_id == policy_id)
                .order_by(TiqoraAiPromptPart.position)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


class PromptPartValidationError(ValueError):
    """Raised for 422-shaped prompt-part input errors (bad ``kind``, content
    too large, reorder id-set mismatch)."""


PROMPT_PART_CONTENT_MAX_LEN = 262_144  # 256 KB


def _validate_prompt_part_fields(*, kind: str | None, content: str | None) -> None:
    if kind is not None and kind not in PROMPT_PART_KINDS:
        raise PromptPartValidationError(
            f"Invalid kind: {kind!r} (expected one of {sorted(PROMPT_PART_KINDS)})"
        )
    if content is not None and len(content) > PROMPT_PART_CONTENT_MAX_LEN:
        raise PromptPartValidationError(
            f"content exceeds the maximum length of {PROMPT_PART_CONTENT_MAX_LEN} characters"
        )


async def create_prompt_part(
    session: AsyncSession,
    *,
    change_by: int,
    policy_id: int,
    kind: str,
    title: str,
    content: str,
) -> TiqoraAiPromptPart:
    """Appends a new part at the end (``max(position) + 1``, or ``0`` if the
    policy has no parts yet)."""
    _validate_prompt_part_fields(kind=kind, content=content)
    max_position = (
        await session.execute(
            select(func.max(TiqoraAiPromptPart.position)).where(
                TiqoraAiPromptPart.policy_id == policy_id
            )
        )
    ).scalar_one_or_none()
    row = TiqoraAiPromptPart(
        policy_id=policy_id,
        kind=kind,
        title=title,
        content=content,
        position=(max_position + 1) if max_position is not None else 0,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_prompt_part(session: AsyncSession, part_id: int) -> TiqoraAiPromptPart | None:
    return await session.get(TiqoraAiPromptPart, part_id)


async def update_prompt_part(
    session: AsyncSession,
    row: TiqoraAiPromptPart,
    *,
    change_by: int,
    **fields: Any,
) -> TiqoraAiPromptPart:
    """Partial update (title/content/enabled). ``fields`` keys must be model
    attribute names; only keys present are applied."""
    _validate_prompt_part_fields(
        kind=fields.get("kind"),
        content=fields.get("content"),
    )
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.change_by = change_by
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_prompt_part(session: AsyncSession, row: TiqoraAiPromptPart) -> None:
    await session.delete(row)
    await session.commit()


async def reorder_prompt_parts(
    session: AsyncSession, *, policy_id: int, change_by: int, ordered_ids: list[int]
) -> list[TiqoraAiPromptPart]:
    """Re-assigns ``position`` 0..N-1 following ``ordered_ids``. ``ordered_ids``
    must be exactly the set of existing part ids for ``policy_id`` (any
    mismatch — missing, extra, or duplicate ids — raises)."""
    rows = await load_prompt_parts(session, policy_id)
    existing_ids = {row.id for row in rows}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != existing_ids:
        raise PromptPartValidationError(
            "reorder ids must be exactly the set of existing prompt part ids "
            f"for policy {policy_id} (no duplicates, no missing/extra ids)"
        )
    by_id = {row.id: row for row in rows}
    now = datetime.now(UTC).replace(tzinfo=None)
    for position, part_id in enumerate(ordered_ids):
        part = by_id[part_id]
        part.position = position
        part.change_by = change_by
        part.change_time = now
    await session.commit()
    return await load_prompt_parts(session, policy_id)


def _validate_escalation_rules_json(raw: str | None) -> None:
    if raw is None or raw == "":
        return
    import json

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise QueuePolicyValidationError(f"escalation_rules is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise QueuePolicyValidationError("escalation_rules must be a JSON array")
    try:
        validate_escalation_rules(parsed)
    except EscalationRuleError as exc:
        raise QueuePolicyValidationError(f"escalation_rules: {exc}") from exc


def _validate_fields(
    *,
    autonomy: str | None,
    identity_mode: str | None,
    enabled_auto_reply: bool | None,
    service_user_id: int | None,
    llm_provider_id: int | None,
    reply_language_mode: str | None,
    reply_language_fixed: str | None,
    reply_language_default: str | None,
) -> None:
    if autonomy is not None and autonomy not in AUTONOMY_MODES:
        raise QueuePolicyValidationError(
            f"Invalid autonomy: {autonomy!r} (expected one of {sorted(AUTONOMY_MODES)})"
        )
    if identity_mode is not None and identity_mode not in IDENTITY_MODES:
        raise QueuePolicyValidationError(
            f"Invalid identity_mode: {identity_mode!r} (expected one of {sorted(IDENTITY_MODES)})"
        )
    if enabled_auto_reply:
        if service_user_id is None:
            raise QueuePolicyValidationError(
                "enabled_auto_reply=true requires service_user_id to be set"
            )
        if llm_provider_id is None:
            raise QueuePolicyValidationError(
                "enabled_auto_reply=true requires llm_provider_id to be set"
            )
    if reply_language_mode is not None and reply_language_mode not in REPLY_LANGUAGE_MODES:
        raise QueuePolicyValidationError(
            f"Invalid reply_language_mode: {reply_language_mode!r} "
            f"(expected one of {sorted(REPLY_LANGUAGE_MODES)})"
        )
    if reply_language_mode == REPLY_LANGUAGE_FIXED and not reply_language_fixed:
        raise QueuePolicyValidationError(
            "reply_language_mode=fixed requires reply_language_fixed to be set"
        )
    if reply_language_mode == REPLY_LANGUAGE_AUTO and not reply_language_default:
        raise QueuePolicyValidationError(
            "reply_language_mode=auto requires reply_language_default to be set"
        )


async def _validate_vision_provider(session: AsyncSession, vision_provider_id: int | None) -> None:
    if vision_provider_id is None:
        return
    provider = await session.get(TiqoraLlmProvider, vision_provider_id)
    if provider is None:
        raise QueuePolicyValidationError(f"vision_provider_id {vision_provider_id} does not exist")
    if not provider.supports_vision:
        raise QueuePolicyValidationError(
            f"Provider {vision_provider_id!r} does not have supports_vision enabled"
        )


async def _enforce_gate_on_enable(
    session: AsyncSession,
    *,
    previous: TiqoraAiQueuePolicy | None,
    enabled_auto_reply: bool | None,
    enabled_summary: bool | None,
    enabled_manual_assist: bool | None,
) -> None:
    """Only re-check the gate when ``enabled_auto_reply`` is *becoming* true.

    ``enabled_summary``/``enabled_manual_assist`` are accepted unconditionally
    (plan §3.0 v1.1 relaxation) — the parameters are still passed in so the
    calling code doesn't have to know which flag is gated.
    """
    _ = enabled_summary, enabled_manual_assist
    prev_auto = bool(previous.enabled_auto_reply) if previous else False
    if enabled_auto_reply is True and not prev_auto:
        await require_feature_allowed(session, FEATURE_AUTO_REPLY)


async def create_queue_policy(
    session: AsyncSession,
    *,
    change_by: int,
    queue_id: int,
    enabled_auto_reply: bool = False,
    enabled_summary: bool = False,
    enabled_manual_assist: bool = False,
    system_prompt: str = "",
    autonomy: str = "off",
    service_user_id: int | None = None,
    llm_provider_id: int | None = None,
    model_override: str | None = None,
    vision_provider_id: int | None = None,
    kb_tags: str | None = None,
    kb_category_ids: str | None = None,
    mcp_client_ids: str | None = None,
    mcp_tool_overrides: str | None = None,
    summary_article_threshold: int | None = None,
    summary_char_threshold: int | None = None,
    summary_incremental_min_articles: int | None = None,
    summary_incremental_min_chars: int | None = None,
    max_clarifications: int = 2,
    max_auto_replies: int = 5,
    max_replies_per_hour: int | None = None,
    budget_tokens_day: int | None = None,
    escalation_rules: str | None = None,
    ai_disclosure_enabled: bool = False,
    ai_disclosure_text: str | None = None,
    pii_masking: bool = True,
    identity_mode: str = "ticket_customer_id",
    clarify_schema_json: str | None = None,
    ignored_senders: str | None = None,
    ignore_senders_manual: bool = False,
    reply_language_mode: str = "off",
    reply_language_fixed: str | None = None,
    reply_language_default: str | None = None,
    allowed_state_types: str | None = None,
) -> TiqoraAiQueuePolicy:
    _validate_fields(
        autonomy=autonomy,
        identity_mode=identity_mode,
        enabled_auto_reply=enabled_auto_reply,
        service_user_id=service_user_id,
        llm_provider_id=llm_provider_id,
        reply_language_mode=reply_language_mode,
        reply_language_fixed=reply_language_fixed,
        reply_language_default=reply_language_default,
    )
    _validate_escalation_rules_json(escalation_rules)
    await _validate_vision_provider(session, vision_provider_id)
    await _enforce_gate_on_enable(
        session,
        previous=None,
        enabled_auto_reply=enabled_auto_reply,
        enabled_summary=enabled_summary,
        enabled_manual_assist=enabled_manual_assist,
    )

    row = TiqoraAiQueuePolicy(
        queue_id=queue_id,
        enabled_auto_reply=enabled_auto_reply,
        enabled_summary=enabled_summary,
        enabled_manual_assist=enabled_manual_assist,
        system_prompt=system_prompt,
        autonomy=autonomy,
        service_user_id=service_user_id,
        llm_provider_id=llm_provider_id,
        model_override=model_override,
        vision_provider_id=vision_provider_id,
        kb_tags=kb_tags,
        kb_category_ids=kb_category_ids,
        mcp_client_ids=mcp_client_ids,
        mcp_tool_overrides=mcp_tool_overrides,
        summary_article_threshold=summary_article_threshold,
        summary_char_threshold=summary_char_threshold,
        summary_incremental_min_articles=summary_incremental_min_articles,
        summary_incremental_min_chars=summary_incremental_min_chars,
        max_clarifications=max_clarifications,
        max_auto_replies=max_auto_replies,
        max_replies_per_hour=max_replies_per_hour,
        budget_tokens_day=budget_tokens_day,
        escalation_rules=escalation_rules,
        ai_disclosure_enabled=ai_disclosure_enabled,
        ai_disclosure_text=ai_disclosure_text,
        pii_masking=pii_masking,
        identity_mode=identity_mode,
        clarify_schema_json=clarify_schema_json,
        ignored_senders=ignored_senders,
        ignore_senders_manual=ignore_senders_manual,
        reply_language_mode=reply_language_mode,
        reply_language_fixed=reply_language_fixed,
        reply_language_default=reply_language_default,
        allowed_state_types=allowed_state_types,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_queue_policy(
    session: AsyncSession,
    row: TiqoraAiQueuePolicy,
    *,
    change_by: int,
    **fields: Any,
) -> TiqoraAiQueuePolicy:
    """Partial update. ``fields`` keys must be model attribute names; only
    keys present are applied (caller passes ``model_dump(exclude_unset=True)``)."""
    effective_autonomy = fields.get("autonomy", row.autonomy)
    effective_identity_mode = fields.get("identity_mode", row.identity_mode)
    effective_enabled_auto_reply = fields.get("enabled_auto_reply", row.enabled_auto_reply)
    effective_service_user_id = fields.get("service_user_id", row.service_user_id)
    effective_llm_provider_id = fields.get("llm_provider_id", row.llm_provider_id)
    effective_reply_language_mode = fields.get("reply_language_mode", row.reply_language_mode)
    effective_reply_language_fixed = fields.get("reply_language_fixed", row.reply_language_fixed)
    effective_reply_language_default = fields.get(
        "reply_language_default", row.reply_language_default
    )

    _validate_fields(
        autonomy=effective_autonomy,
        identity_mode=effective_identity_mode,
        enabled_auto_reply=effective_enabled_auto_reply,
        service_user_id=effective_service_user_id,
        llm_provider_id=effective_llm_provider_id,
        reply_language_mode=effective_reply_language_mode,
        reply_language_fixed=effective_reply_language_fixed,
        reply_language_default=effective_reply_language_default,
    )
    if "escalation_rules" in fields:
        _validate_escalation_rules_json(fields.get("escalation_rules"))
    if "vision_provider_id" in fields:
        await _validate_vision_provider(session, fields.get("vision_provider_id"))
    await _enforce_gate_on_enable(
        session,
        previous=row,
        enabled_auto_reply=fields.get("enabled_auto_reply"),
        enabled_summary=fields.get("enabled_summary"),
        enabled_manual_assist=fields.get("enabled_manual_assist"),
    )

    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.change_by = change_by
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_queue_policy(session: AsyncSession, row: TiqoraAiQueuePolicy) -> None:
    await session.delete(row)
    await session.commit()


__all__ = [
    "PROMPT_PART_CONTENT_MAX_LEN",
    "PromptPartValidationError",
    "QueuePolicyValidationError",
    "create_prompt_part",
    "create_queue_policy",
    "delete_prompt_part",
    "delete_queue_policy",
    "get_prompt_part",
    "get_queue_policy",
    "get_queue_policy_by_queue",
    "list_queue_policies",
    "load_prompt_parts",
    "reorder_prompt_parts",
    "update_prompt_part",
    "update_queue_policy",
]
