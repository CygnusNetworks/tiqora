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

    from tiqora.avatars.settings import AVATARS_SECTION
    from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION
    from tiqora.jobs.settings import JOBS_SECTION
    from tiqora.settings_sections.section import SETTINGS_SECTIONS, validate_section_schema

    for section in (JOBS_SECTION, AVATARS_SECTION, PROCESS_DEPLOY_SECTION):
        validate_section_schema(section)
        registry.add(SETTINGS_SECTIONS, section)

    from tiqora.jobs.handlers.ticket_export import TICKET_EXPORT_JOB
    from tiqora.jobs.registry import JOB_HANDLERS

    registry.add(JOB_HANDLERS, TICKET_EXPORT_JOB)


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
