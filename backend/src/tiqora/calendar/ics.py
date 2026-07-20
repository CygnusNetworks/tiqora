"""Minimal RFC 5545 (iCalendar) export — no external dependency.

Emits one VEVENT per parent appointment (using a native RRULE line for
recurring series, plus EXDATE lines for excluded occurrences) rather than one
VEVENT per expanded occurrence — this is both smaller and lets calendar
clients (Znuny, Thunderbird, Google Calendar, …) apply their own recurrence
rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tiqora.calendar.recurrence import parse_exclude_list
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment

_DTSTAMP_FMT = "%Y%m%dT%H%M%SZ"
_DT_FMT = "%Y%m%dT%H%M%S"
_DATE_FMT = "%Y%m%d"

_FREQ_MAP = {"Daily": "DAILY", "Weekly": "WEEKLY", "Monthly": "MONTHLY", "Yearly": "YEARLY"}


def _fold(line: str) -> str:
    """Fold lines >75 octets per RFC 5545 §3.1 (naive char-based fold)."""
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    rest = line[75:]
    while rest:
        parts.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(parts)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fmt_dt(dt: datetime, *, all_day: bool) -> str:
    if all_day:
        return dt.strftime(_DATE_FMT)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
        return dt.strftime(_DT_FMT) + "Z"
    return dt.strftime(_DT_FMT)


def _rrule_line(appt: CalendarAppointment) -> str | None:
    if not appt.recur_type or appt.recur_type not in _FREQ_MAP:
        return None
    parts = [f"FREQ={_FREQ_MAP[appt.recur_type]}"]
    if appt.recur_interval and appt.recur_interval > 1:
        parts.append(f"INTERVAL={appt.recur_interval}")
    if appt.recur_count:
        parts.append(f"COUNT={appt.recur_count}")
    elif appt.recur_until:
        parts.append(f"UNTIL={_fmt_dt(appt.recur_until, all_day=bool(appt.all_day))}")
    return "RRULE:" + ";".join(parts)


def appointment_to_vevent(appt: CalendarAppointment, exclude: list[datetime] | None = None) -> str:
    all_day = bool(appt.all_day)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_escape(appt.unique_id)}",
        f"DTSTAMP:{datetime.now(UTC).strftime(_DTSTAMP_FMT)}",
        f"DTSTART{';VALUE=DATE' if all_day else ''}:{_fmt_dt(appt.start_time, all_day=all_day)}",
        f"DTEND{';VALUE=DATE' if all_day else ''}:{_fmt_dt(appt.end_time, all_day=all_day)}",
        f"SUMMARY:{_escape(appt.title)}",
    ]
    if appt.description:
        lines.append(f"DESCRIPTION:{_escape(appt.description)}")
    if appt.location:
        lines.append(f"LOCATION:{_escape(appt.location)}")
    rrule = _rrule_line(appt)
    if rrule:
        lines.append(rrule)
    for ex in exclude or []:
        lines.append(f"EXDATE:{_fmt_dt(ex, all_day=all_day)}")
    lines.append("END:VEVENT")
    return "\r\n".join(_fold(line) for line in lines)


def build_ical(calendar_row: Calendar, appointments: list[CalendarAppointment]) -> str:
    """Build a full VCALENDAR document for one Znuny ``calendar`` row."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Tiqora//Calendar Export//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(calendar_row.name)}",
    ]
    # Header/footer lines are folded individually; VEVENT blocks are already
    # internally folded by appointment_to_vevent() and must not be re-folded
    # as if each block were a single (long) line.
    header = "\r\n".join(_fold(line) for line in lines)
    vevents = [
        appointment_to_vevent(appt, parse_exclude_list(appt.recur_exclude)) for appt in appointments
    ]
    footer = _fold("END:VCALENDAR")
    return "\r\n".join([header, *vevents, footer]) + "\r\n"
