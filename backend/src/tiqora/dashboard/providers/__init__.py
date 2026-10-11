"""Data providers of dashboard widgets (D6).

A provider loads one widget's data for the **viewing** user (never the dashboard owner), in its
own session handed over by :mod:`tiqora.dashboard.runner`. Providers never commit: the runner
commits after the statement timeout is lifted (MariaDB keeps that limit per connection).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.extensions.registry import ExtensionPoint

FORBIDDEN_MESSAGE: Final = "Dafür fehlt Ihnen die Berechtigung."


@dataclass(frozen=True, slots=True)
class ProviderContext:
    user_id: int  # the viewer
    session: AsyncSession  # own session per provider run (never the request's)
    now: datetime  # naive UTC
    #: ``{"queue_ids": [...], "time_range": ...}`` of the dashboard, already reduced to the
    #: empty filters for pinned widgets; ``{}`` outside a dashboard run
    filters: Mapping[str, Any] = field(default_factory=dict)
    redis: Redis | None = None
    preview: bool = False  # True only for the settings preview: never write history
    app_settings: Settings | None = None
    report_cache_backend: Any | None = None
    kiosk: bool = False  # True on the unauthenticated wallboard path
    #: loop-clock (``loop.time()``) instant at which the runner gives up on this provider;
    #: ``None`` outside the runner. Report providers bound their queries by it.
    deadline: float | None = None


def filter_queue_ids(ctx: ProviderContext) -> list[int] | None:
    """The dashboard's queue narrowing, ``None`` when there is none."""
    ids = ctx.filters.get("queue_ids") if ctx.filters else None
    return list(ids) if ids else None


class ProviderForbidden(Exception):  # noqa: N818 - D6 name
    """The viewer may not see this widget's data; ``message`` is shown in the tile."""

    def __init__(self, message: str = FORBIDDEN_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class ProviderError(Exception):
    """The widget cannot run; ``message`` is shown in an error tile (not a permission problem)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class DashboardProvider:
    key: str  # == widget type_key for core widgets
    version: int  # == type_version of the widget it serves
    settings_model: type[BaseModel]  # validated copy of the stored settings (defaults applied)
    run: Callable[[ProviderContext, Any], Awaitable[dict[str, Any]]]
    timeout_seconds: float = 5.0  # batch budget
    #: the provider bounds its own statements (``QueryBudget`` of ``ProviderContext.deadline``);
    #: the runner then sets no ``statement_timeout`` around it, because the reporting engine
    #: sets and resets one per statement and its reset would lift the runner's limit on MariaDB
    self_limited: bool = False


DASHBOARD_PROVIDERS: Final = ExtensionPoint[DashboardProvider](
    "core.dashboard.providers", lambda p: p.key, "Datenquellen für Dashboard-Widgets"
)


def core_providers() -> list[DashboardProvider]:
    """The core providers, registered by ``register_core``."""
    from tiqora.dashboard.providers.ai_status import ai_status_provider
    from tiqora.dashboard.providers.calendar_agenda import calendar_agenda_provider
    from tiqora.dashboard.providers.chat_queue import chat_queue_provider
    from tiqora.dashboard.providers.counter import counter_provider
    from tiqora.dashboard.providers.escalations import escalations_provider
    from tiqora.dashboard.providers.queue_overview import queue_overview_provider
    from tiqora.dashboard.providers.reminders import reminders_provider
    from tiqora.dashboard.providers.report import report_provider
    from tiqora.dashboard.providers.team_presence import team_presence_provider
    from tiqora.dashboard.providers.ticket_list import ticket_list_provider

    return [
        counter_provider(),
        ticket_list_provider(),
        escalations_provider(),
        reminders_provider(),
        calendar_agenda_provider(),
        team_presence_provider(),
        queue_overview_provider(),
        chat_queue_provider(),
        ai_status_provider(),
        report_provider(),
    ]
