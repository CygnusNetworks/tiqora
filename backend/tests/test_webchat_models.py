"""Pure tests for the web-chat config value models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from tiqora.db.tiqora.models import TiqoraChatMessage
from tiqora.webchat.models import (
    ChatConfigValues,
    ChatRateLimits,
    ChatTheme,
    ChatTopic,
    PrechatField,
    UploadPolicy,
)
from tiqora.webchat.serialize import message_out


def test_defaults_are_valid() -> None:
    v = ChatConfigValues(topics=[ChatTopic(key="support", label="Support", queue_id=1)])
    assert v.offline_mode == "form" and v.wait_timeout_seconds == 180 and v.retention_days == 30
    assert [f.key for f in v.prechat_fields] == ["name", "email"]
    assert (
        v.upload_policy.max_bytes == 10 * 1024 * 1024
        and "image/svg+xml" not in v.upload_policy.types
    )


def test_topic_keys_unique() -> None:
    with pytest.raises(ValidationError):
        ChatConfigValues(
            topics=[
                ChatTopic(key="a", label="A", queue_id=1),
                ChatTopic(key="a", label="B", queue_id=2),
            ]
        )


@pytest.mark.parametrize(
    "field",
    [
        {"key": "message", "kind": "text", "label": "x"},  # reserved key
        {"key": "s", "kind": "select", "label": "x"},  # select without options
        {"key": "t", "kind": "text", "label": "x", "options": [{"value": "a", "label": "A"}]},
        {
            "key": "m",
            "kind": "text",
            "label": "x",
            "maps_to": "email",
        },  # email mapping needs kind email
        {
            "key": "d",
            "kind": "text",
            "label": "x",
            "maps_to": "dynamic_field",
        },  # missing dynamic_field
    ],
)
def test_bad_prechat_fields_rejected(field: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PrechatField.model_validate(field)


def test_two_email_mappings_rejected() -> None:
    fields = [
        PrechatField(key="a", kind="email", label="A", maps_to="email"),
        PrechatField(key="b", kind="email", label="B", maps_to="email"),
    ]
    with pytest.raises(ValidationError):
        ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=1)], prechat_fields=fields)


def test_theme_color_and_upload_bounds() -> None:
    with pytest.raises(ValidationError):
        ChatTheme(primary_color="red")
    with pytest.raises(ValidationError):
        UploadPolicy(max_bytes=26 * 1024 * 1024)
    with pytest.raises(ValidationError):
        UploadPolicy(types=["image/svg+xml"])  # type: ignore[list-item]


def test_rate_limit_defaults_and_bounds() -> None:
    limits = ChatRateLimits()
    assert limits.sessions_per_config_minute == 30 and limits.offline_per_config_hour == 60
    assert ChatRateLimits(sessions_per_config_minute=5).sessions_per_config_minute == 5
    for bad in (4, 301):
        with pytest.raises(ValidationError):
            ChatRateLimits(sessions_per_config_minute=bad)


def test_model_round_trips_through_json() -> None:
    v = ChatConfigValues(topics=[ChatTopic(key="support", label="Support", queue_id=3)])
    assert ChatConfigValues.model_validate(v.model_dump(mode="json")) == v


def test_privacy_url_requires_https() -> None:
    topics = [ChatTopic(key="s", label="S", queue_id=1)]
    assert ChatConfigValues(topics=topics, privacy_url="https://www.example.org/p").privacy_url
    with pytest.raises(ValidationError):
        ChatConfigValues(topics=topics, privacy_url="http://www.example.org/p")


def test_topic_without_ai_mode_defaults_off() -> None:
    t = ChatTopic.model_validate({"key": "support", "label": "Support", "queue_id": 3})
    assert t.ai_mode == "off"


def test_topic_rejects_unknown_ai_mode() -> None:
    with pytest.raises(ValidationError):
        ChatTopic.model_validate(
            {"key": "s", "label": "S", "queue_id": 3, "ai_mode": "assist_only"}
        )


def test_new_values_defaults() -> None:
    v = ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=3)])
    assert (
        v.offline_mode,
        v.bot_name,
        v.bot_tone_prompt,
        v.show_on_portal,
        v.identity_create_customers,
    ) == ("form", "KI-Assistent", None, False, False)


def test_offline_mode_ai_accepted() -> None:
    topics = [ChatTopic(key="s", label="S", queue_id=3)]  # no first_line topic on purpose
    assert ChatConfigValues(topics=topics, offline_mode="ai").offline_mode == "ai"
    with pytest.raises(ValidationError):
        ChatConfigValues(topics=topics, offline_mode="bot")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "x" * 81])
def test_bot_name_bounds(bad: str) -> None:
    with pytest.raises(ValidationError):
        ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=3)], bot_name=bad)


def test_message_out_bot_sender_carries_name() -> None:
    m = TiqoraChatMessage(
        session_id="0" * 32,
        ticket_id=1,
        seq=3,
        sender="bot",
        body="Hallo",
        meta='{"agent_name": "KI-Assistent"}',
        created=datetime(2026, 10, 11, 12, 0, 0),
    )
    out = message_out(m)
    assert out.sender == "bot" and out.agent_name == "KI-Assistent"
    assert out.body == "Hallo" and out.code is None and out.params == {}
