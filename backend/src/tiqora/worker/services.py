"""Daemon service catalog — single source of truth for the admin "Dienste"
page (``/api/v1/admin/daemons``) and, indirectly, the worker's tick loops in
``tiqora.worker.__main__``: adding a new takeover service means adding one
entry here rather than re-deriving its enabled/interval/status keys at every
call site (admin schemas, tests, docs).

``interval_settings_attr`` names the :class:`tiqora.config.Settings` field
that supplies the config-level default cadence (the DB override in
``interval_key``, when set, always wins) — looked up dynamically so the
catalog never duplicates a literal that could drift from ``config.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tiqora.domain.settings_store import (
    KEY_AI_AUDIT_CLEANUP_ENABLED,
    KEY_AI_WORKER_ENABLED,
    KEY_AI_WORKER_INTERVAL_SECONDS,
    KEY_ESCALATION_ENABLED,
    KEY_ESCALATION_INTERVAL_SECONDS,
    KEY_GDPR_ERASURE_PURGE_ENABLED,
    KEY_GDPR_RETENTION_ENABLED,
    KEY_GENERIC_AGENT_ENABLED,
    KEY_GENERIC_AGENT_INTERVAL_SECONDS,
    KEY_NOTIFICATIONS_ENABLED,
    KEY_NOTIFICATIONS_INTERVAL_SECONDS,
    KEY_OUTBOX_ENABLED,
    KEY_OUTBOX_INTERVAL_SECONDS,
    KEY_POSTMASTER_ENABLED,
    KEY_POSTMASTER_INTERVAL_SECONDS,
)

ScheduleKind = Literal["interval", "daily"]


@dataclass(frozen=True)
class DaemonService:
    """One row in the "Dienste" catalog.

    ``schedule_kind == "interval"`` implies ``interval_settings_attr`` is set
    (``interval_key`` may still be ``None`` when the cadence is not
    admin-overridable, e.g. the poller); ``schedule_kind == "daily"`` implies
    ``daily_at`` is set.
    """

    slug: str
    enabled_key: str | None  # None: always on, not toggleable (poller)
    default_enabled: bool
    toggleable: bool
    schedule_kind: ScheduleKind
    interval_key: str | None = None
    interval_settings_attr: str | None = None
    daily_at: str | None = None  # UTC "HH:MM"


DAEMON_SERVICES: tuple[DaemonService, ...] = (
    DaemonService(
        slug="poller",
        enabled_key=None,
        default_enabled=True,
        toggleable=False,
        schedule_kind="interval",
        interval_key=None,
        interval_settings_attr="poller_interval_seconds",
    ),
    DaemonService(
        slug="outbox",
        enabled_key=KEY_OUTBOX_ENABLED,
        default_enabled=True,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_OUTBOX_INTERVAL_SECONDS,
        interval_settings_attr="outbox_drain_interval_seconds",
    ),
    DaemonService(
        slug="postmaster",
        enabled_key=KEY_POSTMASTER_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_POSTMASTER_INTERVAL_SECONDS,
        interval_settings_attr="postmaster_interval_seconds",
    ),
    DaemonService(
        slug="escalation",
        enabled_key=KEY_ESCALATION_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_ESCALATION_INTERVAL_SECONDS,
        interval_settings_attr="escalation_interval_seconds",
    ),
    DaemonService(
        slug="notifications",
        enabled_key=KEY_NOTIFICATIONS_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_NOTIFICATIONS_INTERVAL_SECONDS,
        interval_settings_attr="notifications_interval_seconds",
    ),
    DaemonService(
        slug="generic_agent",
        enabled_key=KEY_GENERIC_AGENT_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_GENERIC_AGENT_INTERVAL_SECONDS,
        interval_settings_attr="generic_agent_interval_seconds",
    ),
    DaemonService(
        slug="gdpr_retention",
        enabled_key=KEY_GDPR_RETENTION_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="daily",
        daily_at="03:00",
    ),
    DaemonService(
        slug="gdpr_erasure_purge",
        enabled_key=KEY_GDPR_ERASURE_PURGE_ENABLED,
        default_enabled=True,
        toggleable=True,
        schedule_kind="daily",
        daily_at="03:30",
    ),
    # Status is written by the separate tiqora-ai-worker process (own
    # heartbeat/entrypoint role, tiqora.ai.worker.run_ai_worker) — but
    # daemon.*.status.* keys live in tiqora_settings regardless of which
    # process writes them, so this catalog entry is process-independent like
    # every other row here and the "Dienste" page shows it the same way.
    DaemonService(
        slug="ai_worker",
        enabled_key=KEY_AI_WORKER_ENABLED,
        default_enabled=False,
        toggleable=True,
        schedule_kind="interval",
        interval_key=KEY_AI_WORKER_INTERVAL_SECONDS,
        interval_settings_attr="ai_worker_interval_seconds",
    ),
    DaemonService(
        slug="ai_audit_cleanup",
        enabled_key=KEY_AI_AUDIT_CLEANUP_ENABLED,
        default_enabled=True,
        toggleable=True,
        schedule_kind="daily",
        daily_at="04:00",
    ),
)

DAEMON_SERVICES_BY_SLUG: dict[str, DaemonService] = {s.slug: s for s in DAEMON_SERVICES}

__all__ = ["DAEMON_SERVICES", "DAEMON_SERVICES_BY_SLUG", "DaemonService", "ScheduleKind"]
