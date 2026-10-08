"""Admin switches for automatic customer pictures (settings section)."""

from __future__ import annotations

from pydantic import BaseModel

from tiqora.domain.settings_store import KEY_AVATARS_FAVICON_ENABLED, KEY_AVATARS_GRAVATAR_ENABLED
from tiqora.settings_sections.section import SettingsSection


class AvatarSettings(BaseModel):
    gravatar_enabled: bool = True
    favicon_enabled: bool = True


AVATARS_SECTION = SettingsSection(
    key="avatars",
    model=AvatarSettings,
    title_key="admin.settings.avatars.title",
    i18n_prefix="admin.settings.avatars",
    storage_keys={
        "gravatar_enabled": KEY_AVATARS_GRAVATAR_ENABLED,
        "favicon_enabled": KEY_AVATARS_FAVICON_ENABLED,
    },
)
