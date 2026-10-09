"""Builders shared by the core report sources (label-key patterns, filter types)."""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import ColumnElement, Table

from tiqora.filters import FieldType
from tiqora.reporting.facts import CHANNEL_KEYS
from tiqora.reporting.semantic import (
    BoolFn,
    CompileContext,
    Dimension,
    DimensionKind,
    ExprFn,
    FilterOnlyField,
    LabelSource,
    Measure,
    MeasureAgg,
    MeasureUnit,
)

OPEN_TYPES: Final = ("new", "open", "pending reminder", "pending auto")
STATE_TYPES: Final = (*OPEN_TYPES, "closed", "removed", "merged")
CHANNELS: Final = tuple(dict.fromkeys(CHANNEL_KEYS.values()))

#: Ticket attributes that have a wave-0 ``POST /reference/field-options`` source.
OPTION_SOURCES: Final = frozenset({"queue", "state", "priority", "type", "service", "sla"})

_FILTER_TYPE_BY_KIND: Final[dict[str, FieldType]] = {
    "id": "id",
    "string": "string",
    "enum": "enum",
    "date": "datetime",
}


def col(table: Any, name: str) -> ExprFn:
    """Expression function for one column of an aliased table."""
    column = table.c[name]

    def expr(ctx: CompileContext) -> ColumnElement[Any]:
        return column  # type: ignore[no-any-return]

    return expr


def dim(
    key: str,
    kind: DimensionKind,
    expr: ExprFn,
    *,
    labels: LabelSource = "none",
    options: str | None = None,
    values: str | None = None,
    enum: tuple[str, ...] = (),
    filterable: bool = True,
    stock: bool = False,
) -> Dimension:
    """Dimension with the ``reports.dimensions.<key>`` label and its filter type.

    id / string / enum / date dimensions are filterable (``datetime`` for dates);
    hour / weekday / local_date are not (``filterable=False`` opts out too).
    """
    if kind == "enum" and not enum:
        raise ValueError(f"enum dimension {key} needs enum values")
    return Dimension(
        key=key,
        label_key=f"reports.dimensions.{key}",
        kind=kind,
        expr=expr,
        labels=labels,
        i18n_values=values,
        filter_type=_FILTER_TYPE_BY_KIND.get(kind) if filterable else None,
        options_source=options if filterable else None,
        stock=stock,
        enum_values=enum,
    )


def attribute_dim(key: str, expr: ExprFn, source: LabelSource) -> Dimension:
    """id dimension of a ticket attribute (queue/state/priority/type/service/sla)."""
    return dim(key, "id", expr, labels=source, options=source)


def flag(key: str, expr: ExprFn) -> FilterOnlyField:
    return FilterOnlyField(
        key=key, label_key=f"reports.dimensions.{key}", filter_type="bool", expr=expr
    )


def measure(
    key: str,
    agg: MeasureAgg,
    *,
    expr: ExprFn | None = None,
    condition: BoolFn | None = None,
    denominator: BoolFn | None = None,
    unit: MeasureUnit = "count",
    stock: bool = False,
) -> Measure:
    return Measure(
        key=key,
        label_key=f"reports.measures.{key}",
        agg=agg,
        expr=expr,
        condition=condition,
        denominator=denominator,
        unit=unit,
        stock=stock,
    )


def aliased(table: Any, name: str) -> Table:
    alias: Table = table.__table__.alias(name)
    return alias
