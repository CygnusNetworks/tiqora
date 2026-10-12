"""Report compiler: effective definition + permission scope -> SELECT statements.

Permission rule: the viewer's queue scope (``source.scope_column IN queue_ids``)
is the FIRST clause of a top-level ``AND``; every user filter, ``extra_filter``
and runtime parameter is ANDed next to it, so no ``any`` / ``not`` can widen
it. Archived tickets are dropped (``source.archive_exclusion``) unless the
scope includes them — the caller grants that to admins only, like
``GET /tickets``.

Queue-valued dimensions that are not the scope column (``queue_at_create``,
event ``queue`` / ``from_queue`` / ``to_queue``) can name queues the viewer
cannot see; their values outside the scope become :data:`HIDDEN_QUEUE_ID`
(rows stay counted, the engine labels the bucket "Andere Queue"). Filters on
those fields compare the same masked value.

Each dimension expression (incl. the ``to_local`` CASE with bound ``until``
values) is built once; GROUP BY refers to the ``d<i>`` select labels.
Sorting, limits, rates and percentiles are the engine's job.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, get_args

from sqlalchemy import (
    ColumnElement,
    Integer,
    Select,
    and_,
    case,
    distinct,
    func,
    literal,
    literal_column,
    null,
)
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select

from tiqora.domain.timezones import valid_time_zone
from tiqora.extensions.registry import Registry
from tiqora.filters import Condition, Filter, FilterError, FilterFieldSpec, validate_filter
from tiqora.filters.catalog import MAX_IN_VALUES
from tiqora.filters.tickets import TicketQueryScope
from tiqora.reporting.definition import (
    DefinitionError,
    DimensionRef,
    RelativeTimeRange,
    ReportDefinition,
    ReportParameter,
    TimeRange,
    TimeWindow,
    resolve_time_window,
    shift_unit,
    unit_start,
    validate_definition,
)
from tiqora.reporting.filter_sql import compile_conditions
from tiqora.reporting.semantic import (
    CompileContext,
    Dimension,
    MeasureAgg,
    MeasureUnit,
    ReportSource,
)
from tiqora.reporting.sql import Grain, date_bucket, date_part, to_local, tz_segments

#: Dimension value for a queue outside the viewer's scope (never a real queue id).
HIDDEN_QUEUE_ID: Final = -1
#: Window when the definition sets none but the source has a default time field.
DEFAULT_TIME_RANGE: Final = RelativeTimeRange(unit="day", count=30, include_current=True)
#: tz segments for reports without a window cover ``now - 400 days .. now`` (+ margin).
_NO_WINDOW_SPAN: Final = timedelta(days=400)
_DATE_KINDS: Final = frozenset({"date", "local_date"})
_PERCENTILES: Final = frozenset({"p50", "p90"})
_BOOL_OPS: Final = frozenset({"is_true", "is_false"})

Resolver = Callable[[str], tuple[FilterFieldSpec, ColumnElement[Any]] | None]


@dataclass(frozen=True, slots=True)
class RunParams:
    time_range: TimeRange | None = None
    grain: Grain | None = None  # replaces the grain of the first date dimension
    time_zone: str = "UTC"  # resolved by the caller (request > definition > viewer)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    dimension_choices: Mapping[int, str] = field(default_factory=dict)
    window_override: TimeWindow | None = None  # internal: /stats wrappers pass exact UTC bounds
    extra_filter: Filter | None = None  # internal: /stats wrappers' extra filters
    last_snapshot_date: date | None = None  # internal: tiqora_report_state.last_snapshot_date


@dataclass(frozen=True, slots=True)
class MeasurePlan:
    key: str
    agg: MeasureAgg
    unit: MeasureUnit
    columns: tuple[str, ...]  # ("m0",) or ("m0_num", "m0_den"); () for percentiles


@dataclass(frozen=True, slots=True)
class CompiledReport:
    definition: ReportDefinition  # effective (choices/grain/params applied)
    source: ReportSource
    stmt: Select[Any] | None  # GROUP BY dims; None = empty scope -> empty result, no query
    raw_stmt: Select[Any] | None  # dims + percentile values ("v_<key>"); only when p50/p90 used
    #: select labels "d<i>" of the kept dimensions; ``i`` is the index in
    #: ``definition.dimensions`` (``group_by=[1]`` -> ``("d1",)``)
    dims: tuple[str, ...]
    measures: tuple[MeasurePlan, ...]
    window: TimeWindow | None
    stock_dates: tuple[date, ...] | None  # daily_snapshot period-end dates used
    #: archived tickets are part of the result (scope.include_archived); cache keys must
    #: include it because the definition hash does not
    include_archived: bool = False


class ReportCompileError(DefinitionError):
    """Runtime parameter / choice / grain error (422); not SQLAlchemy's ``CompileError``."""

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def compile_report(
    defn: ReportDefinition,
    params: RunParams,
    scope: TicketQueryScope,
    *,
    now: datetime,
    near_miss_percent: int,
    registry: Registry,
    group_by: Sequence[int] | None = None,
) -> CompiledReport:
    """Compile ``defn`` for one run inside ``scope``.

    ``group_by``: dimension indices to keep (totals queries use ``[]`` /
    ``[0]`` / ``[1]``). Raises :class:`DefinitionError` for an invalid
    definition and :class:`ReportCompileError` for invalid run parameters.
    Every select has a ``n`` column (``COUNT(*)``) next to the measures.
    """
    src = validate_definition(defn, registry)
    zone = valid_time_zone(params.time_zone)
    if zone is None:
        raise ReportCompileError(f"Unbekannte Zeitzone „{params.time_zone}“.", path="time_zone")
    effective = _apply_choices_and_grain(defn, src, params)
    effective, param_conds = _apply_parameters(effective, src, params)
    if params.time_range is not None:
        effective = effective.model_copy(update={"time_range": params.time_range})
    try:
        src = validate_definition(effective, registry)
    except DefinitionError as exc:
        raise ReportCompileError(exc.message, path=exc.path) from exc

    keep = _kept_dimensions(effective, group_by)
    time_key, window = _resolve_window(effective, src, params, zone, now)
    tz = (
        tz_segments(zone, window.start_utc, window.end_utc)
        if window is not None
        else tz_segments(zone, now - _NO_WINDOW_SPAN, now)
    )
    ctx = CompileContext(
        now=now, time_zone=zone, tz=tz, near_miss_percent=near_miss_percent, window=window
    )
    scope_col = src.scope_column(ctx)
    scope_ids = sorted(scope.queue_ids)

    def hide(expr: ColumnElement[Any]) -> ColumnElement[Any]:
        return case(
            (expr.in_(scope_ids), expr),
            (expr.is_not(None), literal(HIDDEN_QUEUE_ID, Integer())),
            else_=null(),
        )

    def dim_expr(dim: Dimension) -> ColumnElement[Any]:
        expr = dim.expr(ctx)
        if dim.labels == "queue" and expr is not scope_col:
            return hide(expr)
        return expr

    resolve = _masked_resolver(src, ctx, dim_expr)

    # dimensions: one expression object each, grouped by label
    dim_cols: list[ColumnElement[Any]] = []
    labels: list[str] = []
    for i in keep:
        ref = effective.dimensions[i]
        dim = src.dimension(ref.key)
        assert dim is not None  # validated
        label = f"d{i}"
        dim_cols.append(_bucketed(dim, ref, dim_expr(dim), ctx).label(label))
        labels.append(label)

    # where: scope first, everything else ANDed next to it
    where: list[ColumnElement[bool]] = [scope_col.in_(scope_ids), *src.base_where(ctx)]
    if src.archive_exclusion is not None and not scope.include_archived:
        where.append(src.archive_exclusion(ctx))
    if window is not None and time_key is not None:
        where.extend(_window_conditions(src, time_key, window, ctx))
    stock_dates = _stock_dates(effective, src, keep, window, params.last_snapshot_date)
    if stock_dates is not None:
        stock_dim = next(d for d in src.dimensions if d.stock)
        where.append(stock_dim.expr(ctx).in_(stock_dates))
    if effective.filter is not None:
        where.extend(_filter(effective.filter, resolve, now, "filter"))
    if params.extra_filter is not None:
        where.extend(_filter(params.extra_filter, resolve, now, "extra_filter"))
    for key, cond in param_conds:
        where.extend(_filter(Filter(where=cond), resolve, now, f"parameters.{key}", fixed=True))

    measure_cols, plans, raw_values = _measures(effective, src, ctx)

    def result(stmt: Select[Any] | None, raw_stmt: Select[Any] | None) -> CompiledReport:
        return CompiledReport(
            definition=effective,
            source=src,
            stmt=stmt,
            raw_stmt=raw_stmt,
            dims=tuple(labels),
            measures=tuple(plans),
            window=window,
            stock_dates=stock_dates,
            include_archived=scope.include_archived and src.archive_exclusion is not None,
        )

    if not scope_ids:
        return result(None, None)

    from_clause = src.from_clause(ctx)
    where_clause = and_(*where)
    stmt = (
        sa_select(*dim_cols, func.count().label("n"), *measure_cols)
        .select_from(from_clause)
        .where(where_clause)
    )
    if labels:
        # by label: one GROUP BY term per select column, no re-rendered binds (R-GROUPBY)
        stmt = stmt.group_by(*(literal_column(label) for label in labels))
    raw_stmt: Select[Any] | None = None
    if raw_values:
        raw_stmt = (
            sa_select(*dim_cols, *raw_values)
            .select_from(from_clause)
            .where(where_clause, sa_or(*(v.is_not(None) for v in _unlabel(raw_values))))
        )
    return result(stmt, raw_stmt)


# --------------------------------------------------------------------------
# effective definition
# --------------------------------------------------------------------------


def _apply_choices_and_grain(
    defn: ReportDefinition, src: ReportSource, params: RunParams
) -> ReportDefinition:
    """Swap dimensions for allowed runtime choices; set the first date dimension's grain."""
    refs = list(defn.dimensions)
    for idx, choice in params.dimension_choices.items():
        path = f"dimension_choices.{idx}"
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(refs):
            raise ReportCompileError("Diese Dimension gibt es nicht.", path=path)
        ref = refs[idx]
        if choice != ref.key and choice not in ref.choices:
            raise ReportCompileError(f"„{choice}“ ist hier nicht wählbar.", path=path)
        new_dim = src.dimension(choice)
        grain = ref.grain if new_dim is not None and new_dim.kind in _DATE_KINDS else None
        refs[idx] = ref.model_copy(update={"key": choice, "grain": grain})
    if params.grain is not None:
        if params.grain not in get_args(Grain):
            raise ReportCompileError(f"Unbekannte Auflösung „{params.grain}“.", path="grain")
        for i, ref in enumerate(refs):
            dim = src.dimension(ref.key)
            if dim is not None and dim.kind in _DATE_KINDS:
                refs[i] = ref.model_copy(update={"grain": params.grain})
                break
    return defn.model_copy(update={"dimensions": refs})


def _missing(value: Any) -> bool:
    return value is None or value is False or (isinstance(value, (list, tuple)) and not value)


def _apply_parameters(
    defn: ReportDefinition, src: ReportSource, params: RunParams
) -> tuple[ReportDefinition, list[tuple[str, Condition]]]:
    """Validate runtime values; return the effective definition and one condition per parameter.

    The effective definition records the applied values as each parameter's
    ``default`` (``[]`` = a boolean parameter that is on, ``None`` = skipped) and
    clears ``required``, so it re-compiles to the same query and its hash
    differs per value. ``None``, ``False`` and an empty list count as missing.
    """
    known = {p.key for p in defn.parameters}
    for key in params.parameters:
        if key not in known:
            raise ReportCompileError(f"Unbekannter Parameter „{key}“.", path=f"parameters.{key}")
    conds: list[tuple[str, Condition]] = []
    effective: list[ReportParameter] = []
    for p in defn.parameters:
        path = f"parameters.{p.key}"
        raw = params.parameters.get(p.key)
        applied: list[Any] | None
        if _missing(raw):
            if p.required:
                raise ReportCompileError("Bitte einen Wert angeben.", path=path)
            applied = _from_default(p, path)
        else:
            applied = _from_value(p, raw, path)
        if applied is not None:
            value: Any = None
            if p.op == "in":
                value = applied
            elif p.op == "eq":
                value = applied[0]
            cond = Condition(field=p.field, op=p.op, value=value)
            try:  # same value validation as inline filters (types, enum values, limits)
                validate_filter(Filter(where=cond), src.filter_spec)
            except FilterError as exc:
                raise ReportCompileError(exc.message, path=path) from exc
            conds.append((p.key, cond))
        effective.append(p.model_copy(update={"default": applied, "required": False}))
    return defn.model_copy(update={"parameters": effective}), conds


def _from_default(p: ReportParameter, path: str) -> list[Any] | None:
    if p.default is None:
        return None
    if p.op in _BOOL_OPS:
        return []
    if not p.default:
        return None
    if p.op == "eq" and len(p.default) != 1:
        raise ReportCompileError("Der Vorgabewert muss genau ein Wert sein.", path=path)
    return list(p.default)


def _from_value(p: ReportParameter, raw: Any, path: str) -> list[Any]:
    if p.op in _BOOL_OPS:
        return []  # the value is ignored; presence switches the condition on
    if p.op == "in":
        if not isinstance(raw, (list, tuple)):
            raise ReportCompileError("Bitte die Werte als Liste angeben.", path=path)
        if len(raw) > MAX_IN_VALUES:
            raise ReportCompileError(f"Es sind höchstens {MAX_IN_VALUES} Werte erlaubt.", path=path)
        values = list(raw)
    else:
        if isinstance(raw, (list, tuple, dict)):
            raise ReportCompileError("Bitte genau einen Wert angeben.", path=path)
        values = [raw]
    if p.allowed_values is not None:
        allowed = p.allowed_values
        for v in values:
            if isinstance(v, bool) or v not in allowed:
                raise ReportCompileError(f"„{v}“ ist hier nicht erlaubt.", path=path)
    return values


def _kept_dimensions(defn: ReportDefinition, group_by: Sequence[int] | None) -> list[int]:
    if group_by is None:
        return list(range(len(defn.dimensions)))
    keep = sorted(set(group_by))
    for i in keep:
        if not 0 <= i < len(defn.dimensions):
            raise ValueError(f"group_by index {i} out of range")
    return keep


# --------------------------------------------------------------------------
# window, stock dates, dimensions, filters, measures
# --------------------------------------------------------------------------


def _resolve_window(
    defn: ReportDefinition, src: ReportSource, params: RunParams, zone: str, now: datetime
) -> tuple[str | None, TimeWindow | None]:
    """(time field key, window). No time field or no range on a source without default -> none.

    A range on a source without a default time field (``open_tickets``) applies
    to the definition's ``time_field``, else to the source's first time field.
    """
    has_range = params.window_override is not None or defn.time_range is not None
    key = defn.time_field or src.default_time_field
    if key is None and has_range and src.time_fields:
        key = src.time_fields[0]
    if key is None or not (has_range or src.default_time_field):
        return None, None
    if params.window_override is not None:
        return key, params.window_override
    return key, resolve_time_window(defn.time_range or DEFAULT_TIME_RANGE, zone, now)


def _window_conditions(
    src: ReportSource, key: str, window: TimeWindow, ctx: CompileContext
) -> list[ColumnElement[bool]]:
    dim = src.dimension(key)
    assert dim is not None  # validated time field
    expr = dim.expr(ctx)
    if dim.kind == "local_date":
        return [expr >= window.start_date, expr <= window.end_date]
    return [expr >= window.start_utc, expr < window.end_utc]


def _stock_dates(
    defn: ReportDefinition,
    src: ReportSource,
    keep: Sequence[int],
    window: TimeWindow | None,
    last_snapshot: date | None,
) -> tuple[date, ...] | None:
    """Period-end dates of stock measures; ``None`` = no restriction (daily grain or no stock)."""
    stock_dim = next((d for d in src.dimensions if d.stock), None)
    if stock_dim is None or window is None:
        return None
    if not any((m := src.measure(r.key)) is not None and m.stock for r in defn.measures):
        return None
    ref = next((defn.dimensions[i] for i in keep if defn.dimensions[i].key == stock_dim.key), None)
    grain = None if ref is None else (ref.grain or "day")
    if grain == "day":
        return None
    end = window.end_date if last_snapshot is None else min(window.end_date, last_snapshot)
    if grain is None:
        return (end,) if end >= window.start_date else ()
    dates: list[date] = []
    bucket = unit_start(grain, window.start_date)
    while bucket <= window.end_date:
        nxt = shift_unit(grain, bucket, 1)
        last = min(nxt - timedelta(days=1), end)
        if last >= max(bucket, window.start_date):
            dates.append(last)
        bucket = nxt
    return tuple(dates)


def _bucketed(
    dim: Dimension, ref: DimensionRef, expr: ColumnElement[Any], ctx: CompileContext
) -> ColumnElement[Any]:
    if dim.kind == "date":
        return date_bucket(to_local(expr, ctx.tz), ref.grain or "day")
    if dim.kind == "local_date":
        return date_bucket(expr, ref.grain or "day")
    if dim.kind == "hour":
        return date_part(to_local(expr, ctx.tz), "hour")
    if dim.kind == "weekday":
        return date_part(to_local(expr, ctx.tz), "isodow")
    return expr


def _masked_resolver(
    src: ReportSource, ctx: CompileContext, dim_expr: Callable[[Dimension], ColumnElement[Any]]
) -> Resolver:
    base = src.filter_resolver(ctx)

    def resolve(key: str) -> tuple[FilterFieldSpec, ColumnElement[Any]] | None:
        hit = base(key)
        dim = src.dimension(key)
        if hit is None or dim is None:
            return hit
        return hit[0], dim_expr(dim)

    return resolve


def _filter(
    flt: Filter, resolve: Resolver, now: datetime, path: str, *, fixed: bool = False
) -> list[ColumnElement[bool]]:
    try:
        cond = compile_conditions(flt, resolve, now=now)
    except FilterError as exc:
        where = path if fixed else f"{path}.{exc.path}"
        raise ReportCompileError(exc.message, path=where) from exc
    return [] if cond is None else [cond]


def _measures(
    defn: ReportDefinition, src: ReportSource, ctx: CompileContext
) -> tuple[list[ColumnElement[Any]], list[MeasurePlan], list[ColumnElement[Any]]]:
    cols: list[ColumnElement[Any]] = []
    plans: list[MeasurePlan] = []
    raw: list[ColumnElement[Any]] = []
    for i, ref in enumerate(defn.measures):
        m = src.measure(ref.key)
        assert m is not None  # validated
        label = f"m{i}"
        names: tuple[str, ...] = (label,)
        if m.agg == "count":
            cols.append(func.count().label(label))
        elif m.agg == "count_if":
            assert m.condition is not None
            cols.append(_count_when(m.condition(ctx)).label(label))
        elif m.agg == "rate":
            assert m.condition is not None and m.denominator is not None
            den = m.denominator(ctx)
            # the numerator is counted inside the denominator population (rate <= 100 %)
            cols.append(_count_when(and_(den, m.condition(ctx))).label(f"{label}_num"))
            cols.append(_count_when(den).label(f"{label}_den"))
            names = (f"{label}_num", f"{label}_den")
        elif m.agg in _PERCENTILES:
            assert m.expr is not None
            raw.append(m.expr(ctx).label(f"v_{m.key}"))
            names = ()
        else:
            assert m.expr is not None
            expr = m.expr(ctx)
            cols.append(_aggregate(m.agg, expr).label(label))
        plans.append(MeasurePlan(key=m.key, agg=m.agg, unit=m.unit, columns=names))
    return cols, plans, raw


def _aggregate(agg: MeasureAgg, expr: ColumnElement[Any]) -> ColumnElement[Any]:
    if agg == "count_distinct":
        return func.count(distinct(expr))
    if agg == "sum":
        return func.sum(expr)
    if agg == "avg":
        return func.avg(expr)
    if agg == "min":
        return func.min(expr)
    if agg == "max":
        return func.max(expr)
    raise ValueError(f"unknown aggregate {agg!r}")  # pragma: no cover - MeasureAgg is closed


def _count_when(cond: ColumnElement[bool]) -> ColumnElement[Any]:
    return func.sum(case((cond, 1), else_=0))


def _unlabel(cols: Sequence[ColumnElement[Any]]) -> list[ColumnElement[Any]]:
    return [getattr(c, "element", c) for c in cols]


def effective_definition(
    defn: ReportDefinition, src: ReportSource, params: RunParams
) -> tuple[ReportDefinition, list[tuple[str, Condition]]]:
    """The definition a run compiles (runtime choices, grain and parameters applied) and one
    condition per applied parameter: the public entry for the dashboard drill-down, which must
    rebuild exactly what :func:`compile_report` ran."""
    effective = _apply_choices_and_grain(defn, src, params)
    return _apply_parameters(effective, src, params)
