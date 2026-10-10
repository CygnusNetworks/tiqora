"""API models of the reporting foundation (status, backfill, catalogue, runs and results)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from tiqora.domain.schemas import UtcDateTime
from tiqora.filters.catalog import MAX_IN_VALUES, MAX_STRING_LENGTH
from tiqora.jobs.schemas import JobAccepted
from tiqora.reporting.definition import MAX_KEY_LENGTH, ReportDefinition, TimeRange
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


# Request size limits (422 beyond them): parameter and choice counts follow the
# definition limits (10 parameters, 3 dimensions), parameter values the filter
# language (MAX_IN_VALUES values of at most MAX_STRING_LENGTH characters); export
# labels allow every column of a result (3 dimensions + 10 measures) with headroom.
MAX_RUN_PARAMETERS: Final = 10
MAX_DIMENSION_CHOICES: Final = 3
MAX_LABEL_COLUMNS: Final = 20
MAX_VALUE_LABELS_PER_COLUMN: Final = 500
MAX_LABEL_LENGTH: Final = 200
MAX_INFO_LINE_LENGTH: Final = 300

_ParamString = Annotated[str, Field(max_length=MAX_STRING_LENGTH)]
ParameterValue = (
    Annotated[list[_ParamString | int], Field(max_length=MAX_IN_VALUES)]
    | _ParamString
    | int
    | bool
    | None
)
_ColumnKey = Annotated[str, Field(max_length=MAX_KEY_LENGTH)]
_Label = Annotated[str, Field(max_length=MAX_LABEL_LENGTH)]


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
    parameters: dict[Annotated[str, Field(max_length=40)], ParameterValue] = Field(
        default_factory=dict, max_length=MAX_RUN_PARAMETERS
    )
    #: dimension index as a string ("0") -> dimension key
    dimension_choices: dict[Annotated[str, Field(max_length=3)], _ColumnKey] = Field(
        default_factory=dict, max_length=MAX_DIMENSION_CHOICES
    )
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
    column_labels: dict[_ColumnKey, _Label] = Field(
        default_factory=dict, max_length=MAX_LABEL_COLUMNS
    )
    #: frontend-translated enum values (column key -> value -> label)
    value_labels: dict[
        _ColumnKey,
        Annotated[dict[_Label, _Label], Field(max_length=MAX_VALUE_LABELS_PER_COLUMN)],
    ] = Field(default_factory=dict, max_length=MAX_LABEL_COLUMNS)
    totals_label: str = Field("Summe", max_length=40)
    #: lines of the "Info" sheet ("Zeitraum: …" etc.)
    info_lines: list[Annotated[str, Field(max_length=MAX_INFO_LINE_LENGTH)]] = Field(
        default_factory=list, max_length=20
    )


class ReportOwnerOut(BaseModel):
    user_id: int
    #: "First Last" (the login when both are empty)
    name: str


class ReportOut(BaseModel):
    """A catalogue entry: a built-in report or a saved one."""

    #: ``builtin.<name>`` or the saved report's id as a decimal string
    key: str
    kind: Literal["builtin", "saved"]
    id: int | None = None
    #: built-ins only
    category: str | None = None
    title_key: str | None = None
    description_key: str | None = None
    #: saved reports only (literal text)
    name: str | None = None
    description: str | None = None
    #: the definition as written (parameter controls are built from it); ``None`` only for
    #: a saved report whose stored definition can no longer be read (``definition_error``)
    definition: ReportDefinition | None
    definition_error: str | None = None
    overview: bool = False
    #: built-in, or saved without edit right for this viewer
    read_only: bool
    owner: ReportOwnerOut | None = None
    visibility: Literal["private", "groups", "all"] | None = None
    group_ids: list[int] = Field(default_factory=list)
    version: int | None = None
    copied_from: str | None = None
    changed_at: UtcDateTime | None = None


class ReportCatalogueOut(BaseModel):
    #: built-ins first (catalogue order), then saved reports by name
    reports: list[ReportOut]
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
