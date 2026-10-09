"""Perl semantics shared by the Znuny-compat layers (no Tiqora imports - safe anywhere)."""

from __future__ import annotations

from typing import Any


def perl_truthy(value: Any) -> bool:
    """Perl boolean context: undef, "", "0", 0, 0.0 are false; references (dict/list) are true.

    Znuny stores Checkbox SysConfig values as YAML strings (``--- '0'``), so Python's
    ``bool("0") is True`` must never be used on them.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    if isinstance(value, (int, float)):
        return value != 0
    return True
