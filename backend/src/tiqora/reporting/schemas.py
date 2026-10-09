"""API models of the reporting foundation (status, backfill, catalogue, runs and results)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.jobs.schemas import JobAccepted
from tiqora.reporting.definition import ReportDefinition, TimeRange
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


ParameterValue = list[str | int] | str | int | bool | None


class ReportRunIn(BaseModel):
    """Body of ``POST /reports/run``: exactly one of ``report_key`` / ``definition``.

    Unknown fields are rejected (422): ``window_override``, ``extra_filter`` and
    ``last_snapshot_date`` exist only internally (``RunParams``) for the
    ``/stats`` wrappers and the engine, never as request fields.
    """

    model_config = ConfigDict(extra="forbid")

    report_key: str | None = Field(None, max_length=120)
    definition: ReportDefinition | None = None
    time_range: TimeRange | None = None
    grain: Grain | None = None
    time_zone: str | None = Field(None, max_length=64)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)
    #: dimension index as a string ("0") -> dimension key
    dimension_choices: dict[str, str] = Field(default_factory=dict)
    compare_previous: bool = False
    no_cache: bool = False
    force_job: bool = False


class ReportRunOut(BaseModel):
    """200: ``result`` (inline run or cache hit); 202: ``job`` (background run)."""

    result: ReportResultOut | None = None
    job: JobAccepted | None = None


class ReportExportIn(ReportRunIn):
    format: Literal["xlsx", "csv"] = "xlsx"
    title: str = Field("Bericht", min_length=1, max_length=120)
    #: frontend-translated column headers (column key -> header)
    column_labels: dict[str, str] = Field(default_factory=dict)
    #: frontend-translated enum values (column key -> value -> label)
    value_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    totals_label: str = Field("Summe", max_length=40)
    #: lines of the "Info" sheet ("Zeitraum: …" etc.)
    info_lines: list[str] = Field(default_factory=list, max_length=20)


class BuiltinReportOut(BaseModel):
    key: str
    category: str
    title_key: str
    description_key: str
    #: the catalogue definition as written (parameter controls are built from it)
    definition: ReportDefinition
    overview: bool
    read_only: bool = True


class ReportCatalogueOut(BaseModel):
    reports: list[BuiltinReportOut]
    facts: FactsStatusOut


class SourceDimensionOut(BaseModel):
    """A dimension, or (in ``filter_fields``) a filter-only field of a source.

    Filter-only fields have ``kind`` = their filter type, ``labels`` "none",
    no ``i18n_values`` / ``options_source`` and ``time_field`` false.
    """

    key: str
    label_key: str
    kind: str
    labels: str
    i18n_values: str | None
    filter_type: str | None
    options_source: str | None
    time_field: bool


class SourceMeasureOut(BaseModel):
    key: str
    label_key: str
    agg: str
    unit: str


class ReportSourceOut(BaseModel):
    key: str
    label_key: str
    dimensions: list[SourceDimensionOut]
    measures: list[SourceMeasureOut]
    filter_fields: list[SourceDimensionOut]
    default_time_field: str | None
