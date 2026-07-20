"""``/api/v1/stats/*`` reporting endpoints.

Each report has a JSON endpoint and a ``.csv`` sibling (same query-param
filters, streamed CSV — mirrors the export pattern in
:mod:`tiqora.api.v1.tickets`). Gated on any authenticated agent
(``CurrentUser``); data itself is scoped to the caller's ``ro`` queues by
:class:`tiqora.stats.service.StatsService`, the same permission model used
by ``GET /tickets``.
"""

from __future__ import annotations

import csv
from collections.abc import AsyncGenerator, Iterable
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.stats.schemas import (
    AgentWorkloadItemOut,
    BacklogPointOut,
    BacklogTrendOut,
    DimensionCountOut,
    OpenSnapshotOut,
    SlaStatsOut,
    TicketVolumeOut,
    VolumePointOut,
)
from tiqora.stats.service import Dimension, Granularity, StatsFilters, StatsService

router = APIRouter(prefix="/stats", tags=["stats"])


class _EchoWriter:
    """File-like shim so ``csv.writer`` yields each row as a string (see tickets.py)."""

    def write(self, value: str) -> str:
        return value


def _filters(
    date_from: datetime | None,
    date_to: datetime | None,
    queue_id: int | None,
    state_id: int | None,
    priority_id: int | None,
    type_id: int | None,
    customer_id: str | None,
) -> StatsFilters:
    return StatsFilters(
        date_from=date_from,
        date_to=date_to,
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        type_id=type_id,
        customer_id=customer_id,
    )


async def _csv_stream(header: list[str], rows: Iterable[list[str]]) -> AsyncGenerator[bytes, None]:
    writer = csv.writer(_EchoWriter(), delimiter=";")
    yield b"\xef\xbb\xbf"  # UTF-8 BOM
    yield writer.writerow(header).encode("utf-8")
    for row in rows:
        yield writer.writerow(row).encode("utf-8")


# ---------------------------------------------------------------------------
# Ticket volume
# ---------------------------------------------------------------------------


@router.get("/volume", response_model=TicketVolumeOut)
async def ticket_volume(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
    granularity: Granularity = "day",
) -> TicketVolumeOut:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    points = await svc.ticket_volume(user.id, filters, granularity)
    return TicketVolumeOut(
        granularity=granularity, points=[VolumePointOut.from_dataclass(p) for p in points]
    )


@router.get("/volume.csv")
async def ticket_volume_csv(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
    granularity: Granularity = "day",
) -> StreamingResponse:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    points = await svc.ticket_volume(user.id, filters, granularity)
    rows = [[p.bucket, str(p.created), str(p.closed)] for p in points]
    return StreamingResponse(
        _csv_stream(["Bucket", "Created", "Closed"], rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ticket-volume.csv"'},
    )


# ---------------------------------------------------------------------------
# Open snapshot (by queue / state / priority / owner)
# ---------------------------------------------------------------------------


@router.get("/open-snapshot", response_model=OpenSnapshotOut)
async def open_snapshot(
    user: CurrentUser,
    session: DbSession,
    dimension: Dimension = "queue",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> OpenSnapshotOut:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    items = await svc.open_snapshot(user.id, filters, dimension)
    return OpenSnapshotOut(
        dimension=dimension, items=[DimensionCountOut.from_dataclass(i) for i in items]
    )


@router.get("/open-snapshot.csv")
async def open_snapshot_csv(
    user: CurrentUser,
    session: DbSession,
    dimension: Dimension = "queue",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> StreamingResponse:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    items = await svc.open_snapshot(user.id, filters, dimension)
    rows = [[str(i.id) if i.id is not None else "", i.label, str(i.count)] for i in items]
    return StreamingResponse(
        _csv_stream(["Id", "Label", "Count"], rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="open-snapshot.csv"'},
    )


# ---------------------------------------------------------------------------
# SLA / escalation
# ---------------------------------------------------------------------------


@router.get("/sla", response_model=SlaStatsOut)
async def sla_stats(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> SlaStatsOut:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    stats = await svc.sla_stats(user.id, filters)
    return SlaStatsOut.from_dataclass(stats)


@router.get("/sla.csv")
async def sla_stats_csv(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> StreamingResponse:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    s = await svc.sla_stats(user.id, filters)
    rows = [
        [
            "Total",
            "Escalated",
            "FirstResponseBreached",
            "UpdateBreached",
            "SolutionBreached",
        ],
        [
            str(s.total),
            str(s.escalated),
            str(s.first_response_breached),
            str(s.update_breached),
            str(s.solution_breached),
        ],
    ]

    async def gen() -> AsyncGenerator[bytes, None]:
        writer = csv.writer(_EchoWriter(), delimiter=";")
        yield b"\xef\xbb\xbf"
        for row in rows:
            yield writer.writerow(row).encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sla-stats.csv"'},
    )


# ---------------------------------------------------------------------------
# Agent workload
# ---------------------------------------------------------------------------


@router.get("/agent-workload", response_model=list[AgentWorkloadItemOut])
async def agent_workload(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> list[AgentWorkloadItemOut]:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    items = await svc.agent_workload(user.id, filters)
    return [AgentWorkloadItemOut.from_dataclass(i) for i in items]


@router.get("/agent-workload.csv")
async def agent_workload_csv(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
) -> StreamingResponse:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    items = await svc.agent_workload(user.id, filters)
    rows = [[i.login, i.name, str(i.owned_open), str(i.closed_in_period)] for i in items]
    return StreamingResponse(
        _csv_stream(["Login", "Name", "OwnedOpen", "ClosedInPeriod"], rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="agent-workload.csv"'},
    )


# ---------------------------------------------------------------------------
# Backlog trend
# ---------------------------------------------------------------------------


@router.get("/backlog", response_model=BacklogTrendOut)
async def backlog_trend(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
    granularity: Granularity = "day",
) -> BacklogTrendOut:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    points = await svc.backlog_trend(user.id, filters, granularity)
    return BacklogTrendOut(
        granularity=granularity, points=[BacklogPointOut.from_dataclass(p) for p in points]
    )


@router.get("/backlog.csv")
async def backlog_trend_csv(
    user: CurrentUser,
    session: DbSession,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
    priority_id: int | None = None,
    type_id: int | None = None,
    customer_id: str | None = None,
    granularity: Granularity = "day",
) -> StreamingResponse:
    filters = _filters(date_from, date_to, queue_id, state_id, priority_id, type_id, customer_id)
    svc = StatsService(session)
    points = await svc.backlog_trend(user.id, filters, granularity)
    rows = [[p.bucket, str(p.open_count)] for p in points]
    return StreamingResponse(
        _csv_stream(["Bucket", "OpenCount"], rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="backlog-trend.csv"'},
    )
