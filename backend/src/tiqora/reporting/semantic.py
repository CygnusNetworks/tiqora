"""Semantic layer: sources with their dimensions, measures and filter-only fields.

A :class:`ReportSource` describes one FROM clause (fact table, event table,
snapshot table or live Znuny tables) and what can be grouped, measured and
filtered on it. Column expressions are functions of the
:class:`CompileContext` so they can read ``now`` and the time-zone segments.
The compiler builds each expression once per compile.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import ColumnElement, FromClause

from tiqora.extensions.registry import ExtensionPoint
from tiqora.filters import FieldType, FilterFieldSpec
from tiqora.reporting.sql import TzSegment

if TYPE_CHECKING:
    from tiqora.reporting.definition import TimeWindow

DimensionKind = Literal["id", "string", "enum", "date", "local_date", "hour", "weekday"]
LabelSource = Literal[
    "queue",
    "state",
    "priority",
    "type",
    "service",
    "sla",
    "user",
    "customer_company",
    "customer_user",
    "ticket",
    "none",
]
MeasureAgg = Literal[
    "count", "count_if", "count_distinct", "sum", "avg", "min", "max", "p50", "p90", "rate"
]
MeasureUnit = Literal["count", "number", "minutes", "hours", "days", "units", "percent"]


@dataclass(frozen=True, slots=True)
class CompileContext:
    now: datetime  # naive UTC
    time_zone: str
    tz: tuple[TzSegment, ...]  # for to_local
    near_miss_percent: int
    window: TimeWindow | None


ExprFn = Callable[[CompileContext], ColumnElement[Any]]
BoolFn = Callable[[CompileContext], ColumnElement[bool]]


@dataclass(frozen=True, slots=True)
class Dimension:
    key: str
    label_key: str  # "reports.dimensions.<key>"
    kind: DimensionKind
    expr: (
        ExprFn  # raw column (naive UTC datetime for "date"/"hour"/"weekday"; DATE for "local_date")
    )
    labels: LabelSource = "none"
    i18n_values: str | None = None  # "reports.values.state_type" etc. for enums
    filter_type: FieldType | None = None  # wave-0 filter type; None = not filterable
    options_source: str | None = None  # wave-0 POST /reference/field-options key ("queue", ...)
    stock: bool = False  # daily snapshot date: values are stocks, not flows
    enum_values: tuple[str, ...] = ()  # allowed values when filter_type == "enum"


@dataclass(frozen=True, slots=True)
class FilterOnlyField:
    key: str
    label_key: str
    filter_type: FieldType
    expr: ExprFn


@dataclass(frozen=True, slots=True)
class Measure:
    key: str
    label_key: str  # "reports.measures.<key>"
    agg: MeasureAgg
    expr: ExprFn | None = None  # value for sum/avg/min/max/p50/p90/count_distinct
    condition: BoolFn | None = None  # count_if; rate numerator
    denominator: BoolFn | None = None  # rate
    unit: MeasureUnit = "count"
    stock: bool = False


def _no_where(ctx: CompileContext) -> list[ColumnElement[bool]]:
    return []


@dataclass(frozen=True, slots=True)
class ReportSource:
    key: str
    label_key: str
    from_clause: Callable[[CompileContext], FromClause]
    scope_column: ExprFn  # queue id column checked against the viewer's queues
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]
    filter_fields: tuple[FilterOnlyField, ...] = ()
    base_where: Callable[[CompileContext], list[ColumnElement[bool]]] = _no_where
    time_fields: tuple[str, ...] = ()  # date / local_date dimension keys
    default_time_field: str | None = None
    requires_facts: bool = True

    def dimension(self, key: str) -> Dimension | None:
        for d in self.dimensions:
            if d.key == key:
                return d
        return None

    def measure(self, key: str) -> Measure | None:
        for m in self.measures:
            if m.key == key:
                return m
        return None

    def filter_field(self, key: str) -> FilterOnlyField | None:
        for f in self.filter_fields:
            if f.key == key:
                return f
        return None

    def filter_spec(self, key: str) -> FilterFieldSpec | None:
        """Filter field of a filterable dimension or a filter-only field."""
        dim = self.dimension(key)
        if dim is not None:
            if dim.filter_type is None:
                return None
            return FilterFieldSpec(
                key=dim.key,
                type=dim.filter_type,
                label_key=dim.label_key,
                enum_values=dim.enum_values,
                options_source=dim.options_source,
            )
        field = self.filter_field(key)
        if field is not None:
            return FilterFieldSpec(key=field.key, type=field.filter_type, label_key=field.label_key)
        return None

    def filter_resolver(
        self, ctx: CompileContext
    ) -> Callable[[str], tuple[FilterFieldSpec, ColumnElement[Any]] | None]:
        """Resolver for :func:`~tiqora.reporting.filter_sql.compile_conditions`.

        Each key's expression is built once per resolver (and per condition
        reference), from the source's own column objects.
        """

        def resolve(key: str) -> tuple[FilterFieldSpec, ColumnElement[Any]] | None:
            spec = self.filter_spec(key)
            if spec is None:
                return None
            dim = self.dimension(key)
            if dim is not None:
                return spec, dim.expr(ctx)
            field = self.filter_field(key)
            assert field is not None
            return spec, field.expr(ctx)

        return resolve


REPORT_SOURCES: ExtensionPoint[ReportSource] = ExtensionPoint(
    key="core.reporting.sources",
    item_key=lambda s: s.key,
    description="Report sources (FROM clause, dimensions, measures) for the report engine.",
)
