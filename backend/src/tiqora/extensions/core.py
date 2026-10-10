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
    from tiqora.reporting.settings import REPORTING_SECTION
    from tiqora.settings_sections.section import SETTINGS_SECTIONS, validate_section_schema

    for section in (JOBS_SECTION, AVATARS_SECTION, PROCESS_DEPLOY_SECTION, REPORTING_SECTION):
        validate_section_schema(section)
        registry.add(SETTINGS_SECTIONS, section)

    from tiqora.jobs.handlers.gi_mail import GI_MAIL_JOB
    from tiqora.jobs.handlers.ticket_export import TICKET_EXPORT_JOB
    from tiqora.jobs.registry import JOB_HANDLERS
    from tiqora.reporting.backfill import REPORT_BACKFILL_JOB
    from tiqora.reporting.jobs import REPORT_EXPORT_JOB, REPORT_RUN_JOB

    registry.add(JOB_HANDLERS, TICKET_EXPORT_JOB)
    registry.add(JOB_HANDLERS, GI_MAIL_JOB)
    registry.add(JOB_HANDLERS, REPORT_BACKFILL_JOB)
    registry.add(JOB_HANDLERS, REPORT_RUN_JOB)
    registry.add(JOB_HANDLERS, REPORT_EXPORT_JOB)

    from tiqora.reporting.semantic import REPORT_SOURCES
    from tiqora.reporting.sources import core_report_sources

    for s in core_report_sources():
        registry.add(REPORT_SOURCES, s)

    from tiqora.reporting.catalogue import BUILTIN_REPORTS, core_builtin_reports

    for r in core_builtin_reports():
        registry.add(BUILTIN_REPORTS, r)

    from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers

    for p in core_providers():
        registry.add(DASHBOARD_PROVIDERS, p)


def get_registry() -> Registry:
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            registry = Registry()
            register_core(registry)
            registry.freeze()
            _REGISTRY = registry
        return _REGISTRY


def install_registry(registry: Registry) -> None:
    """Make ``registry`` the process-wide registry (called once by ``init_registry``)."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = registry


def set_registry_for_tests(registry: Registry | None) -> None:
    """Replace the cached registry; ``None`` drops it so the next call rebuilds."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = registry
