"""Report engine: run a compiled report and turn the rows into a :class:`ReportResultOut`.

Order of work for one run: viewer scope (``ticket_query_scope``; archived
tickets only for admins who ask, like ``GET /tickets``) -> state row
(``last_snapshot_date``, facts status) -> compile -> cache lookup -> main
query (+ raw percentile rows) -> top-n folding -> rates and percentiles ->
zero buckets of a time series (only when the grid fits the row cap) -> sort
-> limit -> row cap -> totals and the previous period (own queries, never
derived from capped rows; row/column totals only for the returned rows) ->
labels -> cache store (only when the report data is ready).

Time limits (R-INLINEBUDGET): every statement runs inside
``statement_timeout`` plus a client-side ``asyncio.wait_for`` backstop. A
:class:`QueryBudget` decides the seconds per statement: in a background job
each statement gets ``query_timeout_seconds``; inline (an HTTP request behind
nginx's 90 s) all statements of the request share one deadline of at most
:data:`INLINE_BUDGET_SECONDS` — each statement gets the remaining budget
(never more than ``query_timeout_seconds``), and a used-up budget raises
:class:`~tiqora.reporting.sql.ReportQueryTimeout` before the next statement.
A client-side timeout or cancellation invalidates the connection
unconditionally (a cancelled aiomysql/asyncpg call leaves it mid-protocol).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import Row, Select, func
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.filters.tickets import TicketQueryScope, ticket_query_scope
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.reporting.cache import ReportCache
from tiqora.reporting.compiler import CompiledReport, MeasurePlan, RunParams, compile_report
from tiqora.reporting.definition import (
    ReportDefinition,
    TimeWindow,
    definition_hash,
    shift_unit,
    unit_start,
)
from tiqora.reporting.labels import resolve_labels
from tiqora.reporting.schemas import (
    Cell,
    ColumnType,
    FactsStatusOut,
    ReportResultOut,
    ReportTotalsOut,
    ResultColumnOut,
    TimeWindowOut,
)
from tiqora.reporting.semantic import Dimension, ReportSource
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sql import ReportQueryTimeout, invalidate_quietly, statement_timeout
from tiqora.reporting.state import facts_status_of, load_state

OTHER_KEY: Final = "__other__"
PERCENTILE_SAMPLE_CAP: Final = 200_000
#: hard bound on the groups one query may load (inline AND job mode): beyond it the
#: result is cut (arbitrary groups, ``truncated``) before top-n, sort and the row cap
MAX_GROUPS: int = 200_000
#: shared deadline of all queries of one inline request (nginx cuts /api/ at 90 s)
INLINE_BUDGET_SECONDS: Final = 75
#: client-side backstop on top of the server-side statement timeout
CLIENT_GRACE_SECONDS: float = 5.0

_PERCENTILE_Q: Final = {"p50": 0.5, "p90": 0.9}
#: measures whose values add up when groups are folded into "__other__"
_ADDITIVE: Final = frozenset({"count", "count_if", "sum"})
#: measures that are 0 (not NULL) for an empty group
_ZERO_WHEN_EMPTY: Final = frozenset({"count", "count_if", "count_distinct", "sum"})
_COUNT_AGGS: Final = frozenset({"count", "count_if", "count_distinct"})
_DATE_KINDS: Final = frozenset({"date", "local_date"})
_DIM_TYPES: Final[dict[str, ColumnType]] = {
    "id": "id",
    "string": "string",
    "enum": "enum",
    "date": "date",
    "local_date": "date",
    "hour": "hour",
    "weekday": "weekday",
}

Key = tuple[Any, ...]


# --------------------------------------------------------------------------
# time budget and statement execution
# --------------------------------------------------------------------------


@dataclass
class QueryBudget:
    """Seconds per statement; see the module docstring."""

    per_query_seconds: int = 60
    deadline: float | None = None  # clock() value; None = no shared deadline (jobs)
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def inline(
        cls,
        reporting: ReportingSettings,
        seconds: float = INLINE_BUDGET_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> QueryBudget:
        return cls(reporting.query_timeout_seconds, clock() + seconds, clock)

    @classmethod
    def job(cls, reporting: ReportingSettings) -> QueryBudget:
        return cls(reporting.query_timeout_seconds)

    def statement_seconds(self) -> int:
        """Timeout for the next statement; raises ``ReportQueryTimeout`` when used up."""
        if self.deadline is None:
            return self.per_query_seconds
        remaining = self.deadline - self.clock()
        if remaining < 1:
            raise ReportQueryTimeout("the report's time budget is used up")
        return max(1, min(self.per_query_seconds, int(remaining)))


async def _fetch(session: AsyncSession, stmt: Select[Any], budget: QueryBudget) -> list[Row[Any]]:
    seconds = budget.statement_seconds()
    try:
        async with statement_timeout(session, seconds):
            try:
                result = await asyncio.wait_for(
                    session.execute(stmt), seconds + CLIENT_GRACE_SECONDS
                )
            except (TimeoutError, asyncio.CancelledError):
                # client side: the driver call was cancelled mid-protocol; the
                # connection must never go back to the pool (before the reset runs)
                await invalidate_quietly(session)
                raise
            return list(result.all())
    except TimeoutError as exc:
        raise ReportQueryTimeout(f"statement exceeded {seconds}s (client side)") from exc


async def _bounded[R](
    budget: QueryBudget, work: Callable[[], Awaitable[R]], session: AsyncSession
) -> R:
    """Run non-report lookups (labels) within the budget (per-query limit in jobs).

    A timeout or cancellation invalidates the connection like :func:`_fetch`.
    """
    seconds = budget.statement_seconds()
    try:
        return await asyncio.wait_for(work(), seconds)
    except (TimeoutError, asyncio.CancelledError) as exc:
        await invalidate_quietly(session)
        if isinstance(exc, TimeoutError):
            raise ReportQueryTimeout(f"label lookup exceeded {seconds}s") from exc
        raise


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunPlan:
    compiled: CompiledReport
    cache_key: str
    definition: ReportDefinition = field(repr=False)  # as given (totals recompile from it)
    params: RunParams = field(repr=False)  # incl. last_snapshot_date of the state row
    scope: TicketQueryScope = field(repr=False)
    facts: FactsStatusOut = field(repr=False)
    row_cap: int = 0
    compare_previous: bool = False
    now: datetime = field(default_factory=utc_now)
    near_miss_percent: int = 10
    registry: Registry | None = field(default=None, repr=False)


async def plan_report(
    session: AsyncSession,
    *,
    defn: ReportDefinition,
    params: RunParams,
    user_id: int,
    reporting: ReportingSettings,
    row_cap: int | None = None,
    compare_previous: bool = False,
    now: datetime | None = None,
    registry: Registry | None = None,
    include_archived: bool = False,
) -> RunPlan:
    """Scope, state, compile and cache key — everything before the first report query.

    ``include_archived`` takes effect for admins only (same rule as ``GET /tickets``).
    """
    stamp = now if now is not None else utc_now()
    reg = registry if registry is not None else get_registry()
    grant = include_archived and await PermissionEngine(session).is_admin(user_id)
    scope = await ticket_query_scope(session, user_id, include_archived=grant)
    state = await load_state(session)
    effective_params = replace(params, last_snapshot_date=state.last_snapshot_date)
    compiled = compile_report(
        defn,
        effective_params,
        scope,
        now=stamp,
        near_miss_percent=reporting.near_miss_percent,
        registry=reg,
    )
    cap = row_cap if row_cap is not None else reporting.max_result_rows
    key = ReportCache.key_for(
        definition_hash=definition_hash(compiled.definition),
        queue_ids=scope.queue_ids,
        window=compiled.window,
        time_zone=effective_params.time_zone,
        row_cap=cap,
        compare_previous=compare_previous,
        include_archived=compiled.include_archived,
        extra_filter=effective_params.extra_filter,
        last_snapshot_date=effective_params.last_snapshot_date,
        near_miss_percent=reporting.near_miss_percent,
    )
    return RunPlan(
        compiled=compiled,
        cache_key=key,
        definition=defn,
        params=effective_params,
        scope=scope,
        facts=facts_status_of(state),
        row_cap=cap,
        compare_previous=compare_previous,
        now=stamp,
        near_miss_percent=reporting.near_miss_percent,
        registry=reg,
    )


def _recompile(plan: RunPlan, group_by: Sequence[int], window: TimeWindow | None) -> CompiledReport:
    params = plan.params if window is None else replace(plan.params, window_override=window)
    assert plan.registry is not None
    return compile_report(
        plan.definition,
        params,
        plan.scope,
        now=plan.now,
        near_miss_percent=plan.near_miss_percent,
        registry=plan.registry,
        group_by=group_by,
    )


def resolve_budget(budget: QueryBudget | None, reporting: ReportingSettings) -> QueryBudget:
    """The one meaning of ``budget=None`` in every engine entry point: job mode.

    Inline (HTTP) callers MUST create one :meth:`QueryBudget.inline` per request
    and pass that same object to ``estimate_rows`` and ``run_report``.
    """
    return budget if budget is not None else QueryBudget.job(reporting)


async def estimate_rows(
    session: AsyncSession,
    compiled: CompiledReport,
    *,
    reporting: ReportingSettings,
    budget: QueryBudget | None = None,
) -> int:
    """``SELECT COUNT(*)`` over the same FROM/WHERE as ``compiled.stmt`` (scope included).

    ``budget`` as in :func:`run_report` (see :func:`resolve_budget`).
    """
    if compiled.stmt is None:
        return 0
    stmt = compiled.stmt.with_only_columns(func.count(), maintain_column_froms=True).group_by(None)
    rows = await _fetch(session, stmt, resolve_budget(budget, reporting))
    return int(rows[0][0]) if rows else 0


# --------------------------------------------------------------------------
# collecting groups
# --------------------------------------------------------------------------


@dataclass
class _Group:
    n: int
    #: per measure: scalar | (num, den) for rates | list[float] for percentiles
    values: list[Any]
    folded: bool = False


def _num(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)):
        return value
    return float(value)


def _empty_group(plans: Sequence[MeasurePlan]) -> _Group:
    values: list[Any] = []
    for plan in plans:
        if plan.agg == "rate":
            values.append((0, 0))
        elif plan.agg in _PERCENTILE_Q:
            values.append([])
        else:
            values.append(0 if plan.agg in _ZERO_WHEN_EMPTY else None)
    return _Group(n=0, values=values)


async def _collect(
    session: AsyncSession, compiled: CompiledReport, budget: QueryBudget
) -> tuple[dict[Key, _Group], bool]:
    """Groups of one compiled query (+ its percentile samples); bool = result was cut.

    At most :data:`MAX_GROUPS` groups are loaded (``LIMIT MAX_GROUPS + 1``): a wider
    result keeps an arbitrary subset and is reported as truncated, so one report can
    never load millions of groups into the worker.
    """
    assert compiled.stmt is not None
    cap = MAX_GROUPS
    rows = await _fetch(session, compiled.stmt.limit(cap + 1), budget)
    truncated = len(rows) > cap
    groups: dict[Key, _Group] = {}
    for row in rows[:cap]:
        m = row._mapping
        values: list[Any] = []
        for plan in compiled.measures:
            if plan.agg == "rate":
                values.append((_num(m[plan.columns[0]]) or 0, _num(m[plan.columns[1]]) or 0))
            elif plan.agg in _PERCENTILE_Q:
                values.append([])
            else:
                values.append(_num(m[plan.columns[0]]))
        groups[tuple(m[d] for d in compiled.dims)] = _Group(n=int(m["n"]), values=values)
    if compiled.raw_stmt is not None:
        raw = await _fetch(session, compiled.raw_stmt.limit(PERCENTILE_SAMPLE_CAP + 1), budget)
        if len(raw) > PERCENTILE_SAMPLE_CAP:
            truncated = True
            raw = raw[:PERCENTILE_SAMPLE_CAP]
        slots = [
            (i, f"v_{plan.key}")
            for i, plan in enumerate(compiled.measures)
            if plan.agg in _PERCENTILE_Q
        ]
        for row in raw:
            m = row._mapping
            group = groups.get(tuple(m[d] for d in compiled.dims))
            if group is None:  # a group cut off by MAX_GROUPS
                continue
            for i, label in slots:
                value = m[label]
                if value is not None:  # NULLs are skipped per column
                    group.values[i].append(float(value))
    return groups, truncated


def _merge(into: _Group, other: _Group, plans: Sequence[MeasurePlan]) -> None:
    into.n += other.n
    into.folded = True
    for i, plan in enumerate(plans):
        a, b = into.values[i], other.values[i]
        if plan.agg == "rate":
            into.values[i] = (a[0] + b[0], a[1] + b[1])
        elif plan.agg in _PERCENTILE_Q:
            into.values[i] = [*a, *b]
        elif plan.agg in _ADDITIVE:
            into.values[i] = None if a is None and b is None else (a or 0) + (b or 0)
        else:
            into.values[i] = None


def _rank_score(group: _Group, plans: Sequence[MeasurePlan]) -> float:
    """Ranking for top-n: the first measure when it adds up, else the row count."""
    if plans and plans[0].agg in _ADDITIVE | {"count_distinct"}:
        return float(group.values[0] or 0)
    return float(group.n)


def _top_values(
    groups: dict[Key, _Group], pos: int, top_n: int, plans: Sequence[MeasurePlan]
) -> set[Any]:
    scores: dict[Any, float] = {}
    for key, group in groups.items():
        scores[key[pos]] = scores.get(key[pos], 0.0) + _rank_score(group, plans)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return {value for value, _ in ranked[:top_n]}


def _fold(
    groups: dict[Key, _Group], pos: int, keep: set[Any], plans: Sequence[MeasurePlan]
) -> dict[Key, _Group]:
    """Groups whose value at ``pos`` is not in ``keep`` become one ``OTHER_KEY`` group."""
    out: dict[Key, _Group] = {}
    for key, group in groups.items():
        new_key = key if key[pos] in keep else (*key[:pos], OTHER_KEY, *key[pos + 1 :])
        if new_key in out:
            _merge(out[new_key], group, plans)
        elif new_key != key:
            # folded rows show only measures that add up (see _measure_cells)
            out[new_key] = _Group(n=group.n, values=list(group.values), folded=True)
        else:
            out[new_key] = group
    return out


# --------------------------------------------------------------------------
# cells, columns, buckets, sorting
# --------------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation like ``PERCENTILE_CONT``: ``p = q * (n - 1)``."""
    if not values:
        return None
    ordered = sorted(values)
    p = q * (len(ordered) - 1)
    lo, hi = math.floor(p), math.ceil(p)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (p - lo)


def _measure_cells(group: _Group, plans: Sequence[MeasurePlan]) -> list[Cell]:
    cells: list[Cell] = []
    for plan, value in zip(plans, group.values, strict=True):
        if plan.agg == "rate":
            num, den = value
            cells.append(None if not den else num / den)
        elif plan.agg in _PERCENTILE_Q:
            cells.append(percentile(value, _PERCENTILE_Q[plan.agg]))
        elif group.folded and plan.agg not in _ADDITIVE:
            cells.append(None)
        elif value is None:
            cells.append(0 if plan.agg in _ZERO_WHEN_EMPTY else None)
        elif plan.agg in _COUNT_AGGS or (plan.unit == "count" and plan.agg != "avg"):
            cells.append(int(value))
        else:
            cells.append(float(value))
    return cells


def _dim_cell(value: Any) -> Cell:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    return str(value)


def _dimensions(compiled: CompiledReport) -> list[Dimension]:
    out: list[Dimension] = []
    for ref in compiled.definition.dimensions:
        dim = compiled.source.dimension(ref.key)
        assert dim is not None  # validated
        out.append(dim)
    return out


def _columns(compiled: CompiledReport) -> list[ResultColumnOut]:
    src: ReportSource = compiled.source
    cols: list[ResultColumnOut] = []
    for ref, dim in zip(compiled.definition.dimensions, _dimensions(compiled), strict=True):
        cols.append(
            ResultColumnOut(
                key=ref.key,
                role="dimension",
                type=_DIM_TYPES[dim.kind],
                label_key=dim.label_key,
                grain=(ref.grain or "day") if dim.kind in _DATE_KINDS else None,
                i18n_values=dim.i18n_values,
            )
        )
    for plan in compiled.measures:
        measure = src.measure(plan.key)
        assert measure is not None
        col_type: ColumnType = "count" if plan.agg in _COUNT_AGGS else plan.unit
        if plan.agg == "rate":
            col_type = "percent"
        cols.append(
            ResultColumnOut(
                key=plan.key, role="measure", type=col_type, label_key=measure.label_key
            )
        )
    return cols


def _bucket_count(grain: str, start: date, end: date) -> int:
    """Number of buckets without building them (upper bound for day grain: the days)."""
    if grain == "day":
        return (end - start).days + 1
    return len(_buckets(grain, start, end))  # <= 3660 / 7 entries


def _buckets(grain: str, start: date, end: date) -> list[date]:
    out: list[date] = []
    bucket = unit_start(grain, start)
    while bucket <= end:
        out.append(bucket)
        bucket = shift_unit(grain, bucket, 1)
    return out


def _fill_time_series(
    groups: dict[Key, _Group],
    compiled: CompiledReport,
    dims: Sequence[Dimension],
    max_rows: int,
) -> bool:
    """Zero rows for the empty buckets of the first date dimension (in place).

    The grid (buckets x series values, after top-n folding) is only built when it
    fits into ``max_rows`` (the row cap); otherwise nothing is filled and True is
    returned (the result is then flagged ``truncated``). This bounds the work an
    inline definition with many series over a long window can cause.
    """
    window = compiled.window
    defn = compiled.definition
    idx = next((i for i, d in enumerate(dims) if d.kind in _DATE_KINDS), None)
    if window is None or idx is None or dims[idx].stock:
        return False
    series = defn.visualization.series_dimension
    if len(dims) > 1 and (series is None or series == idx):
        return False
    grain = defn.dimensions[idx].grain or "day"
    if _bucket_count(grain, window.start_date, window.end_date) > max_rows:
        return True
    buckets = _buckets(grain, window.start_date, window.end_date)
    others = {(*key[:idx], *key[idx + 1 :]) for key in groups} if len(dims) > 1 else {()}
    if len(others) * len(buckets) > max_rows:
        return True
    for rest in others:
        for bucket in buckets:
            key = (*rest[:idx], bucket, *rest[idx:])
            if key not in groups:
                groups[key] = _empty_group(compiled.measures)
    return False


@dataclass
class _Record:
    key: Key
    cells: list[Cell]


def _sort(
    records: list[_Record], compiled: CompiledReport, dims: Sequence[Dimension]
) -> list[_Record]:
    defn = compiled.definition
    dim_keys = [r.key for r in defn.dimensions]
    measure_keys = [p.key for p in compiled.measures]
    specs: list[tuple[str, int, bool]] = []  # ("d"|"m", index, descending)
    for spec in defn.sort:
        if spec.key in dim_keys:
            specs.append(("d", dim_keys.index(spec.key), spec.direction == "desc"))
        elif spec.key in measure_keys:
            specs.append(("m", measure_keys.index(spec.key), spec.direction == "desc"))
    if not specs:
        idx = next((i for i, d in enumerate(dims) if d.kind in _DATE_KINDS), None)
        if idx is not None:
            specs = [("d", idx, False)]
        elif measure_keys:
            specs = [("m", 0, True)]

    def value(rec: _Record, kind: str, idx: int) -> Any:
        return rec.key[idx] if kind == "d" else rec.cells[idx]

    for kind, idx, desc in reversed(specs):
        present = [r for r in records if value(r, kind, idx) not in (None, OTHER_KEY)]
        missing = [r for r in records if value(r, kind, idx) in (None, OTHER_KEY)]
        present.sort(key=partial(value, kind=kind, idx=idx), reverse=desc)
        records = present + missing
    # the folded "__other__" rows always come last
    return [r for r in records if OTHER_KEY not in r.key] + [
        r for r in records if OTHER_KEY in r.key
    ]


# --------------------------------------------------------------------------
# totals and the previous period
# --------------------------------------------------------------------------


def _midnight_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, datetime.min.time(), tz).astimezone(UTC).replace(tzinfo=None)


def _local_date(naive_utc: datetime, tz: ZoneInfo) -> date:
    return naive_utc.replace(tzinfo=UTC).astimezone(tz).date()


def previous_window(w: TimeWindow) -> TimeWindow:
    """The window right before ``w`` with the same length.

    Whole local days (the normal case) shift by their day count, so DST changes
    never cut a day in half; exact UTC bounds (``/stats`` overrides) shift by
    ``end - start`` with the local dates recomputed.
    """
    tz = ZoneInfo(w.time_zone)
    days = (w.end_date - w.start_date).days + 1
    aligned = w.start_utc == _midnight_utc(w.start_date, tz) and w.end_utc == _midnight_utc(
        w.end_date + timedelta(days=1), tz
    )
    if aligned:
        start_date = w.start_date - timedelta(days=days)
        return TimeWindow(
            start_utc=_midnight_utc(start_date, tz),
            end_utc=w.start_utc,
            start_date=start_date,
            end_date=w.start_date - timedelta(days=1),
            time_zone=w.time_zone,
        )
    start = w.start_utc - (w.end_utc - w.start_utc)
    return TimeWindow(
        start_utc=start,
        end_utc=w.start_utc,
        start_date=_local_date(start, tz),
        end_date=_local_date(w.start_utc - timedelta(microseconds=1), tz),
        time_zone=w.time_zone,
    )


async def _totals(
    session: AsyncSession,
    plan: RunPlan,
    budget: QueryBudget,
    *,
    grand: bool,
    rows: bool,
    columns: bool,
    top: dict[int, set[Any]],
    shown: dict[int, set[Any]],
    window: TimeWindow | None = None,
) -> tuple[ReportTotalsOut, bool]:
    """Totals from their own queries (``group_by`` [] / [0] / [1]); bool = samples capped.

    Row/column totals are kept only for the dimension values of the returned rows
    (``shown``), so they never outgrow the row cap and need no extra labels.
    """
    width = len(plan.compiled.definition.dimensions)
    out = ReportTotalsOut()
    truncated = False
    if grand:
        compiled = _recompile(plan, [], window)
        groups, capped = await _collect(session, compiled, budget)
        truncated |= capped
        group = groups.get(()) or _empty_group(compiled.measures)
        out.grand = [None] * width + _measure_cells(group, compiled.measures)
    for wanted, pos, target in ((rows, 0, out.rows), (columns, 1, out.columns)):
        if not wanted or width < 2:
            continue
        compiled = _recompile(plan, [pos], window)
        groups, capped = await _collect(session, compiled, budget)
        truncated |= capped
        if pos in top:
            groups = _fold(groups, 0, top[pos], compiled.measures)
        for (value,), group in groups.items():
            if value not in shown[pos]:
                continue
            dims: list[Cell] = [None] * width
            dims[pos] = _dim_cell(value)
            target[str(_dim_cell(value))] = dims + _measure_cells(group, compiled.measures)
    return out, truncated


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def _window_out(w: TimeWindow | None) -> TimeWindowOut | None:
    if w is None:
        return None
    return TimeWindowOut(
        start=w.start_utc,
        end=w.end_utc,
        start_date=w.start_date,
        end_date=w.end_date,
        time_zone=w.time_zone,
    )


async def execute_plan(
    session: AsyncSession, plan: RunPlan, *, report_key: str | None, budget: QueryBudget
) -> ReportResultOut:
    """Run the queries of ``plan`` and post-process them (no cache involved)."""
    compiled = plan.compiled
    defn = compiled.definition
    dims = _dimensions(compiled)
    columns = _columns(compiled)
    generated_at = utc_now()

    def result(
        rows: list[list[Cell]],
        labels: dict[str, dict[str, str]],
        totals: ReportTotalsOut | None,
        previous: ReportTotalsOut | None,
        row_count: int,
        truncated: bool,
    ) -> ReportResultOut:
        return ReportResultOut(
            report_key=report_key,
            columns=columns,
            rows=rows,
            labels=labels,
            totals=totals,
            previous=previous,
            row_count=row_count,
            truncated=truncated,
            time_window=_window_out(compiled.window),
            generated_at=generated_at,
            cached=False,
            facts=plan.facts,
        )

    if compiled.stmt is None:  # empty scope: no query at all
        return result([], {}, None, None, 0, False)

    groups, truncated = await _collect(session, compiled, budget)
    top: dict[int, set[Any]] = {}
    for i, ref in enumerate(defn.dimensions):
        if ref.top_n is not None:
            top[i] = _top_values(groups, i, ref.top_n, compiled.measures)
            groups = _fold(groups, i, top[i], compiled.measures)
    if _fill_time_series(groups, compiled, dims, plan.row_cap):
        truncated = True

    records = [_Record(key, _measure_cells(g, compiled.measures)) for key, g in groups.items()]
    records = _sort(records, compiled, dims)
    if defn.limit is not None:
        records = records[: defn.limit]
    row_count = len(records)
    if row_count > plan.row_cap:
        records = records[: plan.row_cap]
        truncated = True

    shown = {i: {rec.key[i] for rec in records} for i in range(len(dims))}
    want = defn.totals
    totals: ReportTotalsOut | None = None
    if want.grand or want.rows or want.columns or plan.compare_previous:
        totals, capped = await _totals(
            session,
            plan,
            budget,
            grand=want.grand or plan.compare_previous,
            rows=want.rows,
            columns=want.columns,
            top=top,
            shown=shown,
        )
        truncated |= capped
    previous: ReportTotalsOut | None = None
    if plan.compare_previous and compiled.window is not None:
        previous, capped = await _totals(
            session,
            plan,
            budget,
            grand=True,
            rows=want.rows,
            columns=want.columns,
            top=top,
            shown=shown,
            window=previous_window(compiled.window),
        )
        truncated |= capped

    labels: dict[str, dict[str, str]] = {}
    for i, (ref, dim) in enumerate(zip(defn.dimensions, dims, strict=True)):
        if dim.labels == "none":
            continue
        # totals rows/columns only carry values of these rows (see _totals)
        keys = [v for v in shown[i] if v is not None and v != OTHER_KEY]
        source = dim.labels
        labels[ref.key] = await _bounded(
            budget, partial(resolve_labels, session, source, keys), session
        )

    rows = [[_dim_cell(v) for v in rec.key] + rec.cells for rec in records]
    return result(rows, labels, totals, previous, row_count, truncated)


async def run_report(
    session: AsyncSession,
    *,
    defn: ReportDefinition,
    params: RunParams,
    user_id: int,
    report_key: str | None,
    reporting: ReportingSettings,
    cache: ReportCache | None = None,
    now: datetime | None = None,
    row_cap: int | None = None,
    compare_previous: bool = False,
    registry: Registry | None = None,
    use_cache: bool = True,
    include_archived: bool = False,
    budget: QueryBudget | None = None,
) -> ReportResultOut:
    """Run one report for ``user_id``; see the module docstring.

    ``budget`` ``None`` = job mode (``query_timeout_seconds`` per statement), the
    same meaning as in :func:`estimate_rows` (see :func:`resolve_budget`); inline
    callers pass one :meth:`QueryBudget.inline` for the whole request.
    """
    plan = await plan_report(
        session,
        defn=defn,
        params=params,
        user_id=user_id,
        reporting=reporting,
        row_cap=row_cap,
        compare_previous=compare_previous,
        now=now,
        registry=registry,
        include_archived=include_archived,
    )
    if use_cache and cache is not None:
        hit = await cache.get(plan.cache_key)
        if hit is not None:
            return hit.model_copy(update={"report_key": report_key, "facts": plan.facts})
    return await execute_and_store(
        session,
        plan,
        report_key=report_key,
        reporting=reporting,
        cache=cache if use_cache else None,
        budget=budget,
    )


async def execute_and_store(
    session: AsyncSession,
    plan: RunPlan,
    *,
    report_key: str | None,
    reporting: ReportingSettings,
    cache: ReportCache | None,
    budget: QueryBudget | None = None,
) -> ReportResultOut:
    """Second half of :func:`run_report` for callers that planned (and looked up) already.

    Runs ``plan`` and stores the result under ``plan.cache_key`` (when ``cache`` is
    given and the data is ready). No cache lookup: the caller did it. ``budget`` as in
    :func:`run_report`.
    """
    result = await execute_plan(
        session,
        plan,
        report_key=report_key,
        budget=resolve_budget(budget, reporting),
    )
    # never cache numbers computed while the report data is still being built: a
    # later hit would show them with the then-current "ready" status
    data_ready = plan.facts.ready or not plan.compiled.source.requires_facts
    if cache is not None and data_ready:
        await cache.put(plan.cache_key, result)
    return result


async def raw_measure_values(
    session: AsyncSession,
    *,
    defn: ReportDefinition,
    measure_key: str,
    params: RunParams,
    user_id: int,
    reporting: ReportingSettings,
    now: datetime | None = None,
    registry: Registry | None = None,
    include_archived: bool = False,
    budget: QueryBudget | None = None,
    on_capped: Callable[[], None] | None = None,
) -> list[float]:
    """The sample values of one percentile measure over the whole report (≤ the sample cap).

    ``on_capped`` is called when more samples existed than the cap (the list is cut).
    """
    plan = await plan_report(
        session,
        defn=defn,
        params=params,
        user_id=user_id,
        reporting=reporting,
        now=now,
        registry=registry,
        include_archived=include_archived,
    )
    if not any(p.key == measure_key and p.agg in _PERCENTILE_Q for p in plan.compiled.measures):
        raise ValueError(f"{measure_key!r} is not a percentile measure of this report")
    compiled = _recompile(plan, [], None)
    if compiled.raw_stmt is None:
        return []
    label = f"v_{measure_key}"
    rows = await _fetch(
        session,
        compiled.raw_stmt.limit(PERCENTILE_SAMPLE_CAP + 1),
        resolve_budget(budget, reporting),
    )
    if len(rows) > PERCENTILE_SAMPLE_CAP:
        rows = rows[:PERCENTILE_SAMPLE_CAP]
        if on_capped is not None:
            on_capped()
    return [float(r._mapping[label]) for r in rows if r._mapping[label] is not None]
