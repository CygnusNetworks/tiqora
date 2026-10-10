"""Pure tests for the web-chat config value models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tiqora.webchat.models import (
    ChatConfigValues,
    ChatRateLimits,
    ChatTheme,
    ChatTopic,
    PrechatField,
    UploadPolicy,
)


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
