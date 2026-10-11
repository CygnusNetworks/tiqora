from datetime import datetime

import pytest
from pydantic import ValidationError

from tiqora.dashboard.filters import (
    DashboardFilters,
    effective_filters,
    filters_key,
    report_time_range,
)
from tiqora.reporting.definition import resolve_time_window


def test_queue_ids_deduplicated_sorted_and_bounded() -> None:
    assert DashboardFilters(queue_ids=[5, 3, 5]).queue_ids == [3, 5]
    with pytest.raises(ValidationError):
        DashboardFilters(queue_ids=list(range(1, 52)))
    with pytest.raises(ValidationError):
        DashboardFilters(queue_ids=[0])
    with pytest.raises(ValidationError):
        DashboardFilters(queue_ids=[2**31])
    assert DashboardFilters(queue_ids=[2**31 - 1]).queue_ids == [2**31 - 1]


def test_extra_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        DashboardFilters.model_validate({"owner": [1]})
    with pytest.raises(ValidationError):
        DashboardFilters.model_validate({"time_range": "1y"})


def test_is_empty() -> None:
    assert DashboardFilters().is_empty()
    assert not DashboardFilters(queue_ids=[1]).is_empty()
    assert not DashboardFilters(time_range="today").is_empty()


# 2026-10-14 is a Wednesday; 09:00 UTC is 11:00 in Europe/Berlin (CEST).
_NOW = datetime(2026, 10, 14, 9, 0)


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    [
        ("today", "2026-10-14", "2026-10-14"),
        ("7d", "2026-10-08", "2026-10-14"),
        ("30d", "2026-09-15", "2026-10-14"),
        ("90d", "2026-07-17", "2026-10-14"),
        ("this_month", "2026-10-01", "2026-10-31"),
        ("last_month", "2026-09-01", "2026-09-30"),
    ],
)
def test_report_time_range_mapping_resolves_to_expected_window(
    preset: str, start: str, end: str
) -> None:
    window = resolve_time_window(report_time_range(preset), "Europe/Berlin", _NOW)  # type: ignore[arg-type]
    assert (window.start_date.isoformat(), window.end_date.isoformat()) == (start, end)


def test_report_time_range_window_uses_local_midnight_bounds() -> None:
    window = resolve_time_window(report_time_range("today"), "Europe/Berlin", _NOW)
    # CEST = UTC+2: local midnight of 14.10. is 22:00 UTC of 13.10.
    assert window.start_utc == datetime(2026, 10, 13, 22, 0)
    assert window.end_utc == datetime(2026, 10, 14, 22, 0)


def test_filters_key_only_for_queue_filters() -> None:
    assert filters_key(DashboardFilters()) is None
    assert filters_key(DashboardFilters(queue_ids=[3, 5])) == filters_key(
        DashboardFilters(queue_ids=[5, 3])
    )
    assert filters_key(DashboardFilters(time_range="7d")) is None
    key = filters_key(DashboardFilters(queue_ids=[3, 5]))
    assert key is not None
    assert key.startswith("q:")
    assert len(key) == 10
    assert key != filters_key(DashboardFilters(queue_ids=[3]))


def test_effective_filters_pinned_is_empty() -> None:
    f = DashboardFilters(queue_ids=[3], time_range="30d")
    assert effective_filters(f, pinned=True).is_empty()
    assert effective_filters(f, pinned=False) == f
