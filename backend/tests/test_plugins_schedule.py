from datetime import datetime

import pytest

from tiqora.plugins.tasks import Schedule, next_due

CREATED = datetime(2026, 10, 10, 4, 0)


def test_interval_never_run_is_due_at_creation() -> None:
    assert next_due(Schedule(every_seconds=300), last_run_at=None, created_at=CREATED) == CREATED


def test_interval_after_last_run() -> None:
    last = datetime(2026, 10, 10, 10, 0)
    assert next_due(Schedule(every_seconds=300), last_run_at=last, created_at=CREATED) == datetime(
        2026, 10, 10, 10, 5
    )


def test_daily_not_due_at_creation() -> None:
    assert next_due(Schedule(daily_at="03:00"), last_run_at=None, created_at=CREATED) == datetime(
        2026, 10, 11, 3, 0
    )


def test_daily_leading_zero_time() -> None:
    assert next_due(Schedule(daily_at="09:05"), last_run_at=None, created_at=CREATED) == datetime(
        2026, 10, 10, 9, 5
    )


def test_cron_after_last_run() -> None:
    s = Schedule(cron="0 */2 * * *")
    assert next_due(s, last_run_at=datetime(2026, 10, 10, 4, 0), created_at=CREATED) == datetime(
        2026, 10, 10, 6, 0
    )


def test_cron_uses_creation_when_never_run() -> None:
    s = Schedule(cron="0 */2 * * *")
    assert next_due(s, last_run_at=None, created_at=CREATED) == datetime(2026, 10, 10, 6, 0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"every_seconds": 60, "daily_at": "03:00"},
        {"every_seconds": 10},
        {"daily_at": "25:00"},
        {"daily_at": "3:00"},
        {"cron": "nope"},
    ],
)
def test_validate_rejects(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Zeitplan"):
        Schedule(**kwargs).validate()  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "s",
    [Schedule(every_seconds=30), Schedule(daily_at="23:59"), Schedule(cron="*/5 * * * *")],
)
def test_validate_accepts(s: Schedule) -> None:
    assert s.validate() is None


@pytest.mark.parametrize(
    ("s", "text"),
    [
        (Schedule(every_seconds=60), "jede Minute"),
        (Schedule(every_seconds=300), "alle 5 Minuten"),
        (Schedule(every_seconds=90), "alle 90 Sekunden"),
        (Schedule(every_seconds=3600), "jede Stunde"),
        (Schedule(every_seconds=7200), "alle 2 Stunden"),
        (Schedule(daily_at="03:00"), "täglich um 03:00 UTC"),
        (Schedule(cron="0 3 * * 1"), "nach Zeitplan „0 3 * * 1“ (UTC)"),
    ],
)
def test_describe(s: Schedule, text: str) -> None:
    assert s.describe() == text


@pytest.mark.parametrize("cron", ["0 0 31 2 *", "0 0 30 2 *", "0 0 31 4 *"])
def test_validate_rejects_never_firing_cron(cron: str) -> None:
    with pytest.raises(ValueError, match="Zeitplan"):
        Schedule(cron=cron).validate()


def test_validate_rejects_trailing_newline_time() -> None:
    with pytest.raises(ValueError, match="Zeitplan"):
        Schedule(daily_at="03:00\n").validate()


def test_validated_leap_day_cron_next_due_never_raises() -> None:
    s = Schedule(cron="0 0 29 2 *")
    s.validate()
    assert next_due(s, last_run_at=None, created_at=datetime(2026, 3, 1)) == datetime(2028, 2, 29)
    # century non-leap year: after 2096-02-29 the next one is 2104 (8-year gap, 2100 skipped)
    assert next_due(s, last_run_at=datetime(2096, 2, 29), created_at=CREATED) == datetime(
        2104, 2, 29
    )
