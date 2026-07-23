"""Daily LLM-audit-log retention cleanup — default ON.

Deletes ``tiqora_ai_audit_log`` rows older than the configured
``ai.audit.retention_days`` setting (default 30, see
:mod:`tiqora.ai.audit`). Gated only by ``daemon.ai_audit_cleanup.enabled``
(default true) — safe to schedule always.
"""

from __future__ import annotations

import structlog

from tiqora.ai.audit import DEFAULT_RETENTION_DAYS, cleanup_audit_log
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_AI_AUDIT_CLEANUP_ENABLED,
    KEY_AI_AUDIT_RETENTION_DAYS,
    get_setting_bool,
    get_setting_int,
)

logger = structlog.get_logger(__name__)


async def run_ai_audit_cleanup_tick() -> dict[str, int]:
    """One scheduler tick: delete audit rows past retention when enabled."""
    factory = get_session_factory()

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_AI_AUDIT_CLEANUP_ENABLED, True)
        if not enabled:
            logger.debug("ai_audit_cleanup_disabled")
            return {"enabled": 0}
        retention_days = await get_setting_int(
            session, KEY_AI_AUDIT_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        )
        deleted = await cleanup_audit_log(session, retention_days=retention_days)

    logger.info("ai_audit_cleanup_tick", retention_days=retention_days, deleted=deleted)
    return {"enabled": 1, "retention_days": retention_days, "deleted": deleted}
