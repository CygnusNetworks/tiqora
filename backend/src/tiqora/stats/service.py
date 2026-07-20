"""Ticket statistics and reporting aggregations.

Permission-filtered (by the caller's ``ro`` queues, same scoping as
:class:`tiqora.domain.ticket_service.TicketService`) SQL aggregations over
the legacy ``ticket``/``article``/``ticket_history`` tables, backing the
``/api/v1/stats/*`` REST endpoints and the agent Reports UI.

This is a modern equivalent of Znuny's ``Kernel::System::Stats`` /
AgentStatistics module, not a port: instead of Znuny's dynamic
report-object framework (arbitrary user-defined X/Y-axis stat objects), it
provides a fixed set of purpose-built reports (volume, backlog, open
snapshot, SLA, agent workload) with typed parameters and dataclass results.

Bucketing (day/week/month) is done in Python after fetching bare
``(id, timestamp)`` pairs rather than with dialect-specific SQL date-trunc
functions, so the same code runs unmodified against both the MySQL and
PostgreSQL backends the rest of the codebase supports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.article import Article, ArticleSenderType
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.queue_service import OPEN_STATE_TYPES
from tiqora.permissions.engine import PermissionEngine

Granularity = Literal["day", "week", "month"]
Dimension = Literal["queue", "state", "priority", "owner"]


# ---------------------------------------------------------------------------
# Typed params / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatsFilters:
    """Common filter set accepted by every report.

    ``date_from``/``date_to`` bound ``Ticket.create_time`` (and, for the
    "closed" side of a report, ``TicketHistory.create_time``); both ends are
    inclusive when set.
    """

    date_from: datetime | None = None
    date_to: datetime | None = None
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


def _bucket_key(dt: datetime | date, granularity: Granularity) -> date:
    d = dt.date() if isinstance(dt, datetime) else dt
    if granularity == "day":
        return d
    if granularity == "week":
        return d - timedelta(days=d.weekday())  # Monday of that week
    return d.replace(day=1)


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)

    # -- permission scoping ---------------------------------------------

    async def _allowed_queue_ids(self, user_id: int, queue_id: int | None) -> set[int]:
        """Permission-filtered queue id set for *user_id* (``ro``).

        Empty set means "no visible data" — every report treats it as zero
        rows rather than raising.
        """
        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return set()
        rows = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed = set(rows.scalars().all())
        if not allowed:
            return set()
        if queue_id is not None:
            return {queue_id} if queue_id in allowed else set()
        return allowed

    # -- shared lookups ----------------------------------------------------

    async def _open_state_ids(self) -> set[int]:
        rows = await self._session.execute(
            select(TicketState.id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(TicketStateType.name.in_(OPEN_STATE_TYPES), TicketState.valid_id == 1)
        )
        return set(rows.scalars().all())

    async def _closed_state_ids(self) -> set[int]:
        rows = await self._session.execute(
            select(TicketState.id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(TicketStateType.name == "closed")
        )
        return set(rows.scalars().all())

    def _apply_filters(
        self, stmt: Select[Any], filters: StatsFilters, allowed_queues: set[int]
    ) -> Select[Any]:
        stmt = stmt.where(Ticket.queue_id.in_(allowed_queues))
        if filters.state_id is not None:
            stmt = stmt.where(Ticket.ticket_state_id == filters.state_id)
        if filters.priority_id is not None:
            stmt = stmt.where(Ticket.ticket_priority_id == filters.priority_id)
        if filters.type_id is not None:
            stmt = stmt.where(Ticket.type_id == filters.type_id)
        if filters.customer_id is not None:
            stmt = stmt.where(Ticket.customer_id == filters.customer_id)
        return stmt

    async def _labels_for(self, dimension: Dimension, ids: Iterable[int]) -> dict[int, str]:
        id_list = list(ids)
        if not id_list:
            return {}
        if dimension == "queue":
            rows = await self._session.execute(
                select(Queue.id, Queue.name).where(Queue.id.in_(id_list))
            )
        elif dimension == "state":
            rows = await self._session.execute(
                select(TicketState.id, TicketState.name).where(TicketState.id.in_(id_list))
            )
        elif dimension == "priority":
            rows = await self._session.execute(
                select(TicketPriority.id, TicketPriority.name).where(TicketPriority.id.in_(id_list))
            )
        else:
            rows = await self._session.execute(
                select(Users.id, Users.login).where(Users.id.in_(id_list))
            )
        return {i: n for i, n in rows.all()}

    # -- reports -------------------------------------------------------

    async def ticket_volume(
        self,
        user_id: int,
        filters: StatsFilters,
        granularity: Granularity = "day",
    ) -> list[VolumePoint]:
        """Tickets created vs. closed per bucket (day/week/month)."""
        allowed = await self._allowed_queue_ids(user_id, filters.queue_id)
        if not allowed:
            return []

        created_stmt = self._apply_filters(select(Ticket.create_time), filters, allowed)
        if filters.date_from is not None:
            created_stmt = created_stmt.where(Ticket.create_time >= filters.date_from)
        if filters.date_to is not None:
            created_stmt = created_stmt.where(Ticket.create_time <= filters.date_to)
        created_counts: dict[date, int] = defaultdict(int)
        for (ct,) in (await self._session.execute(created_stmt)).all():
            created_counts[_bucket_key(ct, granularity)] += 1

        closed_state_ids = await self._closed_state_ids()
        closed_counts: dict[date, int] = defaultdict(int)
        if closed_state_ids:
            hist_stmt = self._apply_filters(
                select(TicketHistory.create_time).join(
                    Ticket, Ticket.id == TicketHistory.ticket_id
                ),
                filters,
                allowed,
            ).where(TicketHistory.state_id.in_(closed_state_ids))
            if filters.date_from is not None:
                hist_stmt = hist_stmt.where(TicketHistory.create_time >= filters.date_from)
            if filters.date_to is not None:
                hist_stmt = hist_stmt.where(TicketHistory.create_time <= filters.date_to)
            for (ct,) in (await self._session.execute(hist_stmt)).all():
                closed_counts[_bucket_key(ct, granularity)] += 1

        buckets = sorted(set(created_counts) | set(closed_counts))
        return [
            VolumePoint(
                bucket=b.isoformat(),
                created=created_counts.get(b, 0),
                closed=closed_counts.get(b, 0),
            )
            for b in buckets
        ]

    async def open_snapshot(
        self, user_id: int, filters: StatsFilters, dimension: Dimension
    ) -> list[DimensionCount]:
        """Current open-ticket count grouped by *dimension* (queue/state/priority/owner)."""
        allowed = await self._allowed_queue_ids(user_id, filters.queue_id)
        if not allowed:
            return []
        open_state_ids = await self._open_state_ids()
        if not open_state_ids:
            return []

        stmt = self._apply_filters(select(Ticket), filters, allowed).where(
            Ticket.ticket_state_id.in_(open_state_ids),
            Ticket.archive_flag == 0,
        )
        rows = (await self._session.execute(stmt)).scalars().all()

        counts: dict[int, int] = defaultdict(int)
        for t in rows:
            key = {
                "queue": t.queue_id,
                "state": t.ticket_state_id,
                "priority": t.ticket_priority_id,
                "owner": t.user_id,
            }[dimension]
            counts[key] += 1

        labels = await self._labels_for(dimension, counts.keys())
        return [
            DimensionCount(id=k, label=labels.get(k, str(k)), count=c)
            for k, c in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    async def sla_stats(self, user_id: int, filters: StatsFilters) -> SlaStats:
        """Escalation snapshot + first-response/solution time distributions."""
        allowed = await self._allowed_queue_ids(user_id, filters.queue_id)
        if not allowed:
            return SlaStats(
                total=0,
                escalated=0,
                first_response_breached=0,
                update_breached=0,
                solution_breached=0,
            )

        stmt = self._apply_filters(select(Ticket), filters, allowed)
        if filters.date_from is not None:
            stmt = stmt.where(Ticket.create_time >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(Ticket.create_time <= filters.date_to)
        tickets = list((await self._session.execute(stmt)).scalars().all())

        total = len(tickets)
        now_epoch = int(datetime.now(UTC).timestamp())
        escalated = sum(1 for t in tickets if t.escalation_time and t.escalation_time <= now_epoch)
        fr_breached = sum(
            1
            for t in tickets
            if t.escalation_response_time and t.escalation_response_time <= now_epoch
        )
        upd_breached = sum(
            1 for t in tickets if t.escalation_update_time and t.escalation_update_time <= now_epoch
        )
        sol_breached = sum(
            1
            for t in tickets
            if t.escalation_solution_time and t.escalation_solution_time <= now_epoch
        )

        first_response_minutes: list[float] = []
        solution_minutes: list[float] = []
        ticket_ids = [t.id for t in tickets]
        create_by_id = {t.id: t.create_time for t in tickets}

        if ticket_ids:
            fr_rows = await self._session.execute(
                select(Article.ticket_id, func.min(Article.create_time))
                .join(ArticleSenderType, ArticleSenderType.id == Article.article_sender_type_id)
                .where(
                    Article.ticket_id.in_(ticket_ids),
                    ArticleSenderType.name == "agent",
                    Article.is_visible_for_customer == 1,
                )
                .group_by(Article.ticket_id)
            )
            for tid, first_time in fr_rows.all():
                ct = create_by_id.get(tid)
                if ct is not None and first_time is not None:
                    first_response_minutes.append((first_time - ct).total_seconds() / 60)

            closed_ids = await self._closed_state_ids()
            if closed_ids:
                sol_rows = await self._session.execute(
                    select(TicketHistory.ticket_id, func.min(TicketHistory.create_time))
                    .where(
                        TicketHistory.ticket_id.in_(ticket_ids),
                        TicketHistory.state_id.in_(closed_ids),
                    )
                    .group_by(TicketHistory.ticket_id)
                )
                for tid, closed_time in sol_rows.all():
                    ct = create_by_id.get(tid)
                    if ct is not None and closed_time is not None:
                        solution_minutes.append((closed_time - ct).total_seconds() / 60)

        return SlaStats(
            total=total,
            escalated=escalated,
            first_response_breached=fr_breached,
            update_breached=upd_breached,
            solution_breached=sol_breached,
            first_response_minutes=first_response_minutes,
            solution_minutes=solution_minutes,
        )

    async def agent_workload(self, user_id: int, filters: StatsFilters) -> list[AgentWorkloadItem]:
        """Per-agent open-ticket ownership + tickets closed in the filtered period."""
        allowed = await self._allowed_queue_ids(user_id, filters.queue_id)
        if not allowed:
            return []

        open_state_ids = await self._open_state_ids()
        owned_stmt = self._apply_filters(
            select(Ticket.user_id, func.count()), filters, allowed
        ).where(Ticket.archive_flag == 0)
        if open_state_ids:
            owned_stmt = owned_stmt.where(Ticket.ticket_state_id.in_(open_state_ids))
        owned_stmt = owned_stmt.group_by(Ticket.user_id)
        owned_counts: dict[int, int] = {
            uid: cnt for uid, cnt in (await self._session.execute(owned_stmt)).all()
        }

        closed_ids = await self._closed_state_ids()
        closed_counts: dict[int, int] = {}
        if closed_ids:
            hist_stmt = self._apply_filters(
                select(Ticket.user_id, func.count(func.distinct(TicketHistory.ticket_id))).join(
                    TicketHistory, TicketHistory.ticket_id == Ticket.id
                ),
                filters,
                allowed,
            ).where(TicketHistory.state_id.in_(closed_ids))
            if filters.date_from is not None:
                hist_stmt = hist_stmt.where(TicketHistory.create_time >= filters.date_from)
            if filters.date_to is not None:
                hist_stmt = hist_stmt.where(TicketHistory.create_time <= filters.date_to)
            hist_stmt = hist_stmt.group_by(Ticket.user_id)
            closed_counts = {
                uid: cnt for uid, cnt in (await self._session.execute(hist_stmt)).all()
            }

        user_ids = set(owned_counts) | set(closed_counts)
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
                owned_open=owned_counts.get(uid, 0),
                closed_in_period=closed_counts.get(uid, 0),
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
