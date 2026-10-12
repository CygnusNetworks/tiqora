"""Optimistic-lock version of a stored ``pm_*`` row."""

from __future__ import annotations

import hashlib


def entity_version(*, name: str, config: str, layout: str = "", state_entity_id: str = "") -> str:
    """First 16 hex of sha256 over the stored row text; sent back as ``expected_version``."""
    h = hashlib.sha256()
    for part in (name, config, layout, state_entity_id):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]
