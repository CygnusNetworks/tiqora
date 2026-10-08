"""Admin switches for automatic customer pictures — ``/api/v1/admin/avatars``.

Uploaded pictures are always served; these only control the outbound lookups
(Gravatar for people, website icons for companies). Backed by the ``avatars``
settings section, so the generic settings API shows the same values.
"""

from __future__ import annotations

from fastapi import APIRouter

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.avatars.settings import AVATARS_SECTION, AvatarSettings
from tiqora.settings_sections.store import load_section_values, save_section_values

router = APIRouter(prefix="/avatars", tags=["admin:avatars"])


async def _current(session: DbSession, secret_key: str) -> AvatarSettings:
    values = await load_section_values(session, AVATARS_SECTION, secret_key=secret_key)
    return AvatarSettings.model_validate(values.model_dump())


@router.get("", response_model=AvatarSettings)
async def get_avatar_settings(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> AvatarSettings:
    _ = admin
    return await _current(session, settings.secret_key)


@router.put("", response_model=AvatarSettings)
async def put_avatar_settings(
    body: AvatarSettings, admin: AdminUser, session: DbSession, settings: AppSettings
) -> AvatarSettings:
    _ = admin
    await save_section_values(
        session, AVATARS_SECTION, body.model_dump(), secret_key=settings.secret_key
    )
    await session.commit()
    return await _current(session, settings.secret_key)
