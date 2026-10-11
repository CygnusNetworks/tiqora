"""``core.calendar-agenda``: today's (and the next days') appointments of the viewer's calendars."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from tiqora.calendar.schemas import OccurrenceOut
from tiqora.calendar.service import CalendarService
from tiqora.dashboard.providers import DashboardProvider, ProviderContext
from tiqora.dashboard.settings_models import CalendarAgendaSettings
from tiqora.domain.timezones import resolve_user_time_zone
from tiqora.znuny.sysconfig import SysConfig


def start_day(now: datetime, zone: str) -> date:
    """Today's date in ``zone``."""
    return now.replace(tzinfo=UTC).astimezone(zoneinfo.ZoneInfo(zone)).date()


def agenda_window(now: datetime, zone: str, days: int) -> tuple[datetime, datetime]:
    """Start of today in ``zone`` and ``days`` calendar days later, as naive UTC datetimes."""
    tz = zoneinfo.ZoneInfo(zone)
    today = start_day(now, zone)
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
    # All-day appointments are floating dates (stored as UTC midnight): they belong to the
    # day they name, not to the UTC instant. Fetch a day more on each side, then filter.
    found = await CalendarService(ctx.session).list_occurrences(
        ctx.user_id, range_start=start - timedelta(days=1), range_end=end + timedelta(days=1)
    )
    first_day = start_day(ctx.now, zone)
    last_day = first_day + timedelta(days=settings.days)

    def in_window(o: OccurrenceOut) -> bool:
        if o.all_day:
            return first_day <= o.start_time.date() < last_day
        return o.start_time < end and o.end_time > start

    occurrences = [o for o in found if in_window(o)]
    # list_occurrences returns them sorted by start_time
    return {"items": [o.model_dump(mode="json") for o in occurrences[: settings.limit]]}


def calendar_agenda_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.calendar-agenda",
        version=1,
        settings_model=CalendarAgendaSettings,
        run=run_calendar_agenda,
    )
