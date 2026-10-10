"""Plugin load state: per-process results, runtime states and the admin status summary."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

PluginRole = Literal["api", "worker", "mcp"]
PLUGIN_ROLES: Final[tuple[PluginRole, ...]] = ("api", "worker", "mcp")

#: boots that died between ``mark_loading`` and ``mark_result`` before a plugin is quarantined
QUARANTINE_AFTER: Final = 3


class RuntimeState(StrEnum):
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"
    INCOMPATIBLE = "incompatible"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class PluginLoadResult:
    plugin_id: str
    version: str
    state: RuntimeState
    error: str | None
    contributions: tuple[tuple[str, str], ...] = ()  # (point_key, item_key) committed


_LOCK = threading.Lock()
_LOADED: Mapping[str, PluginLoadResult] = MappingProxyType({})


def loaded_plugins() -> Mapping[str, PluginLoadResult]:
    """Load results of this process (empty until ``init_registry`` ran, or in safe mode)."""
    return _LOADED


def set_loaded_plugins(results: Iterable[PluginLoadResult]) -> None:
    global _LOADED
    snapshot = MappingProxyType({r.plugin_id: r for r in results})
    with _LOCK:
        _LOADED = snapshot


def is_active(plugin_id: str) -> bool:
    result = _LOADED.get(plugin_id)
    return result is not None and result.state is RuntimeState.ACTIVE


PluginStatus = Literal[
    "active",
    "disabled",
    "failed",
    "quarantined",
    "incompatible",
    "invalid",
    "restart_required",
    "unknown",
]


def summarize_status(
    *,
    enabled: bool,
    problem: str | None,
    api_state: RuntimeState | None,
    compat_problem: str | None,
    other_states: Iterable[RuntimeState] = (),
) -> PluginStatus:
    """One admin-facing status from the switch, discovery and the runtime rows of all roles.

    Precedence (R-STATUS-ROLES): invalid > incompatible > quarantined (any role) >
    failed (any role) > restart_required > active / disabled > unknown. ``api_state`` is the
    API process (it decides on/off); ``other_states`` are worker and mcp rows.
    """
    if problem:
        return "invalid"
    if compat_problem:
        return "incompatible"
    states = [s for s in (api_state, *other_states) if s is not None]
    if RuntimeState.QUARANTINED in states:
        return "quarantined"
    if RuntimeState.FAILED in states:
        return "failed"
    if (
        api_state is None
    ):  # R-STATUS-ROLES-AMEND: "unknown" (api never booted) ranks before restart_required
        return "unknown"
    if enabled != (api_state is RuntimeState.ACTIVE):
        return "restart_required"
    return "active" if enabled else "disabled"
