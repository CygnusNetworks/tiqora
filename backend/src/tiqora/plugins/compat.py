"""Compatibility of a plugin manifest with this Tiqora (SDK range and release range)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Final

import structlog
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tiqora.plugins.manifest import PluginManifest

log = structlog.get_logger(__name__)

_RELEASE_RE: Final = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def release_core_version(env: Mapping[str, str] = os.environ) -> Version | None:
    """The release version of this image, or None for dev/untagged builds.

    Only ``TIQORA_VERSION`` of the exact form ``v?X.Y.Z`` counts; the package version
    is meaningless for compatibility.
    """
    m = _RELEASE_RE.fullmatch(env.get("TIQORA_VERSION", "").strip())
    return Version(m.group(1)) if m else None


def check_compat(
    manifest: PluginManifest, *, core: Version | None, sdk: str | None = None
) -> str | None:
    """German reason why the plugin cannot run here, or None when compatible."""
    if sdk is None:
        # lazy: tiqora_sdk imports the API, which imports this module (import cycle otherwise)
        from tiqora_sdk import SDK_VERSION

        sdk = SDK_VERSION
    if not SpecifierSet(manifest.sdk).contains(Version(sdk), prereleases=True):
        return f"Erweiterung benötigt SDK {manifest.sdk}, dieses Tiqora bringt {sdk} mit"
    if manifest.tiqora is None:
        return None
    if core is None:
        log.debug("plugin_core_version_unknown", plugin=manifest.id, wanted=manifest.tiqora)
        return None
    if not SpecifierSet(manifest.tiqora).contains(core, prereleases=True):
        return f"Erweiterung benötigt Tiqora {manifest.tiqora}, installiert ist {core}"
    return None
