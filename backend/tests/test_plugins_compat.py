from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from packaging.version import Version

from tiqora.plugins.compat import check_compat, release_core_version
from tiqora.plugins.manifest import PluginManifest, parse_manifest

FIX = Path(__file__).parent / "plugins" / "fixtures" / "tq_fixture_hello"


def _manifest(**overrides: Any) -> PluginManifest:
    base = parse_manifest((FIX / "tiqora-plugin.toml").read_text())
    return base.model_copy(update=overrides)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v0.18.2", "0.18.2"),
        ("0.18.0", "0.18.0"),
        ("v0.17.1+327.g9f845be8", None),
        ("9f845be8", None),
        ("", None),
    ],
)
def test_release_core_version(raw: str, expected: str | None) -> None:
    got = release_core_version({"TIQORA_VERSION": raw})
    assert (str(got) if got else None) == expected


def test_sdk_range_checked() -> None:
    m = _manifest(sdk=">=0.2")
    reason = check_compat(m, core=None, sdk="0.1.0")
    assert reason is not None and "0.1.0" in reason


def test_core_range_checked_only_on_release() -> None:
    m = _manifest(tiqora=">=1.0")
    assert check_compat(m, core=Version("0.18.0")) is not None
    assert check_compat(m, core=None) is None


def test_compatible() -> None:
    assert check_compat(_manifest(tiqora=">=0.18,<1"), core=Version("0.18.3")) is None
