"""Daily GDPR erasure backup purge — default ON, no ownership gate required.

Deletes ``tiqora_gdpr_backup`` rows for jobs whose ``backup_expires_at`` has
passed and flips those jobs to ``status=purged``. Safe to schedule always;
gated only by the ``gdpr.erasure.purge_enabled`` setting (default true).
"""

from __future__ import annotations

import structlog

from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_GDPR_ERASURE_PURGE_ENABLED, get_setting_bool
from tiqora.gdpr.erasure import purge_expired_backups

logger = structlog.get_logger(__name__)


async def run_gdpr_erasure_purge_tick() -> dict[str, int]:
    """One scheduler tick: purge expired GDPR erasure backups when enabled."""
    factory = get_session_factory()

    async with factory() as session:
        # Default ON — only skip when explicitly disabled.
        enabled = await get_setting_bool(session, KEY_GDPR_ERASURE_PURGE_ENABLED, True)
    if not enabled:
        logger.debug("gdpr_erasure_purge_disabled")
        return {"enabled": 0}

    result = await purge_expired_backups(factory)
    logger.info(
        "gdpr_erasure_purge_tick",
        purged_jobs=result.get("purged_jobs", 0),
        deleted_backups=result.get("deleted_backups", 0),
    )
    return {"enabled": 1, **result}
