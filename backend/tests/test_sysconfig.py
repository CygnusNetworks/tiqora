"""Unit tests for SysConfig YAML resolution (no database required)."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from tiqora.znuny.sysconfig import (
    ZNUNY_SETTING_DEFAULTS,
    SysConfig,
    decode_effective_value,
    yaml_encode_effective,
)


def test_decode_yaml_scalar_string() -> None:
    raw = yaml.safe_dump("Ticket#")
    assert decode_effective_value(raw) == "Ticket#"
    assert decode_effective_value(raw.encode("utf-8")) == "Ticket#"


def test_decode_yaml_integer_and_list() -> None:
    assert decode_effective_value(yaml.safe_dump(10)) == 10
    assert decode_effective_value(yaml.safe_dump(["a", "b"])) == ["a", "b"]


def test_decode_plain_non_yaml_fallback() -> None:
    # Not valid YAML structure we care about — still returns something usable
    assert decode_effective_value("plain-string") == "plain-string"


def test_decode_none_and_empty() -> None:
    assert decode_effective_value(None) is None
    assert decode_effective_value("") == ""
    assert decode_effective_value(b"") == ""


@pytest.mark.asyncio
async def test_fetch_injection_modified_overrides_default() -> None:
    rows: dict[str, Any] = {
        "SystemID": yaml_encode_effective("99"),
        "Ticket::Hook": yaml_encode_effective("Case#"),
    }

    async def fetch(name: str) -> Any | None:
        return rows.get(name)

    cfg = SysConfig(fetch=fetch, ttl_seconds=60)
    assert await cfg.system_id() == "99"
    assert await cfg.ticket_hook() == "Case#"
    # Second call hits cache
    assert await cfg.system_id() == "99"


@pytest.mark.asyncio
async def test_missing_falls_back_to_documented_defaults() -> None:
    async def fetch(_name: str) -> Any | None:
        return None

    cfg = SysConfig(fetch=fetch)
    assert await cfg.system_id() == ZNUNY_SETTING_DEFAULTS["SystemID"]
    assert await cfg.ticket_number_generator() == ZNUNY_SETTING_DEFAULTS["Ticket::NumberGenerator"]
    assert await cfg.ticket_hook() == ZNUNY_SETTING_DEFAULTS["Ticket::Hook"]
    assert await cfg.ticket_hook_divider() == ZNUNY_SETTING_DEFAULTS["Ticket::HookDivider"]
    assert await cfg.ticket_index_module() == ZNUNY_SETTING_DEFAULTS["Ticket::IndexModule"]
    assert await cfg.otrs_time_zone() == ZNUNY_SETTING_DEFAULTS["OTRSTimeZone"]
    assert await cfg.default_language() == ZNUNY_SETTING_DEFAULTS["DefaultLanguage"]
    assert await cfg.fqdn() == ZNUNY_SETTING_DEFAULTS["FQDN"]


@pytest.mark.asyncio
async def test_tiqora_settings_bundle() -> None:
    async def fetch(_name: str) -> Any | None:
        return None

    cfg = SysConfig(fetch=fetch)
    bundle = await cfg.tiqora_settings()
    assert set(bundle) == set(ZNUNY_SETTING_DEFAULTS)


@pytest.mark.asyncio
async def test_cache_ttl_and_clear() -> None:
    calls = {"n": 0}

    async def fetch(name: str) -> Any | None:
        calls["n"] += 1
        return yaml_encode_effective(f"v{calls['n']}")

    cfg = SysConfig(fetch=fetch, ttl_seconds=3600)
    assert await cfg.get("SystemID") == "v1"
    assert await cfg.get("SystemID") == "v1"  # cached
    assert calls["n"] == 1
    cfg.clear_cache()
    assert await cfg.get("SystemID") == "v2"
    assert calls["n"] == 2
