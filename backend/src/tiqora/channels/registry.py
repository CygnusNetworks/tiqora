"""Channel extension point: which channels exist and which config keys they own."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tiqora.extensions.registry import ExtensionPoint

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
        "dial_scheme",
        "default_customer_user",
        "queue_name",
        "originate_enabled",
        "originate_ari_url",
        "originate_ari_user",
        "originate_ari_secret",
        "originate_context",
        "originate_endpoint",
        "originate_timeout",
        "originate_internal",
        "originate_region",
        "originate_cti",
    },
    "telegram": {
        "bot_token",
        "queue_name",
        "default_customer_user",
        "mode",
        "webhook_url",
        "webhook_secret_token",
        "consent_required",
        "consent_text",
        "consent_confirmed_text",
        "start_text",
        "tone_prompt",
        "consent_button_label",
        "identity_no_match_text",
        "identity_handoff_text",
        "followup_reopen_days",
    },
    # Web chat: only the master switch ``channel.webchat.enabled``; everything else lives in
    # ``tiqora_chat_config`` (one row per widget).
    "webchat": set(),
}


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    key: str
    config_keys: frozenset[str]


CHANNELS: ExtensionPoint[ChannelDescriptor] = ExtensionPoint(
    key="core.channels",
    item_key=lambda c: c.key,
    description="Additional ticket channels (SMS, WhatsApp, phone, Telegram, web chat)",
)


def core_channel_descriptors() -> list[ChannelDescriptor]:
    return [
        ChannelDescriptor(key=name, config_keys=frozenset(keys))
        for name, keys in sorted(CHANNEL_CONFIG_KEYS.items())
    ]
