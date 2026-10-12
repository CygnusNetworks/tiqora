"""Daily GenericInterface debug log retention (service ``gi_debug_cleanup``).

Deletes ``tiqora_gi_debug_entry`` rows (content cascades) older than
``gi.debugger.retention_days`` (default 14, minimum 1). Always on: it only ever removes
disposable diagnostics.
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_GI_DEBUG_RETENTION_DAYS, get_setting_int
from tiqora.gi.debug_store import purge_debug_entries
from tiqora.jobs.service import utc_now

logger = structlog.get_logger(__name__)

DEFAULT_RETENTION_DAYS = 14


async def run_gi_debug_cleanup() -> dict[str, int]:
    """One scheduler tick: delete debugger rows past retention."""
    factory = get_session_factory()
    async with factory() as session:
        configured = await get_setting_int(
            session, KEY_GI_DEBUG_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        )
        retention_days = max(1, configured)
        deleted = await purge_debug_entries(
            session, older_than=utc_now() - timedelta(days=retention_days)
        )
    logger.info("gi_debug_cleanup_tick", retention_days=retention_days, deleted=deleted)
    return {"retention_days": retention_days, "deleted": deleted}
