"""Ticket statistics and reporting aggregations (thin wrappers over the report engine).

Backs the ``/api/v1/stats/*`` REST endpoints. Since the reporting foundation every
method runs the built-in reports (``builtin.volume``, ``builtin.open_now``) or small
inline definitions through :func:`tiqora.reporting.engine.run_report`, so the
numbers of the old Reports page and the new reports page agree. Scoping to the
caller's ``ro`` queues is done by the report compiler, as for every report.

Public methods, dataclasses and response shapes are unchanged; the semantics follow
the report data (``tiqora_report_ticket_fact`` / ``tiqora_report_ticket_event``):

* closes are counted per transition into a closed state (a note added to a closed
  ticket is no second close); solution minutes use the LAST close (Znuny
  ``_TicketGetClosed`` is a MAX), the old code took the first close;
* SLA breaches come from the history, so they survive closing a ticket (the old
  code read the ``escalation_*`` columns, which Znuny zeroes on close);
* ``open_snapshot`` labels owners with "<First> <Last>" instead of the login;
* ``open_snapshot`` and the "owned open" side of ``agent_workload`` describe the
  tickets that are open now and ignore ``date_from``/``date_to`` (as before);
* every row is returned: the built-ins' ``top_n`` (15 plus ``__other__``) is removed
  from the copies these wrappers run.

A result cut by the row cap or the percentile sample cap is recorded in
:attr:`StatsService.truncated` (the API turns it into ``X-Tiqora-Truncated``).

All queries of one :class:`StatsService` (= one request) share one inline time
budget (R-INLINEBUDGET); a used-up budget raises
:class:`~tiqora.reporting.sql.ReportQueryTimeout`, which the API maps to 504.
Until the first report-data build has finished (``facts.ready`` false) the history
based numbers are incomplete.

Bucketing helper :func:`_bucket_key` is kept for the time-zone tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.user import Users
from tiqora.domain.timezones import to_zone
from tiqora.extensions.core import get_registry
from tiqora.filters import AllOf, Condition, Filter, FilterNode
from tiqora.jobs.service import utc_now
from tiqora.reporting.catalogue import BUILTIN_REPORTS, QUEUE_PARAM_FIELD
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition, TimeWindow
from tiqora.reporting.engine import QueryBudget, raw_measure_values, run_report
from tiqora.reporting.schemas import Cell, ReportResultOut
from tiqora.reporting.settings import ReportingSettings

Granularity = Literal["day", "week", "month"]
Dimension = Literal["queue", "state", "priority", "owner"]

#: window start when ``date_from`` is not given (the report definition limit)
DEFAULT_SPAN_DAYS = 3660
#: wrappers return every row; the engine's row cap at its configured maximum
STATS_ROW_CAP = 10_000


# ---------------------------------------------------------------------------
# Typed params / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatsFilters:
    """Common filter set accepted by every report.

    ``date_from``/``date_to`` bound ``Ticket.create_time`` (and, for the
    "closed" side of a report, ``TicketHistory.create_time``); both ends are
    inclusive when set. They are naive UTC like the columns; ``time_zone`` is
    the zone whose calendar days/weeks/months the buckets follow.
    """

    date_from: datetime | None = None
    date_to: datetime | None = None
    time_zone: str = "UTC"
    queue_id: int | None = None
    state_id: int | None = None
    priority_id: int | None = None
    type_id: int | None = None
    customer_id: str | None = None


@dataclass(frozen=True)
class VolumePoint:
    """One bucket of the ticket-volume-over-time report."""

    bucket: str  # ISO date of bucket start
    created: int
    closed: int


@dataclass(frozen=True)
class DimensionCount:
    """One row of an open-tickets-by-<dimension> snapshot report."""

    id: int | None
    label: str
    count: int


@dataclass(frozen=True)
class SlaStats:
    """SLA/escalation snapshot plus first-response/solution time distributions.

    ``*_minutes`` are raw per-ticket sample lists (minutes from creation to
    the relevant event) so the caller can compute percentiles/histograms
    without the service having to guess a bucketing scheme.
    """

    total: int
    escalated: int
    first_response_breached: int
    update_breached: int
    solution_breached: int
    first_response_minutes: list[float] = field(default_factory=list)
    solution_minutes: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class AgentWorkloadItem:
    """Per-agent ticket load for the workload report."""

    user_id: int
    login: str
    name: str
    owned_open: int
    closed_in_period: int


@dataclass(frozen=True)
class BacklogPoint:
    """One bucket of the backlog-trend report (running open-ticket count)."""

    bucket: str
    open_count: int


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _bucket_key(dt: datetime | date, granularity: Granularity, zone: str = "UTC") -> date:
    # DB datetimes are naive UTC; bucket by the viewer's calendar day.
    d = to_zone(dt, zone).date() if isinstance(dt, datetime) else dt
    if granularity == "day":
        return d
    if granularity == "week":
        return d - timedelta(days=d.weekday())  # Monday of that week
    return d.replace(day=1)


#: extra-filter field names per source (all current ticket values, as the old code filtered)
_TICKET_FIELDS = {
    "state": "state",
    "priority": "priority",
    "type": "type",
    "customer": "customer",
}
_EVENT_FIELDS = {
    "state": "ticket_state",
    "priority": "ticket_priority",
    "type": "ticket_type",
    "customer": "customer",
}


def _extra_filter(filters: StatsFilters, names: Mapping[str, str]) -> Filter | None:
    conditions: list[FilterNode] = []
    for attr, value in (
        ("state", filters.state_id),
        ("priority", filters.priority_id),
        ("type", filters.type_id),
        ("customer", filters.customer_id),
    ):
        if value is not None:
            conditions.append(Condition(field=names[attr], op="eq", value=value))
    if not conditions:
        return None
    return Filter(where=conditions[0] if len(conditions) == 1 else AllOf(all=conditions))


def _window(filters: StatsFilters, now: datetime) -> TimeWindow | None:
    """Exact UTC bounds, end exclusive; ``None`` for an empty (inverted) range."""
    start = filters.date_from or now - timedelta(days=DEFAULT_SPAN_DAYS)
    end = filters.date_to + timedelta(microseconds=1) if filters.date_to is not None else now
    if end <= start:
        return None
    zone = filters.time_zone
    return TimeWindow(
        start_utc=start,
        end_utc=end,
        start_date=to_zone(start, zone).date(),
        end_date=to_zone(end - timedelta(microseconds=1), zone).date(),
        time_zone=zone,
    )


def _builtin(key: str) -> ReportDefinition:
    """Copy of a built-in definition with ``top_n`` removed (no ``__other__`` row)."""
    report = get_registry().get(BUILTIN_REPORTS, key)
    assert report is not None, key
    defn = report.definition
    return defn.model_copy(
        update={"dimensions": [d.model_copy(update={"top_n": None}) for d in defn.dimensions]}
    )


def _inline(
    source: str, measures: list[str], dimensions: list[str] | None = None
) -> ReportDefinition:
    return ReportDefinition.model_validate(
        {
            "source": source,
            "dimensions": [{"key": d} for d in dimensions or []],
            "measures": [{"key": m} for m in measures],
            "parameters": [{"key": "queues", "field": QUEUE_PARAM_FIELD[source], "op": "in"}],
        }
    )


def _int(cell: Cell) -> int:
    return int(cell) if cell is not None else 0


class StatsService:
    """One instance per request: its inline time budget is shared by all its queries.

    ``reporting`` defaults to the section defaults (the API passes the stored
    settings); ``budget`` defaults to a fresh :meth:`QueryBudget.inline`.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        reporting: ReportingSettings | None = None,
        budget: QueryBudget | None = None,
    ) -> None:
        self._session = session
        self._reporting = (
            reporting if reporting is not None else ReportingSettings.model_validate({})
        )
        self._budget = budget if budget is not None else QueryBudget.inline(self._reporting)
        #: why a result of this request is incomplete ("rows", "samples"); see the API header
        self.truncated: list[str] = []

    # -- engine access ---------------------------------------------------

    def _params(
        self,
        filters: StatsFilters,
        names: Mapping[str, str],
        window: TimeWindow | None,
        **extra: Any,
    ) -> RunParams:
        parameters = {} if filters.queue_id is None else {"queues": [filters.queue_id]}
        return RunParams(
            time_zone=filters.time_zone,
            parameters=parameters,
            window_override=window,
            extra_filter=_extra_filter(filters, names),
            **extra,
        )

    async def _run(
        self, user_id: int, defn: ReportDefinition, params: RunParams
    ) -> ReportResultOut:
        result = await run_report(
            self._session,
            defn=defn,
            params=params,
            user_id=user_id,
            report_key=None,
            reporting=self._reporting,
            row_cap=STATS_ROW_CAP,
            use_cache=False,
            budget=self._budget,
        )
        if result.truncated:
            self.truncated.append("rows")
        return result

    async def _raw(
        self, user_id: int, defn: ReportDefinition, measure: str, params: RunParams
    ) -> list[float]:
        return await raw_measure_values(
            self._session,
            defn=defn,
            measure_key=measure,
            params=params,
            user_id=user_id,
            reporting=self._reporting,
            budget=self._budget,
            on_capped=lambda: self.truncated.append("samples"),
        )

    # -- reports -------------------------------------------------------

    async def ticket_volume(
        self,
        user_id: int,
        filters: StatsFilters,
        granularity: Granularity = "day",
    ) -> list[VolumePoint]:
        """Tickets created vs. closed per bucket (day/week/month); buckets with data only."""
        window = _window(filters, utc_now())
        if window is None:
            return []
        result = await self._run(
            user_id,
            _builtin("builtin.volume"),
            self._params(filters, _EVENT_FIELDS, window, grain=granularity),
        )
        created: dict[str, int] = {}
        closed: dict[str, int] = {}
        for bucket, kind, events in result.rows:
            count = _int(events)
            if count <= 0 or bucket is None:
                continue  # zero-filled buckets
            target = created if kind == "create" else closed
            key = str(bucket)[:10]
            target[key] = target.get(key, 0) + count
        return [
            VolumePoint(bucket=b, created=created.get(b, 0), closed=closed.get(b, 0))
            for b in sorted(set(created) | set(closed))
        ]

    async def open_snapshot(
        self, user_id: int, filters: StatsFilters, dimension: Dimension
    ) -> list[DimensionCount]:
        """Current open-ticket count grouped by *dimension* (queue/state/priority/owner)."""
        result = await self._open_by(user_id, filters, dimension)
        labels = result.labels.get(dimension, {})
        counts: list[tuple[Cell, int]] = [(row[0], _int(row[1])) for row in result.rows]
        counts.sort(key=lambda kv: -kv[1])
        return [
            DimensionCount(
                id=int(k) if k is not None else None,
                label=labels.get(str(k), str(k)),
                count=c,
            )
            for k, c in counts
        ]

    async def _open_by(
        self, user_id: int, filters: StatsFilters, dimension: Dimension
    ) -> ReportResultOut:
        """``builtin.open_now`` grouped by *dimension*; not date-bound (the tickets open now)."""
        return await self._run(
            user_id,
            _builtin("builtin.open_now"),
            self._params(filters, _TICKET_FIELDS, None, dimension_choices={0: dimension}),
        )

    async def sla_stats(self, user_id: int, filters: StatsFilters) -> SlaStats:
        """Tickets created in the window: breaches from the history, plus time samples."""
        now = utc_now()
        window = _window(filters, now)
        if window is None:
            return SlaStats(0, 0, 0, 0, 0)
        params = self._params(filters, _TICKET_FIELDS, window)
        kpis = _inline(
            "tickets",
            ["tickets", "response_breaches", "update_breached_tickets", "solution_breaches"],
        )
        rows = (await self._run(user_id, kpis, params)).rows
        total, fr_breached, upd_breached, sol_breached = (
            [_int(c) for c in rows[0]] if rows else [0, 0, 0, 0]
        )
        # currently escalated open tickets created in the window (as before)
        esc_rows = (await self._run(user_id, _inline("open_tickets", ["escalated"]), params)).rows
        escalated = _int(esc_rows[0][0]) if esc_rows else 0
        first_response = await self._raw(
            user_id,
            _inline("tickets", ["first_response_clock_p50"]),
            "first_response_clock_p50",
            params,
        )
        solution = await self._raw(
            user_id, _inline("tickets", ["solution_clock_p50"]), "solution_clock_p50", params
        )
        return SlaStats(
            total=total,
            escalated=escalated,
            first_response_breached=fr_breached,
            update_breached=upd_breached,
            solution_breached=sol_breached,
            first_response_minutes=first_response,
            solution_minutes=solution,
        )

    async def agent_workload(self, user_id: int, filters: StatsFilters) -> list[AgentWorkloadItem]:
        """Per-agent open-ticket ownership + tickets closed in the filtered period."""
        owned = {
            int(row[0]): _int(row[1])
            for row in (await self._open_by(user_id, filters, "owner")).rows
            if row[0] is not None
        }
        closed: dict[int, int] = {}
        window = _window(filters, utc_now())
        if window is not None:
            by_owner = _inline("tickets", ["closed_tickets"], ["owner"]).model_copy(
                update={"time_field": "closed"}
            )
            result = await self._run(
                user_id, by_owner, self._params(filters, _TICKET_FIELDS, window)
            )
            closed = {int(r[0]): _int(r[1]) for r in result.rows if r[0] is not None and _int(r[1])}

        user_ids = set(owned) | set(closed)
        if not user_ids:
            return []
        # Exclude soft-invalidated agents (valid_id != 1): a departed agent
        # who still owns open tickets should not appear in the workload report.
        rows = await self._session.execute(
            select(Users.id, Users.login, Users.first_name, Users.last_name).where(
                Users.id.in_(user_ids), Users.valid_id == 1
            )
        )
        out = [
            AgentWorkloadItem(
                user_id=uid,
                login=login,
                name=f"{fn} {ln}".strip(),
                owned_open=owned.get(uid, 0),
                closed_in_period=closed.get(uid, 0),
            )
            for uid, login, fn, ln in rows.all()
        ]
        out.sort(key=lambda a: -(a.owned_open + a.closed_in_period))
        return out

    async def backlog_trend(
        self, user_id: int, filters: StatsFilters, granularity: Granularity = "day"
    ) -> list[BacklogPoint]:
        """Running open-ticket count per bucket, derived from :meth:`ticket_volume`.

        ``open_count`` at each bucket = cumulative created minus cumulative
        closed up to and including that bucket (floored at zero — filters
        can make the running count negative if closes precede the visible
        window, e.g. a ``date_from`` cutting off older creates).
        """
        volume = await self.ticket_volume(user_id, filters, granularity)
        running = 0
        out: list[BacklogPoint] = []
        for v in volume:
            running += v.created - v.closed
            out.append(BacklogPoint(bucket=v.bucket, open_count=max(running, 0)))
        return out
