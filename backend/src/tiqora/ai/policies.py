"""Queue AI policy service (plan §3.1) — CRUD + Readiness-Gate enforcement.

The gate (``tiqora.ai.gate.require_tiqora_primary``) is only checked on the
*enabling* transition of ``enabled_auto_reply`` / ``enabled_summary`` /
``enabled_manual_assist``: turning a flag off, or leaving it unchanged, never
raises — regression to ``parallel`` must never be blocked (plan §3.0).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.gate import require_tiqora_primary
from tiqora.ai.models import AUTONOMY_MODES, IDENTITY_MODES, TiqoraAiQueuePolicy


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


def _validate_fields(
    *,
    autonomy: str | None,
    identity_mode: str | None,
    enabled_auto_reply: bool | None,
    service_user_id: int | None,
    llm_provider_id: int | None,
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


async def _enforce_gate_on_enable(
    session: AsyncSession,
    *,
    previous: TiqoraAiQueuePolicy | None,
    enabled_auto_reply: bool | None,
    enabled_summary: bool | None,
    enabled_manual_assist: bool | None,
) -> None:
    """Only re-check the gate for flags that are *becoming* true."""
    prev_auto = bool(previous.enabled_auto_reply) if previous else False
    prev_summary = bool(previous.enabled_summary) if previous else False
    prev_manual = bool(previous.enabled_manual_assist) if previous else False

    turning_on = (
        (enabled_auto_reply is True and not prev_auto)
        or (enabled_summary is True and not prev_summary)
        or (enabled_manual_assist is True and not prev_manual)
    )
    if turning_on:
        await require_tiqora_primary(session)


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
) -> TiqoraAiQueuePolicy:
    _validate_fields(
        autonomy=autonomy,
        identity_mode=identity_mode,
        enabled_auto_reply=enabled_auto_reply,
        service_user_id=service_user_id,
        llm_provider_id=llm_provider_id,
    )
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

    _validate_fields(
        autonomy=effective_autonomy,
        identity_mode=effective_identity_mode,
        enabled_auto_reply=effective_enabled_auto_reply,
        service_user_id=effective_service_user_id,
        llm_provider_id=effective_llm_provider_id,
    )
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
    "QueuePolicyValidationError",
    "create_queue_policy",
    "delete_queue_policy",
    "get_queue_policy",
    "get_queue_policy_by_queue",
    "list_queue_policies",
    "update_queue_policy",
]
