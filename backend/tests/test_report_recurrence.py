from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from tiqora.reporting.recurrence import Recurrence, describe, next_occurrence, upcoming
from tiqora.reporting.texts import load_texts

BER = "Europe/Berlin"


def utc(*a: int) -> datetime:
    return datetime(*a)  # naive UTC


def test_daily_basic_and_strictly_after() -> None:
    r = Recurrence(kind="daily", time="07:00")
    assert next_occurrence(r, BER, utc(2026, 10, 11, 4, 59)) == utc(2026, 10, 11, 5, 0)  # CEST +2
    assert next_occurrence(r, BER, utc(2026, 10, 11, 5, 0)) == utc(
        2026, 10, 12, 5, 0
    )  # strictly after


def test_workdays_only() -> None:
    r = Recurrence(kind="daily", time="07:00", workdays_only=True)
    assert next_occurrence(r, BER, utc(2026, 10, 9, 6, 0)) == utc(2026, 10, 12, 5, 0)  # Fri -> Mon


def test_spring_forward_gap_shifts_forward() -> None:
    r = Recurrence(kind="daily", time="02:30")
    # 2027-03-28: 02:30 does not exist -> 03:30 CEST = 01:30 UTC
    assert next_occurrence(r, BER, utc(2027, 3, 27, 12, 0)) == utc(2027, 3, 28, 1, 30)


def test_fall_back_runs_once() -> None:
    r = Recurrence(kind="daily", time="02:30")
    first = next_occurrence(r, BER, utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_occurrence(r, BER, first) == utc(2026, 10, 26, 1, 30)


def test_weekly_weekdays() -> None:
    r = Recurrence(kind="weekly", time="08:15", weekdays=[1, 5])
    assert upcoming(r, BER, utc(2026, 10, 11, 0, 0), 3) == [
        utc(2026, 10, 12, 6, 15),
        utc(2026, 10, 16, 6, 15),
        utc(2026, 10, 19, 6, 15),
    ]


def test_monthly_31_clamps_to_last_day() -> None:
    r = Recurrence(kind="monthly", time="06:00", day_of_month=31)
    assert upcoming(r, BER, utc(2027, 1, 31, 12, 0), 3) == [
        utc(2027, 2, 28, 5, 0),
        utc(2027, 3, 31, 4, 0),
        utc(2027, 4, 30, 4, 0),
    ]


def test_yearly_feb_29() -> None:
    r = Recurrence(kind="yearly", time="06:00", month=2, day_of_month=29)
    assert next_occurrence(r, BER, utc(2027, 1, 1)) == utc(2027, 2, 28, 5, 0)
    assert next_occurrence(r, BER, utc(2027, 3, 1)) == utc(2028, 2, 29, 5, 0)


def test_other_zones() -> None:
    r = Recurrence(kind="daily", time="00:00")
    assert next_occurrence(r, "America/New_York", utc(2026, 10, 11, 3, 0)) == utc(
        2026, 10, 11, 4, 0
    )
    assert next_occurrence(r, "UTC", utc(2026, 10, 11, 0, 0)) == utc(2026, 10, 12, 0, 0)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "weekly", "time": "07:00"},
        {"kind": "weekly", "time": "07:00", "weekdays": [0]},
        {"kind": "weekly", "time": "07:00", "weekdays": [1, 1]},
        {"kind": "daily", "time": "24:00"},
        {"kind": "daily", "time": "7:00"},
        {"kind": "monthly", "time": "07:00"},
        {"kind": "daily", "time": "07:00", "day_of_month": 3},
        {"kind": "yearly", "time": "07:00", "day_of_month": 3},
        {"kind": "weekly", "time": "07:00", "weekdays": [1], "workdays_only": True},
    ],
)
def test_validation_german(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as exc:
        Recurrence.model_validate(bad)
    msg = str(exc.value.errors()[0]["msg"])
    assert msg.startswith("Value error, ")
    assert any(w in msg for w in ("Bitte", "Wochentag", "Uhrzeit", "Tag", "Monat", "nur"))


def test_german_message_for_missing_weekday() -> None:
    with pytest.raises(ValidationError) as exc:
        Recurrence(kind="weekly", time="07:00")
    assert "Bitte mindestens einen Wochentag wählen." in str(exc.value)


def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError):
        next_occurrence(Recurrence(kind="daily", time="07:00"), "Mars/Base", utc(2026, 1, 1))


def test_describe() -> None:
    de = load_texts("de")
    assert describe(Recurrence(kind="weekly", time="07:00", weekdays=[5, 1]), de) == (
        "Jeden Montag und Freitag um 07:00"
    )
    assert describe(Recurrence(kind="monthly", time="06:00", day_of_month=31), de) == (
        "Monatlich am 31. um 06:00 (in kürzeren Monaten am letzten Tag)"
    )
    assert describe(Recurrence(kind="daily", time="07:00"), de) == "Täglich um 07:00"
    assert describe(Recurrence(kind="daily", time="07:00", workdays_only=True), de) == (
        "Montag bis Freitag um 07:00"
    )
    assert describe(Recurrence(kind="yearly", time="06:00", month=3, day_of_month=5), de) == (
        "Jährlich am 5. März um 06:00"
    )
    assert (
        describe(Recurrence(kind="weekly", time="07:00", weekdays=[1, 3, 5]), de)
        == "Jeden Montag, Mittwoch und Freitag um 07:00"
    )
    assert describe(Recurrence(kind="weekly", time="07:00", weekdays=[1, 5]), load_texts("en")) == (
        "Every Monday and Friday at 07:00"
    )
