"""Worker wrapper of the incremental report-data loop (``report_facts`` service)."""

from __future__ import annotations

from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_REPORT_FACTS_ENABLED, get_setting_bool
from tiqora.reporting.incremental import run_report_facts_tick


async def run_report_facts_worker_tick(settings: Settings) -> dict[str, int]:
    """One tick; ``{"skipped": 1}`` when the service is switched off (default ON)."""
    factory = get_session_factory()
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_REPORT_FACTS_ENABLED, True)
    if not enabled:
        return {"skipped": 1}
    return await run_report_facts_tick(settings=settings, session_factory=factory)
