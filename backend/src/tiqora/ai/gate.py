"""Readiness-Gate (plan §3.0, relaxed in v1.1 / Phase E) + auto-reply kill-switch.

The AI subsystem's **auto-reply** feature may only be enabled once the
operator has switched the install to Tiqora-primary operation (mail
ingestion fully on Tiqora, Znuny read-only/off). This is a deliberate
operator decision via ``system.operation_mode``, never auto-detected from
running processes.

Auto-reply sends a customer-visible article via the Tiqora outbox, which
Znuny does not observe while running in parallel — with Znuny's own
autoresponders still active, a customer could receive two answers. Manual
Assist (``tiqora_ai_draft`` is a distinct entity, never an article, always
reviewed by a human before anything is sent) and Summaries (state-only,
``tiqora_ai_ticket_state``, pull-based) write nothing Sync-relevant and are
therefore **not** gated — see :func:`require_feature_allowed`.

Enforcement happens in two places, both required for ``auto_reply``:

1. Admin API (``tiqora.api.v1.admin.ai``) calls :func:`require_tiqora_primary`
   before allowing a queue policy to flip ``enabled_auto_reply`` to ``true``.
2. The AI runtime (:mod:`tiqora.ai.runtime`) re-checks the gate at the start
   of every ``trigger="auto"`` run; the auto-worker tick skips invoking it at
   all while the gate is closed (:mod:`tiqora.ai.auto_worker`).

Additionally, ``ai.auto_reply.paused`` is a global kill-switch (plan #10)
independent of ``operation_mode``: when true, auto-reply runs are blocked
even in ``tiqora_primary``. Toggle via Admin → AI settings.

Switching back to ``parallel`` is always allowed (regression must never be
blocked) and pauses auto-reply only.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import FEATURE_AUTO_REPLY
from tiqora.domain.settings_store import (
    KEY_AI_AUTO_REPLY_PAUSED,
    KEY_OPERATION_MODE,
    get_setting,
    get_setting_bool,
    set_setting,
)

OPERATION_MODE_PARALLEL = "parallel"
OPERATION_MODE_TIQORA_PRIMARY = "tiqora_primary"
VALID_OPERATION_MODES = frozenset({OPERATION_MODE_PARALLEL, OPERATION_MODE_TIQORA_PRIMARY})

# Channels with no Znuny counterpart at all — an auto-reply sent through the
# Tiqora outbox for one of these channels can never be "missed" by Znuny's
# own autoresponders, because Znuny never sees these messages in the first
# place (unlike email, which Znuny still ingests while running in parallel).
# Deliberately narrow (telegram only, for now); names match the channel
# name used in the ArticleCreate outbox payload / channels/common.py
# convention, always lowercase.
TIQORA_ONLY_CHANNELS: frozenset[str] = frozenset({"telegram"})


class AiGateError(RuntimeError):
    """Raised when an AI feature is enabled/run while the gate is not open."""


class AiAutoReplyPausedError(AiGateError):
    """Raised when the global auto-reply kill-switch is on (plan #10)."""


async def get_operation_mode(session: AsyncSession) -> str:
    """Return the current ``system.operation_mode`` (default ``parallel``)."""
    raw = await get_setting(session, KEY_OPERATION_MODE)
    if raw is None or raw.strip() not in VALID_OPERATION_MODES:
        return OPERATION_MODE_PARALLEL
    return raw.strip()


async def set_operation_mode(session: AsyncSession, mode: str) -> str:
    """Validate and persist ``system.operation_mode``.

    Raises :class:`ValueError` for unknown values — callers (admin API)
    translate that into a 422; this module has no HTTP dependency.
    """
    if mode not in VALID_OPERATION_MODES:
        raise ValueError(
            f"Invalid operation_mode: {mode!r} (expected one of {sorted(VALID_OPERATION_MODES)})"
        )
    await set_setting(session, KEY_OPERATION_MODE, mode)
    return mode


async def is_tiqora_primary(session: AsyncSession) -> bool:
    return await get_operation_mode(session) == OPERATION_MODE_TIQORA_PRIMARY


async def require_tiqora_primary(session: AsyncSession) -> None:
    """Raise :class:`AiGateError` unless ``operation_mode == tiqora_primary``.

    Switching a feature *off* (or reverting to ``parallel``) is always
    allowed regardless of this check — callers must only invoke this guard on
    the "enable" path, never on "disable".
    """
    if not await is_tiqora_primary(session):
        raise AiGateError(
            "Auto-reply requires operation_mode=tiqora_primary "
            "(sending would risk double-answering alongside Znuny's own autoresponders "
            "while running in parallel operation)"
        )


async def is_auto_reply_paused(session: AsyncSession) -> bool:
    """Return True when the global auto-reply kill-switch is engaged."""
    return await get_setting_bool(session, KEY_AI_AUTO_REPLY_PAUSED, default=False)


async def set_auto_reply_paused(session: AsyncSession, paused: bool) -> bool:
    """Persist the global auto-reply kill-switch."""
    await set_setting(session, KEY_AI_AUTO_REPLY_PAUSED, "true" if paused else "false")
    return paused


async def require_auto_reply_not_paused(session: AsyncSession) -> None:
    if await is_auto_reply_paused(session):
        raise AiAutoReplyPausedError(
            "Auto-reply is globally paused (ai.auto_reply.paused=true). "
            "Clear the kill-switch in AI settings to resume."
        )


async def queue_serves_tiqora_only_channel(session: AsyncSession, queue_id: int) -> bool:
    """True when *queue_id*'s queue name matches the configured ``queue_name``
    of any :data:`TIQORA_ONLY_CHANNELS` channel (e.g. Telegram) — such a queue
    has no Znuny counterpart to double-answer alongside, so it is exempt from
    :func:`require_tiqora_primary` (see :func:`require_feature_allowed`).
    """
    # Lazy import: tiqora.channels.common pulls in domain/ticket_write_service
    # and permissions.engine, which would otherwise create an import cycle
    # with tiqora.ai.gate at module load time.
    from tiqora.channels.common import channel_setting

    row = (
        await session.execute(text("SELECT name FROM queue WHERE id = :qid"), {"qid": queue_id})
    ).first()
    if row is None or not row[0]:
        return False
    queue_name = str(row[0])
    for channel in TIQORA_ONLY_CHANNELS:
        configured_queue_name = await channel_setting(session, channel, "queue_name")
        if configured_queue_name and configured_queue_name == queue_name:
            return True
    return False


async def require_feature_allowed(
    session: AsyncSession, feature: str, *, source_channel: str | None = None
) -> None:
    """Feature-scoped Readiness-Gate (plan §3.0 v1.1 relaxation, Phase E).

    Only :data:`~tiqora.ai.models.FEATURE_AUTO_REPLY` requires
    ``operation_mode=tiqora_primary`` and a clear kill-switch — see the
    module docstring for why. ``manual_assist`` and ``summary`` always pass.

    ``source_channel`` (the triggering article's channel, e.g. from the
    ArticleCreate outbox payload) skips the ``operation_mode`` check when it
    is one of :data:`TIQORA_ONLY_CHANNELS` (case-insensitive) — those
    channels have no Znuny counterpart to double-answer, so auto-reply may
    run in ``parallel`` operation for them too. The kill-switch
    (``ai.auto_reply.paused``) is never skipped, regardless of channel.
    """
    if feature == FEATURE_AUTO_REPLY:
        is_tiqora_only_channel = (
            source_channel is not None and source_channel.strip().lower() in TIQORA_ONLY_CHANNELS
        )
        if not is_tiqora_only_channel:
            await require_tiqora_primary(session)
        await require_auto_reply_not_paused(session)


__all__ = [
    "OPERATION_MODE_PARALLEL",
    "OPERATION_MODE_TIQORA_PRIMARY",
    "TIQORA_ONLY_CHANNELS",
    "VALID_OPERATION_MODES",
    "AiAutoReplyPausedError",
    "AiGateError",
    "get_operation_mode",
    "is_auto_reply_paused",
    "is_tiqora_primary",
    "queue_serves_tiqora_only_channel",
    "require_auto_reply_not_paused",
    "require_feature_allowed",
    "require_tiqora_primary",
    "set_auto_reply_paused",
    "set_operation_mode",
]
