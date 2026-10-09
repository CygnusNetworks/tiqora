"""API models of the reporting foundation (status, backfill and report results)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.reporting.sql import Grain

#: One result cell: dimension values (ids, strings, ISO dates) and measure values.
Cell = str | int | float | None
ColumnType = Literal[
    "id",
    "string",
    "enum",
    "date",
    "hour",
    "weekday",
    "count",
    "number",
    "minutes",
    "hours",
    "days",
    "units",
    "percent",
]


class FactsStatusOut(BaseModel):
    """Whether the derived report data is usable, and how far the first build is."""

    ready: bool
    as_of: UtcDateTime | None
    backfill_status: Literal["pending", "running", "done"]
    backfill_job_id: int | None
    #: 0..1 while the backfill runs, otherwise ``None``
    backfill_progress: float | None


class ReportingStatusOut(FactsStatusOut):
    """Admin view: the status plus the bookkeeping behind it."""

    last_history_id: int
    history_lag: int
    gap_count: int
    dirty_count: int
    last_snapshot_date: date | None
    snapshot_time_zone: str | None
    fact_rows: int
    event_rows: int


class ReportBackfillIn(BaseModel):
    since: date | None = None
    restart: bool = True
    snapshots: bool = True


class ResultColumnOut(BaseModel):
    key: str
    role: Literal["dimension", "measure"]
    type: ColumnType
    label_key: str
    grain: Grain | None = None
    i18n_values: str | None = None


class ReportTotalsOut(BaseModel):
    #: aligned with the columns; dimension cells ``None``
    grand: list[Cell] | None = None
    #: first-dimension value -> row (that dimension cell set, the others ``None``)
    rows: dict[str, list[Cell]] = Field(default_factory=dict)
    #: second-dimension value -> row
    columns: dict[str, list[Cell]] = Field(default_factory=dict)


class TimeWindowOut(BaseModel):
    start: UtcDateTime
    end: UtcDateTime
    start_date: date
    end_date: date
    time_zone: str


class ReportResultOut(BaseModel):
    report_key: str | None
    columns: list[ResultColumnOut]
    rows: list[list[Cell]]
    #: column key -> str(value) -> label
    labels: dict[str, dict[str, str]]
    totals: ReportTotalsOut | None
    previous: ReportTotalsOut | None
    #: rows after the definition's ``limit``, before the row cap (``truncated``)
    row_count: int
    #: rows cut by the row cap, or percentile samples capped
    truncated: bool
    time_window: TimeWindowOut | None
    generated_at: UtcDateTime
    cached: bool
    facts: FactsStatusOut
