"""Process transition actions ``AppointmentCreate``, ``AppointmentUpdate``, ``AppointmentRemove``.

Ports of ``Kernel/System/ProcessManagement/TransitionAction/Appointment{Create,Update,Remove}.pm``
(Znuny 6.5.22). Shared rules:

* ``UserID`` is ``Config.UserID`` (``DynamicField_UserID`` is converted to it first), else the
  acting user (``Base::_OverrideUserID``).
* The calendar comes from ``CalendarName`` (valid calendars only) or ``CalendarID``.
* ``TeamID`` and ``ResourceID`` are comma lists (``split /\\s*,\\s*/``).
* Date keys are strings in the system time zone (``OTRSTimeZone``), ``Base::_ValidDateTimeConvert``
  also accepts a bare date. They are stored as naive UTC.
* ``AppointmentCreate`` checks no permission (``Appointment.pm`` ``AppointmentCreate``).
  ``AppointmentDelete`` needs ``create`` or ``rw`` on the calendar (``Appointment.pm:1548-1560``).
  ``AppointmentUpdate`` needs ``rw`` here (the Tiqora service rule; plan D4).
* A failing action never aborts the transition (``Process.pm:576-586``): every refusal is a
  ``process_action_appointment_skipped`` warning and a return, nothing raises.

Limitations (logged and ignored): ``Custom*`` recurrence types, ``RecurrenceFrequency``,
``RecurrenceExclude``, ``RecurrenceID``, ``RecurringRaw``, ``ParentID``, ``UniqueID``,
``TicketAppointmentRuleID`` and all ``Notification*`` keys.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import RECUR_TYPES
from tiqora.calendar.schemas import AppointmentIn, AppointmentUpdateIn, RecurrenceIn
from tiqora.calendar.service import (
    AppointmentNotFound,
    CalendarForbidden,
    CalendarNotFound,
    CalendarService,
)
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment
from tiqora.domain.ticket_write_service import InvalidInput, update_dynamic_field
from tiqora.permissions.engine import PermissionEngine
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

_IGNORED_KEYS = (
    "ParentID",
    "UniqueID",
    "RecurringRaw",
    "RecurrenceFrequency",
    "RecurrenceExclude",
    "RecurrenceID",
    "TicketAppointmentRuleID",
)
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_COMMA_SPLIT_RE = re.compile(r"\s*,\s*")
_MAX_LEN = 255  # calendar_appointment.title/location are String(255)


def _skip(action: str, reason: str) -> None:
    logger.warning("process_action_appointment_skipped action=%s reason=%s", action, reason)


def _text(config: dict[str, Any], key: str) -> str | None:
    """A config value as a trimmed string; ``None`` when absent or empty (Perl-falsy ``''``)."""
    value = config.get(key)
    if value is None or isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def _too_long(config: dict[str, Any], action: str) -> bool:
    """Title/Location beyond the column width would raise a DataError after the insert."""
    for key in ("Title", "Location"):
        value = _text(config, key)
        if value is not None and len(value) > _MAX_LEN:
            _skip(action, f"{key} is longer than {_MAX_LEN} characters")
            return True
    return False


def _split_list(value: str | None) -> list[str]:
    if value is None:
        return []
    return [p for p in _COMMA_SPLIT_RE.split(value.strip()) if p]


def _acting_user(config: dict[str, Any], user_id: int, action: str) -> int:
    """``_OverrideUserID``: ``DynamicField_UserID`` becomes ``UserID``, which wins when set."""
    override = _text(config, "DynamicField_UserID") or _text(config, "UserID")
    if override is None:
        return user_id
    try:
        return int(override)
    except ValueError:
        _skip(action, f"UserID {override!r} is not a number, using the acting user")
        return user_id


async def _zone(sysconfig: SysConfig) -> ZoneInfo | None:
    name = await sysconfig.otrs_time_zone()
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _parse_time(value: str, zone: ZoneInfo) -> datetime | None:
    """``YYYY-MM-DD[ HH:MM[:SS]]`` in the system zone -> aware UTC (``_ValidDateTimeConvert``)."""
    m = _DATE_RE.match(value.strip())
    if m is None:
        return None
    year, month, day, hour, minute, second = (int(g) if g else 0 for g in m.groups())
    try:
        local = datetime(year, month, day, hour, minute, second, tzinfo=zone)
    except ValueError:
        return None
    return local.astimezone(UTC)


async def _resolve_calendar(session: AsyncSession, config: dict[str, Any]) -> Calendar | None:
    """``CalendarName`` wins over ``CalendarID``; only valid calendars (``CalendarGet``)."""
    name = _text(config, "CalendarName")
    if name is not None:
        stmt = select(Calendar).where(Calendar.name == name, Calendar.valid_id == 1)
        return (await session.execute(stmt)).scalars().first()
    raw_id = _text(config, "CalendarID")
    if raw_id is None or not raw_id.isdigit():
        return None
    row = await session.get(Calendar, int(raw_id))
    return row if row is not None and row.valid_id == 1 else None


def _warn_ignored(config: dict[str, Any], action: str) -> None:
    ignored = [k for k in _IGNORED_KEYS if perl_truthy(config.get(k))]
    ignored += sorted(k for k in config if k.startswith("Notification") and perl_truthy(config[k]))
    if ignored:
        _skip(action, "unsupported keys ignored: " + ", ".join(ignored))


def _recurrence(config: dict[str, Any], zone: ZoneInfo, action: str) -> RecurrenceIn | None:
    """Tiqora's RRULE subset (``RECUR_TYPES``); ``Custom*`` types are logged and ignored."""
    if not perl_truthy(config.get("Recurring")):
        return None
    kind = _text(config, "RecurrenceType")
    if kind not in RECUR_TYPES:
        _skip(action, f"RecurrenceType {kind!r} is not supported, appointment is not recurring")
        return None
    interval = _text(config, "RecurrenceInterval")
    count = _text(config, "RecurrenceCount")
    until_raw = _text(config, "RecurrenceUntil")
    until = _parse_time(until_raw, zone) if until_raw else None
    if until_raw and until is None:
        _skip(action, f"RecurrenceUntil {until_raw!r} is not a date, ignored")
    try:
        return RecurrenceIn(
            type=str(kind),
            interval=int(interval) if interval else 1,
            count=int(count) if count else None,
            until=until,
        )
    except ValueError:
        _skip(action, "invalid RecurrenceInterval/RecurrenceCount, appointment is not recurring")
        return None


async def action_appointment_create(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """``AppointmentCreate.pm``: create an appointment, optionally store its id in a DF."""
    action = "AppointmentCreate"
    actor = _acting_user(config, user_id, action)
    zone = await _zone(sysconfig)
    if zone is None:
        _skip(action, "OTRSTimeZone is not a valid time zone")
        return
    calendar = await _resolve_calendar(session, config)
    if calendar is None:
        _skip(action, "unknown or invalid calendar (CalendarName/CalendarID)")
        return
    title = _text(config, "Title")
    start_raw, end_raw = _text(config, "StartTime"), _text(config, "EndTime")
    if title is None or start_raw is None or end_raw is None:
        _skip(action, "Title, StartTime and EndTime are required")
        return
    start, end = _parse_time(start_raw, zone), _parse_time(end_raw, zone)
    if start is None or end is None:
        _skip(action, "StartTime/EndTime are not dates")
        return
    if end < start:
        _skip(action, "EndTime is before StartTime")
        return
    if _too_long(config, action):
        return
    _warn_ignored(config, action)
    data = AppointmentIn(
        calendar_id=calendar.id,
        title=title,
        description=_text(config, "Description"),
        location=_text(config, "Location"),
        start_time=start,
        end_time=end,
        all_day=perl_truthy(config.get("AllDay")),
        team_id=",".join(_split_list(_text(config, "TeamID"))) or None,
        resource_id=",".join(_split_list(_text(config, "ResourceID"))) or None,
        recurrence=_recurrence(config, zone, action),
    )
    # Appointment.pm AppointmentCreate checks no calendar permission.
    row = await CalendarService(session).create_appointment_unchecked(actor, data)
    field_name = _text(config, "DynamicField_AppointmentID")
    if field_name is not None:
        # AppointmentCreate.pm: ValueSet with UserID 1 (system history entry).
        try:
            await update_dynamic_field(
                session, ticket_id=ticket_id, field_name=field_name, values=[str(row.id)], user_id=1
            )
        except InvalidInput as exc:
            _skip(action, f"DynamicField_AppointmentID {field_name!r} not written: {exc}")


async def _appointment(
    session: AsyncSession, config: dict[str, Any], action: str
) -> CalendarAppointment | None:
    raw = _text(config, "AppointmentID")
    if raw is None or not raw.isdigit():
        _skip(action, "AppointmentID is required")
        return None
    row = await session.get(CalendarAppointment, int(raw))
    if row is None:
        _skip(action, f"appointment {raw} not found")
    return row


async def action_appointment_update(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """``AppointmentUpdate.pm``: change the given fields, keep every unset one."""
    del ticket_id
    action = "AppointmentUpdate"
    actor = _acting_user(config, user_id, action)
    zone = await _zone(sysconfig)
    if zone is None:
        _skip(action, "OTRSTimeZone is not a valid time zone")
        return
    row = await _appointment(session, config, action)
    if row is None:
        return
    if _too_long(config, action):
        return
    values: dict[str, Any] = {}
    for key, field in (("StartTime", "start_time"), ("EndTime", "end_time")):
        raw = _text(config, key)
        if raw is None:
            continue
        parsed = _parse_time(raw, zone)
        if parsed is None:
            _skip(action, f"{key} is not a date")
            return
        values[field] = parsed
    for key, field in (
        ("Title", "title"),
        ("Description", "description"),
        ("Location", "location"),
    ):
        text = _text(config, key)
        if text is not None:
            values[field] = text
    if "AllDay" in config:
        values["all_day"] = perl_truthy(config.get("AllDay"))
    for key, field in (("TeamID", "team_id"), ("ResourceID", "resource_id")):
        ids = _split_list(_text(config, key))
        if ids:
            values[field] = ",".join(ids)
    new_start = values.get("start_time", row.start_time)
    new_end = values.get("end_time", row.end_time)
    if new_start is not None and new_end is not None:
        # Stored times are naive UTC; compare like with like.
        a = new_start.replace(tzinfo=None) if new_start.tzinfo else new_start
        b = new_end.replace(tzinfo=None) if new_end.tzinfo else new_end
        if b < a:
            _skip(action, "EndTime is before StartTime")
            return
    recurrence = _recurrence(config, zone, action)
    if recurrence is not None:
        values["recurrence"] = recurrence
    _warn_ignored(config, action)
    try:
        await CalendarService(session).update_appointment(
            actor, row.id, AppointmentUpdateIn(**values)
        )
    except (CalendarForbidden, CalendarNotFound, AppointmentNotFound) as exc:
        _skip(action, str(exc))


async def action_appointment_remove(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """``AppointmentRemove.pm``: delete when the user holds ``create`` or ``rw``."""
    del ticket_id, sysconfig
    action = "AppointmentRemove"
    actor = _acting_user(config, user_id, action)
    row = await _appointment(session, config, action)
    if row is None:
        return
    try:
        calendar = await session.get(Calendar, row.calendar_id)
        if calendar is None:
            raise CalendarNotFound(f"calendar {row.calendar_id} not found")
        # Appointment.pm:1548-1560: AppointmentDelete requires create or rw. ``rw`` implies
        # ``create`` in PermissionEngine.groups_for_permission.
        allowed = await PermissionEngine(session).groups_for_permission(actor, "create")
        if calendar.group_id not in allowed:
            raise CalendarForbidden(
                f"user {actor} lacks create/rw on the calendar of appointment {row.id}"
            )
        await CalendarService(session).delete_appointment_unchecked(actor, row.id)
    except (CalendarForbidden, CalendarNotFound, AppointmentNotFound) as exc:
        _skip(action, str(exc))
