"""Plugin load state: per-process results, runtime states and the admin status summary."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

PluginRole = Literal["api", "worker", "mcp", "ai-worker"]
PLUGIN_ROLES: Final[tuple[PluginRole, ...]] = ("api", "worker", "mcp", "ai-worker")

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


def mark_failed(plugin_id: str, error: str) -> None:
    """Turn a loaded plugin into FAILED for this process (e.g. its routes were refused)."""
    global _LOADED
    with _LOCK:
        current = _LOADED.get(plugin_id)
        if current is None:
            return
        failed = PluginLoadResult(
            current.plugin_id, current.version, RuntimeState.FAILED, error, current.contributions
        )
        _LOADED = MappingProxyType({**_LOADED, plugin_id: failed})


def really_cancelled(stop: asyncio.Event | None = None) -> bool:
    """True when a ``CancelledError`` means the running task is cancelled or the worker stops.

    Plugin code can raise ``CancelledError`` on its own (``t.cancel(); await t`` unguarded).
    Outside a real cancellation that is a plugin failure, not a shutdown (B-I1). An
    ``asyncio.timeout`` block turns its own cancellation into ``TimeoutError`` and uncancels
    the task, so this is never the timeout case.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        return True
    return stop is not None and stop.is_set()


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
    API process (it decides on/off); ``other_states`` are worker, mcp and
    ai-worker rows (a role whose container never ran has no row and does not count).
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
