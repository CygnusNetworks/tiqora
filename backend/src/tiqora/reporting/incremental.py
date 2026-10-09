"""Incremental upkeep of the report data (the ``report_facts`` worker loop).

One tick keeps facts, events and daily snapshots current after the backfill:
it follows ``ticket_history`` by id with a watermark and a list of "gaps" (ids
below the watermark that were not visible yet - an insert that commits later
than a higher id), rebuilds tickets from the dirty queue, upgrades facts of an
old version, sweeps facts of deleted tickets and writes the missing daily
snapshots. Every step commits on its own and is idempotent, so a crash between
steps only repeats work.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.ticket import Ticket, TicketHistory
from tiqora.db.tiqora.models import TiqoraJob, TiqoraReportTicketFact
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import ACTIVE_STATUSES, enqueue_job, utc_now
from tiqora.reporting.backfill import REPORT_BACKFILL_JOB, ReportBackfillPayload
from tiqora.reporting.dirty import clear_dirty, take_dirty_ticket_ids
from tiqora.reporting.facts import FACT_VERSION
from tiqora.reporting.facts_store import delete_ticket_facts, fact_write_lock, rebuild_and_commit
from tiqora.reporting.settings import load_reporting_settings, reporting_time_zone
from tiqora.reporting.snapshots import snapshot_days
from tiqora.reporting.state import load_state

logger = structlog.get_logger(__name__)

HISTORY_BATCH: Final = 5000
MAX_BATCHES_PER_TICK: Final = 10
GAP_TTL_SECONDS: Final = 600
MAX_GAPS: Final = 5000
DIRTY_BATCH: Final = 1000
ORPHAN_SWEEP_EVERY_SECONDS: Final = 3600
ORPHAN_PAGE: Final = 5000
VERSION_UPGRADE_BATCH: Final = 500
SNAPSHOT_MAX_DAYS_PER_TICK: Final = 31
BACKFILL_DEDUPE_KEY: Final = "report_backfill"
#: a running backfill without job id and with a state write this recent is a CLI run
CLI_RUN_ACTIVE_SECONDS: Final = 600
#: back-off before a failed/cancelled backfill job is queued again: doubles per recent
#: failure from the first to the cap
BACKFILL_RETRY_BASE_SECONDS: Final = 300
BACKFILL_RETRY_MAX_SECONDS: Final = 3600
BACKFILL_FAILURE_WINDOW: Final = timedelta(hours=24)
_IN_CHUNK: Final = 1000

Gaps = dict[int, float]


def _load_gaps(raw: str | None) -> Gaps:
    try:
        data = json.loads(raw) if raw else []
        return {int(i): float(t) for i, t in data}
    except (ValueError, TypeError):
        return {}


def _dump_gaps(gaps: Gaps) -> str:
    return json.dumps(sorted([i, t] for i, t in gaps.items()))


async def _save_progress(
    factory: async_sessionmaker[AsyncSession],
    *,
    watermark: int,
    gaps: Gaps,
    now: datetime,
) -> None:
    async with factory() as session:
        state = await load_state(session, for_update=True, retry_on_deadlock=True)
        state.last_history_id = max(state.last_history_id, watermark)
        state.gap_ids = _dump_gaps(gaps)
        state.last_tick_at = now
        state.updated_at = now
        await session.commit()


def _backfill_retry_delay(failures: int) -> timedelta:
    seconds = BACKFILL_RETRY_BASE_SECONDS * 2 ** max(failures - 1, 0)
    return timedelta(seconds=min(seconds, BACKFILL_RETRY_MAX_SECONDS))


async def _ensure_backfill(
    factory: async_sessionmaker[AsyncSession], *, now: datetime, registry: Registry | None
) -> bool:
    """Queue (or re-queue) the backfill while the data is not complete; True when queued.

    A job that was reaped, failed or cancelled leaves the state "running"/"pending"
    with a dead job id: the tick queues a new one, which resumes from the stored
    cursor. Repeated failures back off (see :func:`_backfill_retry_delay`). A
    "running" state without job id and with a recent state write is a CLI run and
    is left alone.
    """
    async with factory() as session:
        state = await load_state(session, for_update=True)
        if state.backfill_status == "done":
            return False
        job = await session.get(TiqoraJob, state.backfill_job_id) if state.backfill_job_id else None
        if job is not None and job.status in ACTIVE_STATUSES:
            return False
        if (
            job is None
            and state.backfill_status == "running"
            and state.updated_at >= now - timedelta(seconds=CLI_RUN_ACTIVE_SECONDS)
        ):
            return False
        if job is not None and job.status in ("failed", "cancelled"):
            since = now - BACKFILL_FAILURE_WINDOW
            failures = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TiqoraJob)
                        .where(
                            TiqoraJob.kind == REPORT_BACKFILL_JOB.kind,
                            TiqoraJob.dedupe_key == BACKFILL_DEDUPE_KEY,
                            TiqoraJob.status.in_(("failed", "cancelled")),
                            func.coalesce(TiqoraJob.finished_at, TiqoraJob.created_at) >= since,
                        )
                    )
                ).scalar_one()
            )
            ended = job.finished_at or job.heartbeat_at or job.created_at
            if now < ended + _backfill_retry_delay(failures):
                return False
        new = await enqueue_job(
            session,
            kind=REPORT_BACKFILL_JOB.kind,
            payload=ReportBackfillPayload(),
            created_by=None,
            dedupe_key=BACKFILL_DEDUPE_KEY,
            registry=registry,
        )
        state.backfill_job_id = new.id
        await session.commit()
    return True


async def _rows_by_ids(session: AsyncSession, ids: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in range(0, len(ids), _IN_CHUNK):
        rows = await session.execute(
            select(TicketHistory.id, TicketHistory.ticket_id).where(
                TicketHistory.id.in_(ids[i : i + _IN_CHUNK])
            )
        )
        out.extend((int(r[0]), int(r[1])) for r in rows.all())
    return out


def _missing_ids(old_wm: int, batch_max: int, returned: set[int]) -> list[int]:
    """Ids in ``(old_wm, batch_max]`` not returned; at most MAX_GAPS, the highest ones."""
    out: list[int] = []
    i = batch_max
    while i > old_wm and len(out) < MAX_GAPS:
        if i not in returned:
            out.append(i)
        i -= 1
    return out


async def _follow_history(
    factory: async_sessionmaker[AsyncSession],
    *,
    watermark: int,
    gaps: Gaps,
    now: datetime,
    wall_clock: Callable[[], float],
) -> tuple[int, int, int, Gaps]:
    """Steps 3: returns (history rows, tickets rebuilt, watermark, gaps)."""
    rows_total = tickets_total = 0
    for batch in range(MAX_BATCHES_PER_TICK):
        async with factory() as session:
            gap_rows = await _rows_by_ids(session, sorted(gaps)) if batch == 0 and gaps else []
            rows = [
                (int(r[0]), int(r[1]))
                for r in (
                    await session.execute(
                        select(TicketHistory.id, TicketHistory.ticket_id)
                        .where(TicketHistory.id > watermark)
                        .order_by(TicketHistory.id)
                        .limit(HISTORY_BATCH)
                    )
                ).all()
            ]
        for hid, _tid in gap_rows:
            gaps.pop(hid, None)
        if rows:
            batch_max = rows[-1][0]
            seen_at = wall_clock()
            for gid in _missing_ids(watermark, batch_max, {r[0] for r in rows}):
                gaps.setdefault(gid, seen_at)
            watermark = batch_max
        cutoff = wall_clock() - GAP_TTL_SECONDS
        for gid in [g for g, t in gaps.items() if t < cutoff]:
            del gaps[gid]
        if len(gaps) > MAX_GAPS:
            for gid in sorted(gaps, key=lambda g: (gaps[g], g))[: len(gaps) - MAX_GAPS]:
                del gaps[gid]
        touched = {tid for _hid, tid in (*gap_rows, *rows)}
        if touched:
            await rebuild_and_commit(factory, sorted(touched), now=now)
        await _save_progress(factory, watermark=watermark, gaps=gaps, now=now)
        rows_total += len(rows) + len(gap_rows)
        tickets_total += len(touched)
        if len(rows) < HISTORY_BATCH:
            break
    return rows_total, tickets_total, watermark, gaps


async def _orphan_sweep(factory: async_sessionmaker[AsyncSession]) -> int:
    removed = 0
    last = 0
    while True:
        async with factory() as session:
            ids = [
                int(i)
                for i in (
                    await session.execute(
                        select(TiqoraReportTicketFact.ticket_id)
                        .where(TiqoraReportTicketFact.ticket_id > last)
                        .order_by(TiqoraReportTicketFact.ticket_id)
                        .limit(ORPHAN_PAGE)
                    )
                )
                .scalars()
                .all()
            ]
            if not ids:
                return removed
            last = ids[-1]
            existing: set[int] = set()
            for i in range(0, len(ids), _IN_CHUNK):
                existing.update(
                    int(t)
                    for t in (
                        await session.execute(
                            select(Ticket.id).where(Ticket.id.in_(ids[i : i + _IN_CHUNK]))
                        )
                    )
                    .scalars()
                    .all()
                )
        orphans = [i for i in ids if i not in existing]
        if orphans:
            async with fact_write_lock(), factory() as session:
                await delete_ticket_facts(session, orphans)
                await session.commit()
            removed += len(orphans)


async def run_report_facts_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
    wall_clock: Callable[[], float] = time.time,
    registry: Registry | None = None,
) -> dict[str, int]:
    """One incremental pass; see the module docstring. ``now`` is naive UTC."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    stamp = now if now is not None else utc_now()
    result = dict.fromkeys(
        ("history_rows", "tickets", "gaps", "dirty", "upgraded", "orphans", "snapshot_days"), 0
    )

    # 1. watermark: older history belongs to the backfill
    async with factory() as session:
        state = await load_state(session, for_update=True, retry_on_deadlock=True)
        if state.last_history_id == 0:
            state.last_history_id = int(
                (await session.execute(select(func.max(TicketHistory.id)))).scalar_one() or 0
            )
            await session.commit()
        watermark = state.last_history_id
        gaps = _load_gaps(state.gap_ids)

    # 2. backfill job
    await _ensure_backfill(factory, now=stamp, registry=registry)

    # 3. history
    rows, tickets, watermark, gaps = await _follow_history(
        factory, watermark=watermark, gaps=gaps, now=stamp, wall_clock=wall_clock
    )
    result.update(history_rows=rows, tickets=tickets, gaps=len(gaps))

    # 4. dirty queue
    async with factory() as session:
        dirty_ids, taken_at = await take_dirty_ticket_ids(session, DIRTY_BATCH)
    if dirty_ids:
        await rebuild_and_commit(factory, dirty_ids, now=stamp)
        async with factory() as session:
            await clear_dirty(session, dirty_ids, taken_at)
            await session.commit()
        result["dirty"] = len(dirty_ids)
        result["tickets"] += len(dirty_ids)

    async with factory() as session:
        state = await load_state(session)
        done = state.backfill_status == "done"
        last_sweep = state.last_orphan_sweep_at
        last_snapshot = state.last_snapshot_date

    # 5. fact version upgrade
    if done:
        async with factory() as session:
            old = [
                int(i)
                for i in (
                    await session.execute(
                        select(TiqoraReportTicketFact.ticket_id)
                        .where(TiqoraReportTicketFact.fact_version < FACT_VERSION)
                        .order_by(TiqoraReportTicketFact.ticket_id)
                        .limit(VERSION_UPGRADE_BATCH)
                    )
                )
                .scalars()
                .all()
            ]
        if old:
            await rebuild_and_commit(factory, old, now=stamp)
            result["upgraded"] = len(old)

    # 6. orphan sweep
    if last_sweep is None or stamp - last_sweep >= timedelta(seconds=ORPHAN_SWEEP_EVERY_SECONDS):
        result["orphans"] = await _orphan_sweep(factory)
        async with factory() as session:
            state = await load_state(session, for_update=True)
            state.last_orphan_sweep_at = stamp
            await session.commit()

    # 7. snapshots
    if done:
        async with factory() as session:
            zone = await reporting_time_zone(session, await load_reporting_settings(session, cfg))
        yesterday = stamp.replace(tzinfo=UTC).astimezone(ZoneInfo(zone)).date() - timedelta(days=1)
        first = last_snapshot + timedelta(days=1) if last_snapshot is not None else yesterday
        days: list[date] = []
        day = first
        while day <= yesterday and len(days) < SNAPSHOT_MAX_DAYS_PER_TICK:
            days.append(day)
            day += timedelta(days=1)
        if days:
            await snapshot_days(factory, days, zone=zone, now=stamp)
            async with factory() as session:
                state = await load_state(session, for_update=True)
                state.last_snapshot_date = days[-1]
                state.snapshot_time_zone = zone
                await session.commit()
            result["snapshot_days"] = len(days)

    result["watermark"] = watermark
    return result


__all__ = ["run_report_facts_tick"]
