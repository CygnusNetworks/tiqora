"""Effective on/off state of the customer portal.

Two configuration levels, one decision point: the deployment-level
``TIQORA_PORTAL_ENABLED`` is a hard off that no database row can override;
otherwise ``portal.enabled`` in ``tiqora_settings`` decides. Both default to
enabled, so existing installations are unaffected.

FastAPI wiring lives in ``tiqora.api.portal.deps.require_portal_enabled`` —
this module stays free of web-layer imports.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.settings_store import KEY_PORTAL_ENABLED, get_setting_bool


def portal_locked_by_env(settings: Settings) -> bool:
    """True when the deployment forces the portal off (admin switch is moot)."""
    return not settings.portal_enabled


async def portal_enabled(session: AsyncSession, settings: Settings) -> bool:
    """The one place that decides whether the customer portal is available."""
    if portal_locked_by_env(settings):
        return False
    return await get_setting_bool(session, KEY_PORTAL_ENABLED, default=True)
