"""``core.calendar-agenda``: today's (and the next days') appointments of the viewer's calendars."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime, time, timedelta
from typing import Any

from tiqora.calendar.service import CalendarService
from tiqora.dashboard.providers import DashboardProvider, ProviderContext
from tiqora.dashboard.settings_models import CalendarAgendaSettings
from tiqora.domain.timezones import resolve_user_time_zone
from tiqora.znuny.sysconfig import SysConfig


def agenda_window(now: datetime, zone: str, days: int) -> tuple[datetime, datetime]:
    """Start of today in ``zone`` and ``days`` calendar days later, as naive UTC datetimes."""
    tz = zoneinfo.ZoneInfo(zone)
    today = now.replace(tzinfo=UTC).astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=days), time.min, tzinfo=tz)
    return (
        start.astimezone(UTC).replace(tzinfo=None),
        end.astimezone(UTC).replace(tzinfo=None),
    )


async def run_calendar_agenda(
    ctx: ProviderContext, settings: CalendarAgendaSettings
) -> dict[str, Any]:
    zone = await resolve_user_time_zone(ctx.session, SysConfig(ctx.session), ctx.user_id)
    start, end = agenda_window(ctx.now, zone, settings.days)
    occurrences = await CalendarService(ctx.session).list_occurrences(
        ctx.user_id, range_start=start, range_end=end
    )
    occurrences.sort(key=lambda o: o.start_time)
    return {"items": [o.model_dump(mode="json") for o in occurrences[: settings.limit]]}


def calendar_agenda_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.calendar-agenda",
        version=1,
        settings_model=CalendarAgendaSettings,
        run=run_calendar_agenda,
    )
