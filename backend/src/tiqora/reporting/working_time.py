"""Working-time arithmetic for reports: the escalation calendars, read once per run.

Everything calendar related delegates to :mod:`tiqora.znuny.escalation`
(``Kernel/System/DateTime.pm`` ``Add``/``Delta`` ports); this module only adds
caching (:class:`CalendarResolver`) and naive-datetime/epoch conversion in the
Znuny storage zone (``OTRSTimeZone``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.escalation import (
    VacationDays,
    VacationDaysOneTime,
    WorkingHours,
    _tzinfo,
    calendar_config,
    destination_time_epoch,
    escalation_preferences,
    to_epoch,
    working_seconds_between,
)
from tiqora.znuny.sysconfig import SysConfig


@dataclass(frozen=True, slots=True)
class WorkingCalendar:
    name: str | None
    working_hours: WorkingHours
    vacation_days: VacationDays
    vacation_days_once: VacationDaysOneTime
    time_zone: str

    @property
    def has_working_hours(self) -> bool:
        return any(self.working_hours.values())


@dataclass(frozen=True, slots=True)
class EscalationTargets:
    first_response_minutes: int  # 0 = none
    update_minutes: int
    solution_minutes: int
    calendar_name: str | None


class CalendarResolver:
    """One per builder run; caches targets, calendars and the storage zone."""

    def __init__(self, session: AsyncSession, sysconfig: SysConfig) -> None:
        self._session = session
        self._sysconfig = sysconfig
        self._storage_tz: str | None = None
        self._targets: dict[tuple[str, int], EscalationTargets] = {}
        self._calendars: dict[str | None, WorkingCalendar] = {}

    async def storage_time_zone(self) -> str:
        if self._storage_tz is None:
            self._storage_tz = await self._sysconfig.otrs_time_zone() or "UTC"
        return self._storage_tz

    async def targets(self, sla_id: int | None, queue_id: int) -> EscalationTargets:
        """SLA targets if the ticket has an SLA, else the queue (Znuny preferences)."""
        key = ("sla", sla_id) if sla_id else ("queue", queue_id)
        cached = self._targets.get(key)
        if cached is None:
            first, update, solution, calendar = await escalation_preferences(
                self._session, sla_id, queue_id
            )
            cached = EscalationTargets(first, update, solution, calendar)
            self._targets[key] = cached
        return cached

    async def calendar(self, name: str | None) -> WorkingCalendar:
        cached = self._calendars.get(name)
        if cached is None:
            hours, vacation, vacation_once, tz = await calendar_config(self._sysconfig, name)
            cached = WorkingCalendar(name, hours, vacation, vacation_once, tz)
            self._calendars[name] = cached
        return cached


def from_epoch(epoch: int, storage_tz: str) -> datetime:
    """Epoch -> naive wall-clock value in the storage zone (inverse of :func:`to_epoch`)."""
    return datetime.fromtimestamp(epoch, tz=_tzinfo(storage_tz)).replace(tzinfo=None)


def working_minutes(
    start: datetime, stop: datetime, calendar: WorkingCalendar, storage_tz: str
) -> int | None:
    """Whole working minutes between two storage-zone values; ``None`` without working hours.

    Znuny stat code truncates with ``int()``; so does this.
    """
    if not calendar.has_working_hours:
        return None
    seconds = working_seconds_between(
        to_epoch(start, storage_tz),
        to_epoch(stop, storage_tz),
        calendar.working_hours,
        calendar.vacation_days,
        calendar.vacation_days_once,
        calendar.time_zone,
    )
    return seconds // 60


def due_at(start: datetime, minutes: int, calendar: WorkingCalendar, storage_tz: str) -> datetime:
    """Deadline *minutes* of working time after *start* (wall minutes without working hours)."""
    if not calendar.has_working_hours:
        return start + timedelta(minutes=minutes)
    epoch = destination_time_epoch(
        to_epoch(start, storage_tz),
        minutes,
        calendar.working_hours,
        calendar.vacation_days,
        calendar.vacation_days_once,
        calendar.time_zone,
    )
    return from_epoch(epoch, storage_tz)


__all__ = [
    "CalendarResolver",
    "EscalationTargets",
    "WorkingCalendar",
    "due_at",
    "from_epoch",
    "to_epoch",
    "working_minutes",
]
