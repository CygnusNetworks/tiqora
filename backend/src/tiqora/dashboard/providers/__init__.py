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

from tiqora.extensions.registry import ExtensionPoint

FORBIDDEN_MESSAGE: Final = "Dafür fehlt Ihnen die Berechtigung."


@dataclass(frozen=True, slots=True)
class ProviderContext:
    user_id: int  # the viewer
    session: AsyncSession  # own session per provider run (never the request's)
    now: datetime  # naive UTC
    filters: Mapping[str, Any] = field(default_factory=dict)  # {} in phase 1
    redis: Redis | None = None
    preview: bool = False  # True only for the settings preview: never write history


class ProviderForbidden(Exception):  # noqa: N818 - D6 name
    """The viewer may not see this widget's data; ``message`` is shown in the tile."""

    def __init__(self, message: str = FORBIDDEN_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class DashboardProvider:
    key: str  # == widget type_key for core widgets
    version: int  # == type_version of the widget it serves
    settings_model: type[BaseModel]  # validated copy of the stored settings (defaults applied)
    run: Callable[[ProviderContext, Any], Awaitable[dict[str, Any]]]
    timeout_seconds: float = 5.0  # batch budget


DASHBOARD_PROVIDERS: Final = ExtensionPoint[DashboardProvider](
    "core.dashboard.providers", lambda p: p.key, "Datenquellen für Dashboard-Widgets"
)


def core_providers() -> list[DashboardProvider]:
    """The core providers, registered by ``register_core``."""
    from tiqora.dashboard.providers.calendar_agenda import calendar_agenda_provider
    from tiqora.dashboard.providers.counter import counter_provider
    from tiqora.dashboard.providers.escalations import escalations_provider
    from tiqora.dashboard.providers.reminders import reminders_provider
    from tiqora.dashboard.providers.team_presence import team_presence_provider
    from tiqora.dashboard.providers.ticket_list import ticket_list_provider

    return [
        counter_provider(),
        ticket_list_provider(),
        escalations_provider(),
        reminders_provider(),
        calendar_agenda_provider(),
        team_presence_provider(),
    ]
