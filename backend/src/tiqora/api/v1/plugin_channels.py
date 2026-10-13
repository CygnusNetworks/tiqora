"""Customer channels from plugins, for the reply composer: ``GET /api/v1/channels/plugin`` (D11)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tiqora.api.deps import CurrentUser
from tiqora.extensions.core import get_registry
from tiqora.plugins.channels import CHANNEL_DRIVERS
from tiqora.plugins.state import is_active

router = APIRouter(prefix="/channels/plugin", tags=["channels:plugin"])


class PluginChannelCapabilitiesOut(BaseModel):
    subject: bool
    html: bool
    attachments: bool
    max_length: int | None


class PluginChannelOut(BaseModel):
    key: str  # the ArticleIn.channel value
    name: str  # communication_channel.name
    label: str
    plugin_id: str
    capabilities: PluginChannelCapabilitiesOut
    can_reply: bool  # False = inbound only


@router.get("", response_model=list[PluginChannelOut])
async def list_plugin_channels(user: CurrentUser) -> list[PluginChannelOut]:
    """The channels of loaded, enabled plugins an agent can answer on or at least see."""
    return [
        PluginChannelOut(
            key=d.key,
            name=d.name,
            label=d.label,
            plugin_id=d.plugin_id,
            capabilities=PluginChannelCapabilitiesOut(
                subject=d.capabilities.subject,
                html=d.capabilities.html,
                attachments=d.capabilities.attachments,
                max_length=d.capabilities.max_length,
            ),
            can_reply=d.send is not None,
        )
        for d in sorted(get_registry().items(CHANNEL_DRIVERS), key=lambda d: d.key)
        if is_active(d.plugin_id)
    ]
