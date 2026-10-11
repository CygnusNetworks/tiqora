"""Worker wrapper of the web chat sweep (``webchat_sweep`` service)."""

from __future__ import annotations

from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_WEBCHAT_SWEEP_ENABLED, get_setting_bool
from tiqora.events.pubsub import get_pubsub_redis
from tiqora.webchat.sweep import run_webchat_sweep_tick


async def run_webchat_worker_tick(settings: Settings) -> dict[str, int]:
    """One tick; ``{"skipped": 1}`` when the service is switched off (default ON)."""
    factory = get_session_factory()
    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_WEBCHAT_SWEEP_ENABLED, True)
    if not enabled:
        return {"skipped": 1}
    return await run_webchat_sweep_tick(
        session_factory=factory, redis_client=get_pubsub_redis(settings)
    )
