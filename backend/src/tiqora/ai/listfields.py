"""Tolerant parsing for the queue-policy list columns.

``kb_tags`` / ``kb_category_ids`` / ``mcp_client_ids`` are Text columns whose
canonical format is a JSON array, but the admin frontend has historically
saved comma-separated strings (``"21,22"`` / ``"studnet, netz"``) and prod
rows exist in both shapes. ``json.loads`` on those either crashes
(``JSONDecodeError`` on ``"a,b"``) or silently yields a scalar
(``json.loads("2") == 2`` → ``.in_(2)`` → SQLAlchemy ArgumentError), which
took down manual assist in production. Accept both shapes everywhere.
"""

from __future__ import annotations

import json


def parse_int_list(raw: str | None) -> list[int]:
    """JSON array or CSV of ints → list[int]; empty/None/garbage → []."""
    if raw is None or not raw.strip():
        return []
    text = raw.strip()
    try:
        value = json.loads(text)
    except ValueError:
        value = text.split(",")
    if not isinstance(value, list):
        value = [value]
    out: list[int] = []
    for item in value:
        try:
            number = int(str(item).strip())
        except ValueError:
            continue
        if number > 0:
            out.append(number)
    return out


def parse_str_list(raw: str | None) -> list[str]:
    """JSON array or CSV of strings → list[str]; empty/None → []."""
    if raw is None or not raw.strip():
        return []
    text = raw.strip()
    try:
        value = json.loads(text)
    except ValueError:
        value = text.split(",")
    if not isinstance(value, list):
        value = [value]
    return [s for s in (str(item).strip() for item in value) if s]
