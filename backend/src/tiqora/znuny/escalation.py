"""Znuny-compatible escalation index builder.

Behavioural port of ``Kernel/System/Ticket.pm::TicketEscalationIndexBuild``
plus the working-time arithmetic from ``Kernel/System/DateTime.pm::Add``
(``AsWorkingTime => 1``) honouring ``TimeWorkingHours`` / ``TimeVacationDays``
/ ``TimeVacationDaysOneTime`` (with per-calendar variants) in OTRSTimeZone.

Ported decision points:

- state type matching ``^(merge|close|remove)`` (case-insensitive): zero all
  four escalation columns and stop.
- Escalation preferences come from the SLA row when ``ticket.sla_id`` is set,
  otherwise from the queue row (``first_response_time`` / ``update_time`` /
  ``solution_time`` in minutes; notify percentages do not affect the columns).
- First response done: any article with sender type ``agent`` and
  ``is_visible_for_customer = 1`` (``_TicketGetFirstResponse``).
- Update base time: reverse walk over articles — last customer contact wins,
  an agent reply after it stops the walk (``TicketEscalationIndexBuild``).
- Update escalation suppressed for ``^pending`` state types.
- Solution done: any ``ticket_history`` row with a closed state_id and history
  type StateUpdate/NewTicket (``_TicketGetClosed``).
- ``escalation_time`` = earliest of the computed destination times, else 0.

Documented divergences (golden-master validation recommended):

- Working-time addition walks hour-by-hour with DST-aware local time
  conversions; Znuny's midnight fast-path assumes 24-hour days and skips DST
  days hour-by-hour — around DST transitions results may differ by ±1 hour.
- Znuny reads TicketGet cached data; we always read the live ticket row.
"""

from __future__ import annotations

import re
import zoneinfo
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SECS_PER_HOUR = 3600
_NO_ESCALATION_STATE_RE = re.compile(r"^(merge|close|remove)", re.IGNORECASE)
_PENDING_STATE_RE = re.compile(r"^pending", re.IGNORECASE)

WorkingHours = dict[str, list[int]]
VacationDays = dict[int, dict[int, str]]
VacationDaysOneTime = dict[int, dict[int, dict[int, str]]]


def _tzinfo(tz_name: str) -> Any:
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return UTC


def destination_time_epoch(
    start_epoch: int,
    minutes: int,
    working_hours: WorkingHours,
    vacation_days: VacationDays,
    vacation_days_once: VacationDaysOneTime,
    tz_name: str,
) -> int:
    """Return the epoch after adding *minutes* of working time to *start_epoch*.

    Port of ``Kernel/System/DateTime.pm::Add(AsWorkingTime => 1)``: consume
    remaining seconds only during configured working hours, skipping vacation
    days; non-working periods advance the clock without consuming budget.
    If no working hours are configured at all, the start time is returned
    unchanged (Znuny returns success without moving the date).
    """
    remaining = minutes * 60
    if remaining <= 0:
        return start_epoch

    if not any(hours for hours in working_hours.values()):
        return start_epoch

    tz = _tzinfo(tz_name)
    hour_sets: dict[str, set[int]] = {day: set(hours) for day, hours in working_hours.items()}

    current = start_epoch
    guard = 0
    while remaining > 0:
        guard += 1
        if guard > 5_000_000:
            raise RuntimeError("destination_time_epoch: loop protection triggered")

        dt = datetime.fromtimestamp(current, tz=tz)
        day_abbr = _DAY_ABBR[dt.weekday()]
        is_vacation = (
            vacation_days.get(dt.month, {}).get(dt.day) is not None
            or vacation_days_once.get(dt.year, {}).get(dt.month, {}).get(dt.day) is not None
        )
        day_hours = hour_sets.get(day_abbr, set())
        is_working_day = not is_vacation and bool(day_hours)

        # Whole-day fast path at midnight (mirrors Znuny's optimization).
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            next_day = datetime.fromtimestamp(current + 24 * _SECS_PER_HOUR, tz=tz)
            # Only 24-hour days qualify (DST days fall through to hour loop).
            if (
                next_day.hour == 0
                and next_day.minute == 0
                and next_day.second == 0
                and next_day.day != dt.day
            ):
                if not is_working_day:
                    current += 24 * _SECS_PER_HOUR
                    continue
                working_secs_today = len(day_hours) * _SECS_PER_HOUR
                if remaining > working_secs_today:
                    remaining -= working_secs_today
                    current += 24 * _SECS_PER_HOUR
                    continue

        secs_into_hour = dt.minute * 60 + dt.second
        secs_to_next_hour = _SECS_PER_HOUR - secs_into_hour

        if is_working_day and dt.hour in day_hours:
            consume = min(secs_to_next_hour, remaining)
            remaining -= consume
            current += consume
        else:
            current += secs_to_next_hour

    return current


# ---------------------------------------------------------------------------
# DB lookups
# ---------------------------------------------------------------------------


async def _first_response_done(session: AsyncSession, ticket_id: int) -> bool:
    """True if the ticket already has a customer-visible agent article."""
    row = (
        await session.execute(
            text(
                "SELECT a.id FROM article a"
                " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid AND ast.name = 'agent'"
                " AND a.is_visible_for_customer = 1"
                " ORDER BY a.create_time LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None


async def _last_sender_time(session: AsyncSession, ticket_id: int) -> datetime | None:
    """Base time for the update escalation (reverse sender-history walk).

    Mirrors the SenderHistory loop in TicketEscalationIndexBuild: only visible
    agent/customer articles count; the newest customer contact wins unless an
    agent replied after it (then no update escalation → caller writes 0).
    """
    rows = (
        await session.execute(
            text(
                "SELECT ast.name, a.is_visible_for_customer, a.create_time"
                " FROM article a"
                " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid ORDER BY a.create_time ASC"
            ),
            {"tid": ticket_id},
        )
    ).fetchall()

    last_sender_time: datetime | None = None
    last_sender_type = ""

    for sender_type, visible, created in reversed(rows):
        if last_sender_time is None:
            last_sender_time = created

        if not visible:
            continue
        if sender_type not in ("agent", "customer"):
            continue

        if sender_type == "agent" and last_sender_type == "customer":
            break

        if sender_type == "customer":
            last_sender_type = "customer"
            last_sender_time = created

        if sender_type == "agent":
            last_sender_time = created
            break

    # Znuny: if the newest relevant sender is an agent (and no later customer
    # follow-up), the update escalation is still (re)started from that agent
    # article; only a completely article-less ticket yields no base time.
    return last_sender_time


async def _solution_done(session: AsyncSession, ticket_id: int) -> bool:
    """True if the ticket was ever set to a closed state (``_TicketGetClosed``)."""
    closed_rows = (
        await session.execute(
            text(
                "SELECT ts.id FROM ticket_state ts"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE tst.name = 'closed'"
            )
        )
    ).fetchall()
    closed_ids = [int(r[0]) for r in closed_rows]
    if not closed_ids:
        return False

    type_rows = (
        await session.execute(
            text("SELECT id FROM ticket_history_type WHERE name IN ('StateUpdate', 'NewTicket')")
        )
    ).fetchall()
    type_ids = [int(r[0]) for r in type_rows]
    if not type_ids:
        return False

    closed_in = ",".join(str(i) for i in sorted(closed_ids))
    types_in = ",".join(str(i) for i in sorted(type_ids))
    row = (
        await session.execute(
            text(
                "SELECT MAX(create_time) FROM ticket_history"
                f" WHERE ticket_id = :tid AND state_id IN ({closed_in})"
                f" AND history_type_id IN ({types_in})"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None and row[0] is not None


async def _escalation_preferences(
    session: AsyncSession, sla_id: int | None, queue_id: int
) -> tuple[int, int, int, str | None]:
    """Return (first_response, update, solution) minutes and calendar name."""
    if sla_id:
        row = (
            await session.execute(
                text(
                    "SELECT first_response_time, update_time, solution_time, calendar_name"
                    " FROM sla WHERE id = :sid"
                ),
                {"sid": sla_id},
            )
        ).first()
    else:
        row = (
            await session.execute(
                text(
                    "SELECT first_response_time, update_time, solution_time, calendar_name"
                    " FROM queue WHERE id = :qid"
                ),
                {"qid": queue_id},
            )
        ).first()
    if row is None:
        return 0, 0, 0, None
    calendar = str(row[3]) if row[3] else None
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), calendar


async def _calendar_config(
    sysconfig: SysConfig, calendar: str | None
) -> tuple[WorkingHours, VacationDays, VacationDaysOneTime, str]:
    """Fetch working hours / vacation days / time zone, honouring calendar suffix.

    Znuny only applies calendar-specific settings when
    ``TimeZone::Calendar<N>Name`` exists; the calendar time zone falls back to
    OTRSTimeZone.
    """
    tz = await sysconfig.otrs_time_zone()
    wh_key, vd_key, vdo_key = (
        "TimeWorkingHours",
        "TimeVacationDays",
        "TimeVacationDaysOneTime",
    )
    if calendar:
        calendar_name = await sysconfig.get(f"TimeZone::Calendar{calendar}Name")
        if calendar_name:
            wh_key = f"TimeWorkingHours::Calendar{calendar}"
            vd_key = f"TimeVacationDays::Calendar{calendar}"
            vdo_key = f"TimeVacationDaysOneTime::Calendar{calendar}"
            calendar_tz = await sysconfig.get(f"TimeZone::Calendar{calendar}")
            if calendar_tz:
                tz = str(calendar_tz)

    raw_wh = await sysconfig.get(wh_key) or {}
    raw_vd = await sysconfig.get(vd_key) or {}
    raw_vdo = await sysconfig.get(vdo_key) or {}

    # SysConfig YAML may deliver string keys/values; normalize to ints.
    working_hours: WorkingHours = {
        str(day): [int(h) for h in hours]
        for day, hours in raw_wh.items()
        if isinstance(hours, list)
    }
    vacation_days: VacationDays = {
        int(m): {int(d): str(name) for d, name in days.items()}
        for m, days in raw_vd.items()
        if isinstance(days, dict)
    }
    vacation_once: VacationDaysOneTime = {
        int(y): {
            int(m): {int(d): str(name) for d, name in days.items()}
            for m, days in months.items()
            if isinstance(days, dict)
        }
        for y, months in raw_vdo.items()
        if isinstance(months, dict)
    }
    return working_hours, vacation_days, vacation_once, tz


def _to_epoch(value: Any, tz_name: str) -> int:
    """Convert a DB datetime/string (OTRSTimeZone wall clock) to epoch seconds."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tzinfo(tz_name))
    return int(dt.timestamp())


async def _set_column(
    session: AsyncSession, ticket_id: int, column: str, value: int, user_id: int
) -> None:
    await session.execute(
        text(
            f"UPDATE ticket SET {column} = :val, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"val": value, "uid": user_id, "tid": ticket_id},
    )


async def escalation_index_build(
    session: AsyncSession,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Recompute the four escalation columns on the ticket row.

    Port of ``Ticket.pm::TicketEscalationIndexBuild``.
    """
    row = (
        await session.execute(
            text(
                "SELECT tst.name, t.sla_id, t.queue_id, t.create_time"
                " FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return

    state_type = str(row[0])
    sla_id = int(row[1]) if row[1] else None
    queue_id = int(row[2])
    create_time = row[3]

    if _NO_ESCALATION_STATE_RE.match(state_type):
        for column in (
            "escalation_response_time",
            "escalation_solution_time",
            "escalation_time",
            "escalation_update_time",
        ):
            await _set_column(session, ticket_id, column, 0, user_id)
        return

    first_response_min, update_min, solution_min, calendar = await _escalation_preferences(
        session, sla_id, queue_id
    )
    working_hours, vacation_days, vacation_once, tz = await _calendar_config(sysconfig, calendar)
    create_epoch = _to_epoch(create_time, tz)

    escalation_time = 0

    # --- first response ---
    if not first_response_min or await _first_response_done(session, ticket_id):
        await _set_column(session, ticket_id, "escalation_response_time", 0, user_id)
    else:
        dest = destination_time_epoch(
            create_epoch, first_response_min, working_hours, vacation_days, vacation_once, tz
        )
        await _set_column(session, ticket_id, "escalation_response_time", dest, user_id)
        escalation_time = dest

    # --- update (suppressed for pending states) ---
    if not update_min or _PENDING_STATE_RE.match(state_type):
        await _set_column(session, ticket_id, "escalation_update_time", 0, user_id)
    else:
        last_time = await _last_sender_time(session, ticket_id)
        if last_time is not None:
            dest = destination_time_epoch(
                _to_epoch(last_time, tz),
                update_min,
                working_hours,
                vacation_days,
                vacation_once,
                tz,
            )
            await _set_column(session, ticket_id, "escalation_update_time", dest, user_id)
            if escalation_time == 0 or dest < escalation_time:
                escalation_time = dest
        else:
            await _set_column(session, ticket_id, "escalation_update_time", 0, user_id)

    # --- solution ---
    if not solution_min or await _solution_done(session, ticket_id):
        await _set_column(session, ticket_id, "escalation_solution_time", 0, user_id)
    else:
        dest = destination_time_epoch(
            create_epoch, solution_min, working_hours, vacation_days, vacation_once, tz
        )
        await _set_column(session, ticket_id, "escalation_solution_time", dest, user_id)
        if escalation_time == 0 or dest < escalation_time:
            escalation_time = dest

    # --- combined escalation_time (earliest destination or 0) ---
    await _set_column(session, ticket_id, "escalation_time", escalation_time, user_id)


__all__ = [
    "destination_time_epoch",
    "escalation_index_build",
]
