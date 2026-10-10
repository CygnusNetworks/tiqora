from datetime import datetime

import pytest

from tiqora.util.cron import CronExpr

D = datetime


@pytest.mark.parametrize(
    ("expr", "after", "expected"),
    [
        ("*/15 * * * *", D(2026, 10, 10, 10, 7), D(2026, 10, 10, 10, 15)),
        ("*/15 * * * *", D(2026, 10, 10, 10, 45), D(2026, 10, 10, 11, 0)),
        ("0 3 * * *", D(2026, 10, 10, 3, 0), D(2026, 10, 11, 3, 0)),  # strictly after
        ("0 3 * * *", D(2026, 10, 10, 2, 59, 30), D(2026, 10, 10, 3, 0)),
        ("30 2 1 * *", D(2026, 10, 10, 0, 0), D(2026, 11, 1, 2, 30)),
        ("0 0 * * 0", D(2026, 10, 10, 12, 0), D(2026, 10, 11, 0, 0)),  # 2026-10-11 is a Sunday
        ("0 0 * * 7", D(2026, 10, 10, 12, 0), D(2026, 10, 11, 0, 0)),
        ("0 0 13 * 5", D(2026, 10, 10, 12, 0), D(2026, 10, 13, 0, 0)),  # dom OR dow (13th wins)
        ("0 0 13 * 5", D(2026, 10, 13, 12, 0), D(2026, 10, 16, 0, 0)),  # next Friday
        ("0 0 29 2 *", D(2026, 3, 1, 0, 0), D(2028, 2, 29, 0, 0)),  # leap year
        ("5,35 8-10/2 * * 1-5", D(2026, 10, 12, 8, 6), D(2026, 10, 12, 8, 35)),
        ("5,35 8-10/2 * * 1-5", D(2026, 10, 12, 10, 36), D(2026, 10, 13, 8, 5)),
        ("0 0 * * 5-7", D(2026, 10, 10, 12, 0), D(2026, 10, 11, 0, 0)),  # 7 inside a range = Sunday
        ("10/20 * * * *", D(2026, 10, 10, 10, 11), D(2026, 10, 10, 10, 30)),  # a/n = a-max/n
        ("0 0 31 * *", D(2026, 11, 1, 0, 0), D(2026, 12, 31, 0, 0)),  # skips 30-day month
        ("59 23 31 12 *", D(2026, 12, 31, 23, 59), D(2027, 12, 31, 23, 59)),  # year rollover
    ],
)
def test_next_after(expr: str, after: datetime, expected: datetime) -> None:
    assert CronExpr.parse(expr).next_after(after) == expected


def test_dow_alias_seven_equals_zero() -> None:
    assert (
        CronExpr.parse("0 0 * * 7").weekdays
        == CronExpr.parse("0 0 * * 0").weekdays
        == frozenset({0})
    )


def test_dom_dow_and_semantics_when_only_one_restricted() -> None:
    # dom restricted, dow '*': the 13th only, never other weekdays
    assert CronExpr.parse("0 0 13 * *").next_after(D(2026, 10, 13, 0, 0)) == D(2026, 11, 13, 0, 0)


@pytest.mark.parametrize(
    "expr",
    [
        "60 * * * *",
        "* * *",
        "*/0 * * * *",
        "a * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "5-1 * * * *",
        "1,,2 * * * *",
        "* * * * * *",
        "",
    ],
)
def test_invalid(expr: str) -> None:
    with pytest.raises(ValueError, match="Zeitplan"):
        CronExpr.parse(expr)


def test_leap_day_century_gap_within_search_bound() -> None:
    assert CronExpr.parse("0 0 29 2 *").next_after(D(2096, 3, 1)) == D(2104, 2, 29)


def test_dow_only_with_dom_star_is_and() -> None:
    # Monday-only at a month boundary: 2026-11-02 is the first Monday after 2026-10-31
    assert CronExpr.parse("0 0 * * 1").next_after(D(2026, 10, 31, 12, 0)) == D(2026, 11, 2, 0, 0)
