"""Admin switches for automatic customer pictures — ``/api/v1/admin/avatars``.

Uploaded pictures are always served; these only control the outbound lookups
(Gravatar for people, website icons for companies).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.domain.settings_store import (
    KEY_AVATARS_FAVICON_ENABLED,
    KEY_AVATARS_GRAVATAR_ENABLED,
    get_setting_bool,
    set_setting,
)

router = APIRouter(prefix="/avatars", tags=["admin:avatars"])


class AvatarSettings(BaseModel):
    gravatar_enabled: bool = True
    favicon_enabled: bool = True


async def _current(session: DbSession) -> AvatarSettings:
    return AvatarSettings(
        gravatar_enabled=await get_setting_bool(
            session, KEY_AVATARS_GRAVATAR_ENABLED, default=True
        ),
        favicon_enabled=await get_setting_bool(session, KEY_AVATARS_FAVICON_ENABLED, default=True),
    )


@router.get("", response_model=AvatarSettings)
async def get_avatar_settings(admin: AdminUser, session: DbSession) -> AvatarSettings:
    _ = admin
    return await _current(session)


@router.put("", response_model=AvatarSettings)
async def put_avatar_settings(
    body: AvatarSettings, admin: AdminUser, session: DbSession
) -> AvatarSettings:
    _ = admin
    await set_setting(session, KEY_AVATARS_GRAVATAR_ENABLED, "1" if body.gravatar_enabled else "0")
    await set_setting(session, KEY_AVATARS_FAVICON_ENABLED, "1" if body.favicon_enabled else "0")
    return await _current(session)
