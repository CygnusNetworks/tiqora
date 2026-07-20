"""Admin config for the additional channel plugins (SMS, WhatsApp, Phone),
mounted at ``/api/v1/admin/channels``.

Config is stored in ``tiqora_settings`` (namespaced ``channel.<name>.<key>``,
see :mod:`tiqora.channels.common`) rather than a dedicated table — every
channel is a handful of key/value strings (enable flag, URLs, tokens,
secrets) and this avoids an extra Alembic migration chain for what is,
functionally, still just settings. Secret-shaped keys are redacted (masked)
on read so ``GET`` responses are safe to log/screenshot; write with the
same key name to rotate a secret.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.channels.common import channel_enabled, setting_key
from tiqora.domain.settings_store import get_setting, set_setting

router = APIRouter(prefix="/channels", tags=["admin:channels"])

# Allowed config keys per channel — anything else is rejected (this endpoint
# writes directly into tiqora_settings, so we don't want to accept arbitrary
# key names). "enabled" is handled separately from the other keys.
CHANNEL_CONFIG_KEYS: Final[dict[str, set[str]]] = {
    "sms": {
        "outbound_webhook_url",
        "outbound_shared_secret",
        "inbound_shared_secret",
        "default_customer_user",
        "queue_name",
    },
    "whatsapp": {
        "phone_number_id",
        "access_token",
        "app_secret",
        "verify_token",
        "api_version",
        "default_customer_user",
        "queue_name",
    },
    "phone": {
        "inbound_shared_secret",
        "default_customer_user",
        "queue_name",
    },
}

_SECRET_KEY_MARKERS: Final[tuple[str, ...]] = ("secret", "token")


def _is_secret_key(key: str) -> bool:
    return any(marker in key for marker in _SECRET_KEY_MARKERS)


class ChannelConfigOut(BaseModel):
    channel: str
    enabled: bool
    config: dict[str, str | None]


class ChannelConfigUpdate(BaseModel):
    enabled: bool | None = None
    config: dict[str, str] = {}


def _require_known_channel(channel: str) -> set[str]:
    keys = CHANNEL_CONFIG_KEYS.get(channel)
    if keys is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown channel: {channel!r}"
        )
    return keys


async def _read_config(session: DbSession, channel: str) -> ChannelConfigOut:
    keys = _require_known_channel(channel)
    enabled = await channel_enabled(session, channel)
    config: dict[str, str | None] = {}
    for key in sorted(keys):
        value = await get_setting(session, setting_key(channel, key))
        if value and _is_secret_key(key):
            value = "********"
        config[key] = value
    return ChannelConfigOut(channel=channel, enabled=enabled, config=config)


@router.get("", response_model=list[ChannelConfigOut])
async def list_channels(admin: AdminUser, session: DbSession) -> list[ChannelConfigOut]:
    _ = admin
    return [await _read_config(session, name) for name in sorted(CHANNEL_CONFIG_KEYS)]


@router.get("/{channel}", response_model=ChannelConfigOut)
async def get_channel(channel: str, admin: AdminUser, session: DbSession) -> ChannelConfigOut:
    _ = admin
    return await _read_config(session, channel)


@router.put("/{channel}", response_model=ChannelConfigOut)
async def update_channel(
    channel: str, body: ChannelConfigUpdate, admin: AdminUser, session: DbSession
) -> ChannelConfigOut:
    _ = admin
    allowed_keys = _require_known_channel(channel)

    unknown = set(body.config) - allowed_keys
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown config key(s) for {channel!r}: {sorted(unknown)}",
        )

    if body.enabled is not None:
        await set_setting(session, setting_key(channel, "enabled"), "1" if body.enabled else "0")
    for key, value in body.config.items():
        await set_setting(session, setting_key(channel, key), value)

    return await _read_config(session, channel)
