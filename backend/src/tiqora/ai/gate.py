"""Readiness-Gate (plan §3.0, relaxed in v1.1 / Phase E): the AI subsystem's
**auto-reply** feature may only be enabled once the operator has switched the
install to Tiqora-primary operation (mail ingestion fully on Tiqora, Znuny
read-only/off). This is a deliberate operator decision via
``system.operation_mode``, never auto-detected from running processes.

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

Switching back to ``parallel`` is always allowed (regression must never be
blocked) and pauses auto-reply only.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import FEATURE_AUTO_REPLY
from tiqora.domain.settings_store import KEY_OPERATION_MODE, get_setting, set_setting

OPERATION_MODE_PARALLEL = "parallel"
OPERATION_MODE_TIQORA_PRIMARY = "tiqora_primary"
VALID_OPERATION_MODES = frozenset({OPERATION_MODE_PARALLEL, OPERATION_MODE_TIQORA_PRIMARY})


class AiGateError(RuntimeError):
    """Raised when an AI feature is enabled/run while the gate is not open."""


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


async def require_feature_allowed(session: AsyncSession, feature: str) -> None:
    """Feature-scoped Readiness-Gate (plan §3.0 v1.1 relaxation, Phase E).

    Only :data:`~tiqora.ai.models.FEATURE_AUTO_REPLY` requires
    ``operation_mode=tiqora_primary`` — see the module docstring for why.
    ``manual_assist`` and ``summary`` always pass, gate or no gate.
    """
    if feature == FEATURE_AUTO_REPLY:
        await require_tiqora_primary(session)


__all__ = [
    "OPERATION_MODE_PARALLEL",
    "OPERATION_MODE_TIQORA_PRIMARY",
    "VALID_OPERATION_MODES",
    "AiGateError",
    "get_operation_mode",
    "is_tiqora_primary",
    "require_feature_allowed",
    "require_tiqora_primary",
    "set_operation_mode",
]
