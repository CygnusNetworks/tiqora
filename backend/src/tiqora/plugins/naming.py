"""Plugin-qualified names for Znuny-shaped items (D1.1).

A plugin passes only ``<Name>``; the registrar builds the qualified form so a plugin can
never shadow a core item and Znuny sees a recognisable, namespaced name.
"""

from __future__ import annotations

import re
from typing import Final

PLUGIN_PREFIX: Final = "Plugin_"
#: names a plugin passes for Znuny-shaped items (actions, conditions, operations, ...)
CAMEL_NAME_RE: Final = re.compile(r"^[A-Z][A-Za-z0-9]{1,40}$")
#: placeholder names
TAG_NAME_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")


def qualified(short_id: str, name: str) -> str:
    """``Plugin_<short_id>_<Name>``, e.g. ``qualified("points", "SetContract")``."""
    return f"{PLUGIN_PREFIX}{short_id}_{name}"


def is_plugin_name(value: str) -> bool:
    """True when ``value`` (or its last ``::`` segment) starts with ``Plugin_``."""
    return value.startswith(PLUGIN_PREFIX) or value.rsplit("::", 1)[-1].startswith(PLUGIN_PREFIX)


def channel_key(short_id: str, name: str) -> str:
    """``plugin_<short_id>_<name.lower()>`` (the ``ArticleIn.channel`` value)."""
    return f"plugin_{short_id}_{name.lower()}"
