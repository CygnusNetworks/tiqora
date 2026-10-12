"""Dashboard-wide filters (pure, no I/O).

This module must not import ``schemas.py`` (``schemas.py`` imports ``DashboardFilters``).
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tiqora.reporting.definition import RelativeTimeRange, TimeRange

#: own alias (bounded 32-bit ids) so the filter model stays independent of ``schemas.py``
QueueIdIn = Annotated[int, Field(ge=1, le=2**31 - 1)]

TimeRangePreset = Literal["today", "7d", "30d", "90d", "this_month", "last_month"]

MAX_FILTER_QUEUES: Final = 50


class DashboardFilters(BaseModel):
    """Filters stored on a dashboard and applied to its filter-aware widgets."""

    model_config = ConfigDict(extra="forbid")

    #: empty = no queue narrowing; deduplicated and sorted
    queue_ids: list[QueueIdIn] = Field(default_factory=list, max_length=MAX_FILTER_QUEUES)
    #: ``None`` = each report widget keeps its own time range
    time_range: TimeRangePreset | None = None

    @field_validator("queue_ids")
    @classmethod
    def _dedupe_sorted(cls, v: list[int]) -> list[int]:
        return sorted(set(v))

    def is_empty(self) -> bool:
        return not self.queue_ids and self.time_range is None


#: preset -> (unit, count, include_current). Windows are local dates in the report time
#: zone; "include_current" means today / this month are part of the window.
#:   today       : the current day only
#:   7d/30d/90d  : the last N days including today
#:   this_month  : the current calendar month (1st .. end of month)
#:   last_month  : the previous complete calendar month
_PRESET_RANGES: Final[dict[str, tuple[str, int, bool]]] = {
    "today": ("day", 1, True),
    "7d": ("day", 7, True),
    "30d": ("day", 30, True),
    "90d": ("day", 90, True),
    "this_month": ("month", 1, True),
    "last_month": ("month", 1, False),
}


def report_time_range(preset: TimeRangePreset) -> TimeRange:
    """Map a dashboard time-range preset to a reporting ``TimeRange``."""
    unit, count, include_current = _PRESET_RANGES[preset]
    return RelativeTimeRange(
        unit=unit,  # type: ignore[arg-type]
        count=count,
        include_current=include_current,
    )


def filters_key(f: DashboardFilters) -> str | None:
    """Counter-history key suffix; only queue filters change counter values."""
    if not f.queue_ids:
        return None
    digest = hashlib.sha1(  # noqa: S324 - cache key, not security
        ",".join(str(i) for i in sorted(set(f.queue_ids))).encode("ascii"),
        usedforsecurity=False,
    ).hexdigest()
    return f"q:{digest[:8]}"


def effective_filters(f: DashboardFilters, *, pinned: bool) -> DashboardFilters:
    """A pinned widget ignores the dashboard filters."""
    return DashboardFilters() if pinned else f
