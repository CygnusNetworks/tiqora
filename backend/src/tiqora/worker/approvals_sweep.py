"""Worker wrapper of the approval sweeper (daemon ``approvals``)."""

from __future__ import annotations

from datetime import datetime

from tiqora.approvals.sweep import run_approvals_sweep, track_timers
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_APPROVALS_ENABLED, get_setting_bool
from tiqora.events.pubsub import get_pubsub_redis


async def run_approvals_sweep_worker_tick(
    settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    """One tick; ``{"skipped": 1}`` when the service is switched off (default ON), which only keeps
    the pause bookkeeping of the module switches going."""
    factory = get_session_factory()
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_APPROVALS_ENABLED, True)
    if not enabled:
        await track_timers(session_factory=factory, now=now)
        return {"skipped": 1}
    return await run_approvals_sweep(
        settings=settings, session_factory=factory, now=now, redis_client=get_pubsub_redis(settings)
    )
