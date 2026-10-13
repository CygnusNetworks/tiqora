"""Daily CMDB housekeeping: queue the version archive job and drop stale staged uploads."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

import structlog

from tiqora.cmdb.archive import ARCHIVE_JOB_KIND, ArchivePayload
from tiqora.cmdb.attachments import purge_stale_staged
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.db.engine import get_session_factory
from tiqora.jobs.service import enqueue_job, utc_now

logger = structlog.get_logger(__name__)

#: staged (unbound) CI uploads older than this are removed
STAGED_MAX_AGE: Final = timedelta(hours=24)


async def run_cmdb_version_archive_tick() -> dict[str, int]:
    """One tick: purge stale staged attachments always; enqueue the archive job when enabled."""
    factory = get_session_factory()
    async with factory() as session:
        settings = await load_cmdb_settings(session)
        purged = await purge_stale_staged(session, utc_now() - STAGED_MAX_AGE)
        enqueued = 0
        if settings.version_archive_enabled:
            await enqueue_job(
                session,
                kind=ARCHIVE_JOB_KIND,
                payload=ArchivePayload(),
                created_by=None,
                dedupe_key="daily",
            )
            enqueued = 1
        await session.commit()
    logger.info("cmdb_version_archive_tick", purged_staged=purged, enqueued=enqueued)
    return {"purged_staged": purged, "enqueued": enqueued}
