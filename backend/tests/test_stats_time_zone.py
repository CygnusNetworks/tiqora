"""Stats and search day bounds follow the viewer's zone, not UTC days."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tiqora.api.v1.search import _day_end_ts, _day_start_ts
from tiqora.api.v1.stats import _bound
from tiqora.stats.service import _bucket_key


def test_date_upper_bound_covers_the_whole_day() -> None:
    # A bare date as ``date_to`` used to compare against 00:00 and drop the day.
    assert _bound(datetime(2026, 10, 7), "UTC", end=True) == datetime(
        2026, 10, 7, 23, 59, 59, 999999
    )


def test_naive_bounds_are_wall_clock_in_the_zone() -> None:
    # 2026-10-07 in Berlin (CEST) runs from 22:00 UTC the day before.
    assert _bound(datetime(2026, 10, 7), "Europe/Berlin", end=False) == datetime(2026, 10, 6, 22, 0)
    assert _bound(datetime(2026, 10, 7), "Europe/Berlin", end=True) == datetime(
        2026, 10, 7, 21, 59, 59, 999999
    )


def test_aware_bounds_are_instants() -> None:
    instant = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)
    assert _bound(instant, "America/New_York", end=True) == datetime(2026, 10, 6, 22, 0)


def test_buckets_use_the_zone_calendar_day() -> None:
    # 23:30 UTC on the 6th is already the 7th in Berlin.
    late = datetime(2026, 10, 6, 23, 30)
    assert _bucket_key(late, "day") == date(2026, 10, 6)
    assert _bucket_key(late, "day", "Europe/Berlin") == date(2026, 10, 7)


def test_search_day_bounds_in_zone() -> None:
    day = date(2026, 10, 7)
    assert _day_start_ts(day, "Europe/Berlin") == int(
        datetime(2026, 10, 6, 22, 0, tzinfo=UTC).timestamp()
    )
    assert _day_end_ts(day) == int(datetime(2026, 10, 7, 23, 59, 59, tzinfo=UTC).timestamp())
