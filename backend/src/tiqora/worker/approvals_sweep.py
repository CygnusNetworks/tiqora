"""Worker wrapper of the approval sweeper (daemon ``approvals``)."""

from __future__ import annotations

from tiqora.approvals.sweep import run_approvals_sweep
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_APPROVALS_ENABLED, get_setting_bool
from tiqora.events.pubsub import get_pubsub_redis


async def run_approvals_sweep_worker_tick(settings: Settings) -> dict[str, int]:
    """One tick; ``{"skipped": 1}`` when the service is switched off (default ON)."""
    factory = get_session_factory()
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_APPROVALS_ENABLED, True)
    if not enabled:
        return {"skipped": 1}
    return await run_approvals_sweep(
        settings=settings, session_factory=factory, redis_client=get_pubsub_redis(settings)
    )
