"""Pure unit tests for recurrence expansion and ICS export (no DB)."""

from __future__ import annotations

from datetime import datetime

from tiqora.calendar.ics import appointment_to_vevent, build_ical
from tiqora.calendar.recurrence import (
    expand_occurrences,
    parse_exclude_list,
    serialize_exclude_list,
)
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment


def test_non_recurring_appointment_yields_single_occurrence_if_in_range() -> None:
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 3, 1, 9, 0),
            end_time=datetime(2026, 3, 1, 10, 0),
            recur_type=None,
            recur_interval=None,
            recur_count=None,
            recur_until=None,
            exclude=None,
            range_start=datetime(2026, 3, 1),
            range_end=datetime(2026, 3, 2),
        )
    )
    assert len(occs) == 1
    assert occs[0].is_recurring is False


def test_non_recurring_appointment_outside_range_is_excluded() -> None:
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 3, 1, 9, 0),
            end_time=datetime(2026, 3, 1, 10, 0),
            recur_type=None,
            recur_interval=None,
            recur_count=None,
            recur_until=None,
            exclude=None,
            range_start=datetime(2026, 4, 1),
            range_end=datetime(2026, 4, 2),
        )
    )
    assert occs == []


def test_daily_recurrence_with_count() -> None:
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 1, 1, 9, 0),
            end_time=datetime(2026, 1, 1, 9, 30),
            recur_type="Daily",
            recur_interval=1,
            recur_count=3,
            recur_until=None,
            exclude=None,
            range_start=datetime(2026, 1, 1),
            range_end=datetime(2026, 2, 1),
        )
    )
    assert [o.start.day for o in occs] == [1, 2, 3]
    assert all(o.is_recurring for o in occs)


def test_weekly_recurrence_respects_interval_and_until() -> None:
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 1, 1, 9, 0),
            end_time=datetime(2026, 1, 1, 10, 0),
            recur_type="Weekly",
            recur_interval=2,
            recur_count=None,
            recur_until=datetime(2026, 2, 1),
            exclude=None,
            range_start=datetime(2026, 1, 1),
            range_end=datetime(2026, 3, 1),
        )
    )
    days = [o.start.day for o in occs]
    # 2026-01-01, 01-15, 01-29 (interval=2 weeks); next would be 02-12 > until
    assert days == [1, 15, 29]


def test_monthly_recurrence_clamps_short_months() -> None:
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 1, 31, 9, 0),
            end_time=datetime(2026, 1, 31, 10, 0),
            recur_type="Monthly",
            recur_interval=1,
            recur_count=3,
            recur_until=None,
            exclude=None,
            range_start=datetime(2026, 1, 1),
            range_end=datetime(2026, 5, 1),
        )
    )
    # Jan 31 -> Feb has no 31st (clamped to 28) -> Mar 28 (not 31, since it steps from Feb 28)
    assert occs[0].start == datetime(2026, 1, 31, 9, 0)
    assert occs[1].start == datetime(2026, 2, 28, 9, 0)


def test_exclude_list_skips_occurrence() -> None:
    excluded = [datetime(2026, 1, 3, 9, 0)]
    occs = list(
        expand_occurrences(
            start_time=datetime(2026, 1, 1, 9, 0),
            end_time=datetime(2026, 1, 1, 9, 30),
            recur_type="Daily",
            recur_interval=1,
            recur_count=5,
            recur_until=None,
            exclude=excluded,
            range_start=datetime(2026, 1, 1),
            range_end=datetime(2026, 2, 1),
        )
    )
    assert [o.start.day for o in occs] == [1, 2, 4, 5]


def test_exclude_list_serialize_roundtrip() -> None:
    values = [datetime(2026, 1, 3, 9, 0), datetime(2026, 1, 10, 9, 0)]
    raw = serialize_exclude_list(values)
    assert parse_exclude_list(raw) == values
    assert parse_exclude_list(None) == []
    assert parse_exclude_list("not json") == []


def test_ics_vevent_contains_rrule_and_exdate() -> None:
    appt = CalendarAppointment(
        id=1,
        calendar_id=1,
        unique_id="20260101T090000-abc123@tiqora",
        title="Weekly sync",
        description="Team sync",
        location="Room 1",
        start_time=datetime(2026, 1, 1, 9, 0),
        end_time=datetime(2026, 1, 1, 9, 30),
        all_day=0,
        recur_type="Weekly",
        recur_interval=2,
        recur_count=None,
        recur_until=datetime(2026, 6, 1),
    )
    vevent = appointment_to_vevent(appt, exclude=[datetime(2026, 1, 15, 9, 0)])
    assert "BEGIN:VEVENT" in vevent
    assert "SUMMARY:Weekly sync" in vevent
    assert "RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=20260601T000000" in vevent
    assert "EXDATE:20260115T090000" in vevent
    assert "END:VEVENT" in vevent


def test_ics_escapes_special_characters() -> None:
    appt = CalendarAppointment(
        id=1,
        calendar_id=1,
        unique_id="uid-1@tiqora",
        title="Meeting; important, notes\nhere",
        description=None,
        location=None,
        start_time=datetime(2026, 1, 1, 9, 0),
        end_time=datetime(2026, 1, 1, 9, 30),
        all_day=0,
    )
    vevent = appointment_to_vevent(appt)
    assert "SUMMARY:Meeting\\; important\\, notes\\nhere" in vevent


def test_build_ical_wraps_calendar() -> None:
    cal = Calendar(id=1, group_id=1, name="Team Calendar", salt_string="x", color="#ff0000")
    appt = CalendarAppointment(
        id=1,
        calendar_id=1,
        unique_id="uid-1@tiqora",
        title="Standup",
        start_time=datetime(2026, 1, 1, 9, 0),
        end_time=datetime(2026, 1, 1, 9, 15),
        all_day=0,
    )
    body = build_ical(cal, [appt])
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.rstrip().endswith("END:VCALENDAR")
    assert "X-WR-CALNAME:Team Calendar" in body
    assert body.count("BEGIN:VEVENT") == 1
