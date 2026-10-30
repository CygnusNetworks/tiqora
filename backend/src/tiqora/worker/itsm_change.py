"""Worker wrapper of the change impact refresh (``itsm_change`` service)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_ITSM_CHANGE_ENABLED, get_setting_bool
from tiqora.itsm.change.impact import refresh_stale_impacts
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.mode import get_itsm_mode


async def run_itsm_change_tick_in(session: AsyncSession, settings: Settings) -> dict[str, int]:
    """One tick in the given session (commits): queues the impact jobs of stale active changes.

    ``{"skipped": 1}`` while the service is switched off or the ITSM mode is not ``tiqora``.
    """
    if not await get_setting_bool(session, KEY_ITSM_CHANGE_ENABLED, True):
        return {"skipped": 1}
    if (await get_itsm_mode(session)).mode != "tiqora":
        return {"skipped": 1}
    queued = await refresh_stale_impacts(session, await load_change_settings(session, settings))
    await session.commit()
    return {"queued": queued}


async def run_itsm_change_tick(settings: Settings) -> dict[str, int]:
    async with get_session_factory()() as session:
        return await run_itsm_change_tick_in(session, settings)
