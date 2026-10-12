"""Tiqora plugin SDK: the only Python API plugins may import.

Plugin code runs in-process with full trust; this module is the stable, versioned
surface (SDK 0.x minors may still break). Anything not in ``__all__`` is internal.
"""

from __future__ import annotations

import typing as _t

SDK_VERSION: _t.Final = "0.2.0"

# ruff: noqa: E402  (SDK_VERSION must exist before the imports below, see tiqora.plugins.compat)
from collections import abc as _abc

from tiqora.api.deps import CurrentUser as CurrentAgent
from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser as AdminAgent
from tiqora.jobs.context import JobContext, JobRetry
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import enqueue_job
from tiqora.plugins.context import (
    OutboundHostNotAllowed,
    PluginContext,
    PluginHttp,
    TicketSummary,
)
from tiqora.plugins.events import EventContext, EventHandler, TicketEvent
from tiqora.plugins.registrar import RegistrationRejected
from tiqora.plugins.tasks import TaskContext, TaskRun

if _t.TYPE_CHECKING:
    from fastapi import APIRouter
    from pydantic import BaseModel

    from tiqora.plugins.sync_hooks import SyncHandler, SyncOperation
    from tiqora.settings_sections.section import SettingsSection

__all__ = [
    "SDK_VERSION",
    # registration
    "Plugin",
    "PluginRegistry",
    "RegistrationRejected",
    # runtime context
    "PluginContext",
    "PluginHttp",
    "OutboundHostNotAllowed",
    "TicketSummary",
    # events / tasks / jobs
    "TicketEvent",
    "EventContext",
    "EventHandler",
    "TaskContext",
    "TaskRun",
    "JobHandler",
    "JobContext",
    "JobRetry",
    "enqueue_job",
    # HTTP route dependencies
    "CurrentAgent",
    "AdminAgent",
    "DbSession",
]


class PluginRegistry(_t.Protocol):
    """What ``Plugin.register`` receives: stages items; nothing is live until all succeed."""

    @property
    def plugin_id(self) -> str: ...

    @property
    def context(self) -> PluginContext: ...

    def settings(self, model: type[BaseModel]) -> SettingsSection: ...

    def route(self, router: APIRouter, *, public: bool = False) -> None: ...

    def on_event(
        self,
        name: str,
        events: _abc.Iterable[str],
        handler: EventHandler,
        *,
        timeout_seconds: int = 30,
    ) -> None: ...

    def scheduled_task(
        self,
        name: str,
        run: TaskRun,
        *,
        every_seconds: int | None = None,
        daily_at: str | None = None,
        cron: str | None = None,
        timeout_seconds: int = 300,
    ) -> None: ...

    def job_handler(self, handler: JobHandler) -> None: ...

    def mcp_tool(
        self,
        name: str,
        fn: _abc.Callable[..., _abc.Awaitable[_t.Any]],
        *,
        description: str,
        write: bool = False,
        timeout_seconds: int = 60,
    ) -> None: ...

    def before(
        self,
        operation: SyncOperation,
        name: str,
        handler: SyncHandler,
        *,
        timeout_ms: int = 1000,
        on_error: _t.Literal["allow", "deny"] = "allow",
        order: int = 100,
    ) -> None: ...


class Plugin(_t.Protocol):
    """The object an entry point of group ``tiqora.plugins`` resolves to."""

    def register(self, registry: PluginRegistry) -> None: ...
