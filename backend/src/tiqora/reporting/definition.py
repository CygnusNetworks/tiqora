"""Report definitions: JSON schema (version 1), time windows, validation, hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Annotated, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tiqora.domain.timezones import valid_time_zone
from tiqora.extensions.registry import Registry
from tiqora.filters import Filter, FilterError, validate_filter
from tiqora.reporting.semantic import REPORT_SOURCES
from tiqora.reporting.sql import Grain

if TYPE_CHECKING:
    from tiqora.reporting.semantic import ReportSource

SCHEMA_VERSION: Final = 1
MAX_RANGE_DAYS: Final = 3660
VisualizationType = Literal[
    "table", "pivot", "bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"
]
_GRAIN_KINDS = frozenset({"date", "local_date"})


class RelativeTimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["relative"] = "relative"
    unit: Literal["day", "week", "month", "quarter", "year"]
    count: int = Field(ge=1, le=MAX_RANGE_DAYS)
    include_current: bool = False


class AbsoluteTimeRange(BaseModel):
    """Local dates in the report time zone, both inclusive."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute"] = "absolute"
    start: date
    end: date

    @model_validator(mode="after")
    def _check_span(self) -> AbsoluteTimeRange:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if (self.end - self.start).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"time range spans more than {MAX_RANGE_DAYS} days")
        return self


TimeRange = Annotated[RelativeTimeRange | AbsoluteTimeRange, Field(discriminator="kind")]


class DimensionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    grain: Grain | None = None  # date dimensions only; default "day"
    choices: list[str] = Field(default_factory=list, max_length=10)  # runtime alternatives
    top_n: int | None = Field(None, ge=1, le=100)  # rest folded into "__other__"


class MeasureRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str


class ReportParameter(BaseModel):
    """Znuny "changeable at runtime"."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    field: str  # dimension key or filter-only field key of the source
    op: Literal["in", "eq", "is_true", "is_false"] = "in"
    label_key: str | None = None
    allowed_values: list[str | int] | None = None  # None = anything the viewer may see
    default: list[str | int] | None = None
    required: bool = False


class SortSpec(BaseModel):
    key: str  # dimension or measure key
    direction: Literal["asc", "desc"] = "desc"


class Totals(BaseModel):
    grand: bool = False  # one total row
    rows: bool = False  # pivot: total per first-dimension value
    columns: bool = False  # pivot: total per second-dimension value


class Visualization(BaseModel):
    type: VisualizationType = "table"
    series_dimension: int | None = None  # index of the dimension that forms the series
    chart_measures: list[int] | None = None  # measure indices plotted; None = all


class ReportDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: str
    dimensions: list[DimensionRef] = Field(default_factory=list, max_length=3)
    measures: list[MeasureRef] = Field(min_length=1, max_length=10)
    filter: Filter | None = None  # wave-0 filter language; keys = source dimension/filter fields
    parameters: list[ReportParameter] = Field(default_factory=list, max_length=10)
    time_field: str | None = None  # None -> source.default_time_field
    time_range: TimeRange | None = None  # None -> no window (no time field) or last 30 days
    time_zone: str | None = None  # None -> the viewer's zone
    visualization: Visualization = Field(default_factory=Visualization)
    sort: list[SortSpec] = Field(default_factory=list, max_length=3)
    limit: int | None = Field(None, ge=1, le=10_000)
    totals: Totals = Field(default_factory=Totals)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start_utc: datetime
    end_utc: datetime  # naive UTC, [start, end)
    start_date: date
    end_date: date  # local, inclusive
    time_zone: str


class DefinitionError(ValueError):
    def __init__(self, message: str, *, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def _add_months(d: date, months: int) -> date:
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def _unit_start(unit: str, today: date) -> date:
    if unit == "day":
        return today
    if unit == "week":
        return today - timedelta(days=today.weekday())
    if unit == "month":
        return today.replace(day=1)
    if unit == "quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    return date(today.year, 1, 1)


def _shift_unit(unit: str, start: date, count: int) -> date:
    """``start`` (a unit start) moved by ``count`` units (negative = back)."""
    if unit == "day":
        return start + timedelta(days=count)
    if unit == "week":
        return start + timedelta(weeks=count)
    if unit == "month":
        return _add_months(start, count)
    if unit == "quarter":
        return _add_months(start, 3 * count)
    return date(start.year + count, 1, 1)


def _local_midnight_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tz).astimezone(UTC).replace(tzinfo=None)


def resolve_time_window(tr: TimeRange, zone: str, now_utc: datetime) -> TimeWindow:
    """Resolve ``tr`` to UTC bounds in ``zone``.

    Relative, ``include_current`` False: the ``count`` complete units before the
    current one; True: the current unit plus ``count - 1`` before it. Weeks
    start Monday, quarters in Jan/Apr/Jul/Oct.
    """
    tz = ZoneInfo(zone)
    if isinstance(tr, AbsoluteTimeRange):
        first, last = tr.start, tr.end
    else:
        today = now_utc.replace(tzinfo=UTC).astimezone(tz).date()
        current = _unit_start(tr.unit, today)
        try:
            if tr.include_current:
                first = _shift_unit(tr.unit, current, -(tr.count - 1))
                last = _shift_unit(tr.unit, current, 1) - timedelta(days=1)
            else:
                first = _shift_unit(tr.unit, current, -tr.count)
                last = current - timedelta(days=1)
        except (ValueError, OverflowError):
            raise DefinitionError(
                "Der Zeitraum liegt außerhalb des darstellbaren Bereichs.", path="time_range"
            ) from None
    return TimeWindow(
        start_utc=_local_midnight_utc(first, tz),
        end_utc=_local_midnight_utc(last + timedelta(days=1), tz),
        start_date=first,
        end_date=last,
        time_zone=zone,
    )


def _check_parameter(src: ReportSource, p: ReportParameter, path: str) -> None:
    spec = src.filter_spec(p.field)
    if spec is None:
        raise DefinitionError(f"Unbekanntes Filterfeld „{p.field}“.", path=f"{path}.field")
    if p.op not in spec.allowed_ops():
        raise DefinitionError(
            f"Für das Feld „{p.field}“ ist „{p.op}“ nicht möglich.", path=f"{path}.op"
        )
    for name, values in (("allowed_values", p.allowed_values), ("default", p.default)):
        for j, v in enumerate(values or []):
            ok = (
                isinstance(v, int) and not isinstance(v, bool)
                if spec.type in ("id", "int")
                else isinstance(v, str)
            )
            if not ok:
                raise DefinitionError(
                    f"Ungültiger Wert für das Feld „{p.field}“.", path=f"{path}.{name}[{j}]"
                )
            if spec.type == "enum" and v not in spec.enum_values:
                raise DefinitionError(f"„{v}“ ist kein erlaubter Wert.", path=f"{path}.{name}[{j}]")
    if p.allowed_values is not None and p.default is not None:
        for j, v in enumerate(p.default):
            if v not in p.allowed_values:
                raise DefinitionError(
                    f"„{v}“ ist nicht unter den erlaubten Werten.", path=f"{path}.default[{j}]"
                )


def validate_definition(defn: ReportDefinition, registry: Registry) -> ReportSource:
    """Check ``defn`` against its source; returns the source or raises :class:`DefinitionError`."""
    src = registry.get(REPORT_SOURCES, defn.source)
    if src is None:
        raise DefinitionError(f"Unbekannte Datenquelle „{defn.source}“.", path="source")

    seen: set[str] = set()
    for i, ref in enumerate(defn.dimensions):
        path = f"dimensions[{i}]"
        dim = src.dimension(ref.key)
        if dim is None:
            raise DefinitionError(f"Unbekannte Dimension „{ref.key}“.", path=f"{path}.key")
        if ref.key in seen:
            raise DefinitionError(f"Dimension „{ref.key}“ ist doppelt.", path=f"{path}.key")
        seen.add(ref.key)
        if ref.grain is not None and dim.kind not in _GRAIN_KINDS:
            raise DefinitionError(
                f"Die Dimension „{ref.key}“ hat keine Zeitauflösung.", path=f"{path}.grain"
            )
        for j, choice in enumerate(ref.choices):
            if src.dimension(choice) is None:
                raise DefinitionError(
                    f"Unbekannte Dimension „{choice}“.", path=f"{path}.choices[{j}]"
                )

    seen = set()
    for i, mref in enumerate(defn.measures):
        if src.measure(mref.key) is None:
            raise DefinitionError(f"Unbekannte Kennzahl „{mref.key}“.", path=f"measures[{i}].key")
        if mref.key in seen:
            raise DefinitionError(f"Kennzahl „{mref.key}“ ist doppelt.", path=f"measures[{i}].key")
        seen.add(mref.key)

    if defn.time_field is not None and defn.time_field not in src.time_fields:
        raise DefinitionError(
            f"„{defn.time_field}“ ist kein Zeitfeld dieser Datenquelle.", path="time_field"
        )
    if defn.time_zone is not None and valid_time_zone(defn.time_zone) is None:
        raise DefinitionError(f"Unbekannte Zeitzone „{defn.time_zone}“.", path="time_zone")

    seen = set()
    for i, param in enumerate(defn.parameters):
        if param.key in seen:
            raise DefinitionError(
                f"Parameter „{param.key}“ ist doppelt.", path=f"parameters[{i}].key"
            )
        seen.add(param.key)
        _check_parameter(src, param, f"parameters[{i}]")

    viz = defn.visualization
    if viz.series_dimension is not None and not 0 <= viz.series_dimension < len(defn.dimensions):
        raise DefinitionError(
            "Die Serien-Dimension liegt außerhalb der Dimensionen.",
            path="visualization.series_dimension",
        )
    for j, idx in enumerate(viz.chart_measures or []):
        if not 0 <= idx < len(defn.measures):
            raise DefinitionError(
                "Die Kennzahl liegt außerhalb der Kennzahlen.",
                path=f"visualization.chart_measures[{j}]",
            )

    sortable = {r.key for r in defn.dimensions} | {m.key for m in defn.measures}
    for i, spec_ in enumerate(defn.sort):
        if spec_.key not in sortable:
            raise DefinitionError(
                f"Sortierung nach „{spec_.key}“ ist nicht möglich.", path=f"sort[{i}].key"
            )

    if defn.filter is not None:
        try:
            validate_filter(defn.filter, src.filter_spec)
        except FilterError as exc:
            raise DefinitionError(exc.message, path=f"filter.{exc.path}") from exc
    return src


def definition_hash(defn: ReportDefinition) -> str:
    payload = json.dumps(
        defn.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
