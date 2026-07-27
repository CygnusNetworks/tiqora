"""Tiqora — modern Znuny/OTRS 6.5-compatible ticket system."""

from __future__ import annotations

import os
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

#: Fallback when neither a build-time version nor package metadata is available
#: (e.g. running from a bare source checkout). Keep roughly in step with the
#: latest release tag so local/dev output is not wildly misleading.
_FALLBACK_VERSION = "0.2.2"

#: `git describe --tags --long --always` output, e.g. "v0.2.2-0-g4520721".
_DESCRIBE_RE = re.compile(r"^(?P<tag>.+)-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+)$", re.IGNORECASE)


def _normalize(raw: str) -> str:
    """Turn a `git describe` string into a human version, mirroring the frontend.

    - exactly on a tag ("v0.2.2-0-g…")  → "v0.2.2"
    - after a tag      ("v0.2.2-5-g…")  → "v0.2.2+5.g4520721"
    - anything else (plain tag / sha)   → returned unchanged
    """
    m = _DESCRIBE_RE.match(raw)
    if not m:
        return raw
    tag = m.group("tag")
    distance = int(m.group("distance"))
    if distance == 0:
        return tag
    return f"{tag}+{distance}.g{m.group('sha')}"


def _resolve_version() -> str:
    """Resolve the running version: build-time env → package metadata → fallback.

    ``TIQORA_VERSION`` is injected at image build time from ``git describe``
    (see Dockerfile / CI), the same source the frontend footer uses, so the UI,
    health endpoints and OpenAPI all report the real release instead of a
    hard-coded constant.
    """
    env = os.environ.get("TIQORA_VERSION", "").strip()
    if env:
        return _normalize(env)
    try:
        return _pkg_version("tiqora")
    except PackageNotFoundError:
        return _FALLBACK_VERSION


__version__ = _resolve_version()
