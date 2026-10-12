"""Worker wrappers of the report scheduler and the run-history purge."""

from __future__ import annotations

from datetime import timedelta

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_REPORT_RUNS_PURGE_ENABLED,
    KEY_REPORT_SCHEDULER_ENABLED,
    get_setting_bool,
)
from tiqora.jobs.service import utc_now
from tiqora.reporting.scheduler import purge_report_runs, run_report_scheduler_tick
from tiqora.reporting.settings import load_reporting_settings


async def run_report_scheduler_worker_tick(settings: Settings) -> dict[str, int]:
    """One tick; ``{"skipped": 1}`` when the service is switched off (default ON)."""
    factory = get_session_factory()
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_REPORT_SCHEDULER_ENABLED, True)
    if not enabled:
        return {"skipped": 1}
    return await run_report_scheduler_tick(settings=settings, session_factory=factory)


async def run_report_runs_purge_tick(settings: Settings | None = None) -> dict[str, int]:
    """Delete runs older than ``run_retention_days``; ``{"skipped": 1}`` when switched off."""
    settings = settings or get_settings()
    factory = get_session_factory()
    async with factory() as session:
        if not await get_setting_bool(session, KEY_REPORT_RUNS_PURGE_ENABLED, True):
            return {"skipped": 1}
        reporting = await load_reporting_settings(session, settings)
        cutoff = utc_now() - timedelta(days=reporting.run_retention_days)
        purged = await purge_report_runs(session, older_than=cutoff)
        await session.commit()
    return {"purged": purged}
