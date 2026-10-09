"""Resumable first build of the report data (facts, events, daily snapshots).

Runs as the ``core.report_backfill`` job (or from the CLI). The cursor lives in
``tiqora_report_state`` and moves forward after every committed chunk, so an
interrupted run continues where it stopped; every step is idempotent (a ticket's
rows are replaced as a whole), so repeating a chunk is harmless.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.legacy.ticket import Ticket, TicketHistory
from tiqora.db.tiqora.models import TiqoraReportTicketFact
from tiqora.jobs.context import JobCancelled, JobContext
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import utc_now
from tiqora.reporting.cache import purge_report_cache_at
from tiqora.reporting.facts_store import FACT_CHUNK_SIZE, rebuild_and_commit
from tiqora.reporting.settings import load_reporting_settings, reporting_time_zone
from tiqora.reporting.snapshots import snapshot_days
from tiqora.reporting.state import load_state

SNAPSHOT_WINDOW_DAYS: Final = 31
TICKETS_MESSAGE: Final = "Tickets werden ausgewertet …"
SNAPSHOTS_MESSAGE: Final = "Tagesstände werden berechnet …"


class ReportBackfillPayload(BaseModel):
    #: Start at the first ticket id created on/after this local date (reporting time
    #: zone); every higher ticket id is processed too. Snapshots start at that date.
    since: date | None = None
    #: ignore the stored cursor and start over
    restart: bool = False
    snapshots: bool = True


ProgressFn = Callable[[int, int | None, str | None], Awaitable[None]]
CancelFn = Callable[[], Awaitable[None]]


def _local_midnight_utc(day: date, zone: str) -> datetime:
    return datetime.combine(day, time.min, ZoneInfo(zone)).astimezone(UTC).replace(tzinfo=None)


def _local_date(moment: datetime, zone: str) -> date:
    return moment.replace(tzinfo=UTC).astimezone(ZoneInfo(zone)).date()


@dataclass(frozen=True, slots=True)
class _Run:
    zone: str
    start: int
    cursor: int
    target: int
    #: ``since`` of the run being worked on: the stored one when resuming, never the
    #: payload's (the ticket range and the snapshot days must belong together)
    since: date | None
    #: ``backfill_run_token`` of the run; a writer of a replaced run must not touch the state
    token: str


async def _begin(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    payload: ReportBackfillPayload,
) -> _Run:
    """Prepare the state (start a new run or pick up the stored one)."""
    async with factory() as session:
        state = await load_state(session, for_update=True, retry_on_deadlock=True)
        values = await load_reporting_settings(session, settings)
        zone = await reporting_time_zone(session, values)
        if state.last_history_id == 0:
            # CLI path without a running worker: older history belongs to this run,
            # newer history to the incremental tick.
            state.last_history_id = int(
                (await session.execute(select(func.max(TicketHistory.id)))).scalar_one() or 0
            )
        resume = (
            not payload.restart
            and state.backfill_status == "running"
            and state.backfill_cursor_ticket_id is not None
            and state.backfill_start_ticket_id is not None
            and state.backfill_target_ticket_id is not None
            and state.backfill_run_token is not None
        )
        if not resume:
            if payload.since is not None:
                first = (
                    await session.execute(
                        select(func.min(Ticket.id)).where(
                            Ticket.create_time >= _local_midnight_utc(payload.since, zone)
                        )
                    )
                ).scalar_one()
            else:
                first = (await session.execute(select(func.min(Ticket.id)))).scalar_one()
            target = int((await session.execute(select(func.max(Ticket.id)))).scalar_one() or 0)
            # no ticket on/after `since`: an empty range
            start = int(first) - 1 if first is not None else target
            now = utc_now()
            state.backfill_status = "running"
            state.backfill_start_ticket_id = start
            state.backfill_cursor_ticket_id = start
            state.backfill_target_ticket_id = target
            state.backfill_since = payload.since
            state.backfill_started_at = now
            state.backfill_run_token = uuid.uuid4().hex
            state.backfill_finished_at = None
            state.updated_at = now
        if resume:
            # a takeover: the token rotates so the previous owner (a CLI run, a reaped
            # job that woke up) stops at its next state write, and updated_at is fresh so
            # the worker tick does not mistake the run for an abandoned one
            state.backfill_run_token = uuid.uuid4().hex
            state.updated_at = utc_now()
        assert state.backfill_start_ticket_id is not None
        assert state.backfill_cursor_ticket_id is not None
        assert state.backfill_target_ticket_id is not None
        assert state.backfill_run_token is not None
        run = _Run(
            zone,
            state.backfill_start_ticket_id,
            state.backfill_cursor_ticket_id,
            state.backfill_target_ticket_id,
            state.backfill_since,
            state.backfill_run_token,
        )
        await session.commit()
    if not resume:
        # a new build (first backfill, admin rebuild, CLI): results cached from the old
        # data must not outlive it (best effort, never raises)
        await purge_report_cache_at(settings.redis_url)
    return run


async def _save_cursor(
    factory: async_sessionmaker[AsyncSession],
    run: _Run,
    cursor: int,
    *,
    finish: tuple[date | None, str] | None = None,
) -> None:
    """Move the cursor forward (or finish the run) if this run still owns the state.

    A run that was replaced (restart, another job or the CLI started a new one) or is
    already finished writes nothing and stops with :class:`JobCancelled`; the cursor
    never moves backwards.
    """
    async with factory() as session:
        state = await load_state(session, for_update=True, retry_on_deadlock=True)
        if state.backfill_run_token != run.token or state.backfill_status != "running":
            raise JobCancelled
        stamp = utc_now()
        state.backfill_cursor_ticket_id = max(cursor, state.backfill_cursor_ticket_id or 0)
        state.updated_at = stamp
        if finish is not None:
            state.backfill_status = "done"
            state.backfill_finished_at = stamp
            if finish[0] is not None:
                state.last_snapshot_date = finish[0]
                state.snapshot_time_zone = finish[1]
        await session.commit()


async def run_backfill(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    payload: ReportBackfillPayload,
    *,
    progress: ProgressFn,
    check_cancelled: CancelFn,
    sleep_seconds: float = 0.05,
    now: datetime | None = None,
) -> dict[str, int]:
    """Build facts for every ticket of the range, then the daily snapshots.

    A new run (``restart``, no stored cursor, or a state that is not "running") starts
    at the first ticket id (created on/after ``since`` when given) and ends at the
    highest ticket id of that moment; tickets created later belong to the incremental
    tick. A run that finds status "running" with a cursor continues after the cursor.
    A run whose state was taken over meanwhile (restart, other job, CLI) raises
    :class:`JobCancelled` at its next cursor write. The snapshot days start at the
    ``since`` of the run being worked on (the stored one when resuming).
    Returns ``{"tickets": n, "snapshot_days": d}``.
    """
    run = await _begin(factory, settings, payload)
    zone, start, cursor, target = run.zone, run.start, run.cursor, run.target
    total = max(target - start, 0)

    while cursor < target:
        async with factory() as session:
            ids = list(
                (
                    await session.execute(
                        select(Ticket.id)
                        .where(Ticket.id > cursor, Ticket.id <= target)
                        .order_by(Ticket.id)
                        .limit(FACT_CHUNK_SIZE)
                    )
                )
                .scalars()
                .all()
            )
        if not ids:
            break
        await rebuild_and_commit(factory, ids, now=now)
        cursor = ids[-1]
        await _save_cursor(factory, run, cursor)
        await progress(cursor - start, total, TICKETS_MESSAGE)
        await check_cancelled()
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)

    if cursor < target:  # the highest ids were deleted meanwhile: the phase is complete anyway
        await progress(total, total, TICKETS_MESSAGE)
    async with factory() as session:
        tickets = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Ticket)
                    .where(Ticket.id > start, Ticket.id <= target)
                )
            ).scalar_one()
        )

    days_done = 0
    yesterday: date | None = None
    if payload.snapshots:
        yesterday = _local_date(now or utc_now(), zone) - timedelta(days=1)
        first_day = run.since
        if first_day is None:
            async with factory() as session:
                oldest = (
                    await session.execute(select(func.min(TiqoraReportTicketFact.created_at)))
                ).scalar_one()
            first_day = _local_date(oldest, zone) if oldest is not None else None
        days: list[date] = []
        if first_day is not None:
            days = [first_day + timedelta(days=i) for i in range((yesterday - first_day).days + 1)]
        for i in range(0, len(days), SNAPSHOT_WINDOW_DAYS):
            window = days[i : i + SNAPSHOT_WINDOW_DAYS]
            await snapshot_days(factory, window, zone=zone, now=now)
            # proves liveness (updated_at) and stops a run that was taken over
            await _save_cursor(factory, run, cursor)
            days_done += len(window)
            # one bar over both phases: the snapshot days continue after the tickets
            await progress(total + days_done, total + len(days), SNAPSHOTS_MESSAGE)
            await check_cancelled()

    await _save_cursor(
        factory, run, target, finish=(yesterday, zone) if yesterday is not None else (None, zone)
    )
    return {"tickets": tickets, "snapshot_days": days_done}


async def run_backfill_job(ctx: JobContext, payload: Any) -> dict[str, int]:
    return await run_backfill(
        ctx.session_factory,
        ctx.settings,
        payload,
        progress=ctx.progress,
        check_cancelled=ctx.check_cancelled,
    )


REPORT_BACKFILL_JOB = JobHandler(
    kind="core.report_backfill",
    payload_model=ReportBackfillPayload,
    run=run_backfill_job,
    title_key="jobs.kinds.core.report_backfill",
    timeout_seconds=86_400,
)
