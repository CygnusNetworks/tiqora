"""Perl semantics shared by the Znuny-compat layers (no Tiqora imports - safe anywhere)."""

from __future__ import annotations

import re
from typing import Any

_COMMA_RE = re.compile(r"\s*,\s*")


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


def perl_comma_list(value: Any) -> list[str]:
    """``split /\\s*,\\s*/`` of a Znuny list key, empty items dropped (a list is taken as is)."""
    if value is None:
        return []
    items = value if isinstance(value, list | tuple) else _COMMA_RE.split(str(value).strip())
    return [str(i).strip() for i in items if str(i).strip()]
