"""Process-wide registry with every core item registered exactly once."""

from __future__ import annotations

import threading

from tiqora.extensions.registry import Registry

_REGISTRY: Registry | None = None
_LOCK = threading.Lock()


def register_core(registry: Registry) -> None:
    """The only place core items are registered (no import-time side effects)."""
    from tiqora.channels.registry import CHANNELS, core_channel_descriptors

    for descriptor in core_channel_descriptors():
        registry.add(CHANNELS, descriptor)


def get_registry() -> Registry:
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            registry = Registry()
            register_core(registry)
            registry.freeze()
            _REGISTRY = registry
        return _REGISTRY


def set_registry_for_tests(registry: Registry | None) -> None:
    """Replace the cached registry; ``None`` drops it so the next call rebuilds."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = registry
