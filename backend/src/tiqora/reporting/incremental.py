"""Incremental upkeep of the report data (the ``report_facts`` worker loop).

Exactly ONE worker replica is expected: the write lock and the tick lock are
process-local, and two replicas would overwrite each other's gap list (every other
interval loop of the worker has the same assumption).

Changes that write no ``ticket_history`` row are not seen: direct SQL changes,
calendar / SLA / queue configuration (working minutes stay as built) and a change of
the reporting time zone. They need a backfill ("Berichtsdaten neu aufbauen"). Right
after an erasure a rebuild that read before the erasure committed may bring old
customer fields back until the next tick repairs it (the erasure's dirty mark wins).

One tick keeps facts, events and daily snapshots current after the backfill:
it follows ``ticket_history`` by id with a watermark and a list of "gaps" (ids
below the watermark that were not visible yet - an insert that commits later
than a higher id), rebuilds tickets from the dirty queue, upgrades facts of an
old version, sweeps facts of deleted tickets and writes the missing daily
snapshots. Every step commits on its own and is idempotent, so a crash between
steps only repeats work.
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.ticket import Ticket, TicketHistory
from tiqora.db.tiqora.models import TiqoraJob, TiqoraReportTicketFact
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import ACTIVE_STATUSES, enqueue_job, utc_now
from tiqora.reporting.backfill import REPORT_BACKFILL_JOB, ReportBackfillPayload
from tiqora.reporting.dirty import clear_dirty, mark_tickets_dirty, take_dirty_ticket_ids
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
#: back-off before a failed backfill job is queued again (an admin-cancelled one never is):
#: the wait doubles per failure of the last 24 h, from the base to the cap
BACKFILL_RETRY_BASE_SECONDS: Final = 300
BACKFILL_RETRY_MAX_SECONDS: Final = 3600
BACKFILL_FAILURE_WINDOW: Final = timedelta(hours=24)
_IN_CHUNK: Final = 1000

#: a ticket whose rebuild fails is retried after 60 s * 2^n (at most one hour)
TICKET_RETRY_BASE_SECONDS: Final = 60
TICKET_RETRY_MAX_SECONDS: Final = 3600

Gaps = dict[int, float]

_tick_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
#: ticket id -> (failures, wall-clock time before which it is not retried); per process
_failing: dict[int, tuple[int, float]] = {}


def _tick_lock() -> asyncio.Lock:
    """Serialises ticks inside one process (one lock per event loop)."""
    loop = asyncio.get_running_loop()
    lock = _tick_locks.get(loop)
    if lock is None:
        lock = _tick_locks[loop] = asyncio.Lock()
    return lock


def _load_gaps(raw: str | None) -> Gaps:
    try:
        data = json.loads(raw) if raw else []
        return {int(i): float(t) for i, t in data}
    except (ValueError, TypeError):
        logger.warning("report_facts_gap_list_unreadable", length=len(raw or ""))
        return {}


async def _id_step(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """``(auto_increment_increment, offset)`` of the MariaDB/MySQL connection, else ``(1, 1)``."""
    async with factory() as session:
        if session.get_bind().dialect.name not in ("mysql", "mariadb"):
            return 1, 1
        row = (
            await session.execute(
                text("SELECT @@auto_increment_increment, @@auto_increment_offset")
            )
        ).one()
    return max(int(row[0]), 1), int(row[1])


async def _rebuild_isolated(
    factory: async_sessionmaker[AsyncSession],
    ids: list[int],
    *,
    now: datetime,
    wall_clock: Callable[[], float],
) -> list[int]:
    """Rebuild; a ticket that keeps failing is isolated instead of blocking the batch.

    The whole batch is tried first; if that raises, every ticket is tried on its own.
    Returns the ids that failed (logged, with a per-ticket back-off recorded).
    """
    if not ids:
        return []
    try:
        await rebuild_and_commit(factory, ids, now=now)
    except Exception:  # noqa: BLE001 - fall back to one ticket at a time
        logger.warning("report_facts_batch_failed", tickets=len(ids), exc_info=True)
    else:
        for tid in ids:
            _failing.pop(tid, None)
        return []
    failed: list[int] = []
    for tid in ids:
        try:
            await rebuild_and_commit(factory, [tid], now=now)
        except Exception:  # noqa: BLE001
            count = _failing.get(tid, (0, 0.0))[0] + 1
            delay = min(TICKET_RETRY_BASE_SECONDS * 2 ** (count - 1), TICKET_RETRY_MAX_SECONDS)
            _failing[tid] = (count, wall_clock() + delay)
            logger.error("report_facts_ticket_failed", ticket_id=tid, failures=count, exc_info=True)
            failed.append(tid)
        else:
            _failing.pop(tid, None)
    return failed


def _backing_off(tid: int, wall_clock: Callable[[], float]) -> bool:
    entry = _failing.get(tid)
    return entry is not None and wall_clock() < entry[1]


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
        await session.commit()


def _backfill_retry_delay(failures: int) -> timedelta:
    seconds = BACKFILL_RETRY_BASE_SECONDS * 2 ** max(failures - 1, 0)
    return timedelta(seconds=min(seconds, BACKFILL_RETRY_MAX_SECONDS))


async def _ensure_backfill(
    factory: async_sessionmaker[AsyncSession], *, now: datetime, registry: Registry | None
) -> bool:
    """Queue (or re-queue) the backfill while the data is not complete; True when queued.

    A job that was reaped or failed leaves the state "running"/"pending"
    with a dead job id: the tick queues a new one, which resumes from the stored
    cursor. Repeated failures back off (see :func:`_backfill_retry_delay`). A
    "running" state without job id and with a recent state write is a CLI run and
    is left alone.
    """
    async with factory() as session:
        state = await load_state(session, for_update=True, retry_on_deadlock=True)
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
        if job is not None and job.status == "cancelled":
            return False  # stopped by an admin: only an explicit rebuild starts it again
        if job is not None and job.status == "failed":
            since = now - BACKFILL_FAILURE_WINDOW
            failures = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TiqoraJob)
                        .where(
                            TiqoraJob.kind == REPORT_BACKFILL_JOB.kind,
                            TiqoraJob.dedupe_key == BACKFILL_DEDUPE_KEY,
                            TiqoraJob.status == "failed",
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


def _missing_ids(
    old_wm: int, batch_max: int, returned: set[int], *, step: int = 1, offset: int = 1
) -> list[int]:
    """Expected ids in ``(old_wm, batch_max]`` not returned; at most MAX_GAPS, the highest.

    With ``auto_increment_increment > 1`` only ids of the connection's sequence
    (``id % step == offset % step``) are expected; the others are not gaps.
    """
    out: list[int] = []
    i = batch_max
    if step > 1:
        i -= (i - offset) % step
    while i > old_wm and len(out) < MAX_GAPS:
        if i not in returned:
            out.append(i)
        i -= step
    return out


async def _follow_history(
    factory: async_sessionmaker[AsyncSession],
    *,
    watermark: int,
    gaps: Gaps,
    now: datetime,
    wall_clock: Callable[[], float],
    step: int = 1,
    offset: int = 1,
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
            for gid in _missing_ids(
                watermark, batch_max, {r[0] for r in rows}, step=step, offset=offset
            ):
                gaps.setdefault(gid, seen_at)
            watermark = batch_max
        cutoff = wall_clock() - GAP_TTL_SECONDS
        expired = sorted(g for g, t in gaps.items() if t < cutoff)
        for gid in expired:
            del gaps[gid]
        if expired:
            logger.warning("report_facts_gaps_expired", count=len(expired), ids=expired[:50])
        if len(gaps) > MAX_GAPS:
            for gid in sorted(gaps, key=lambda g: (gaps[g], g))[: len(gaps) - MAX_GAPS]:
                del gaps[gid]
        touched = {tid for _hid, tid in (*gap_rows, *rows)}
        if touched:
            failed = await _rebuild_isolated(
                factory, sorted(touched), now=now, wall_clock=wall_clock
            )
            if failed:  # the watermark moves on; the dirty queue retries them with back-off
                async with factory() as session:
                    await mark_tickets_dirty(session, failed)
                    await session.commit()
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
    async with _tick_lock():
        return await _run_tick(settings, session_factory, now, wall_clock, registry)


async def _run_tick(
    settings: Settings | None,
    session_factory: async_sessionmaker[AsyncSession] | None,
    now: datetime | None,
    wall_clock: Callable[[], float],
    registry: Registry | None,
) -> dict[str, int]:
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
    step, offset = await _id_step(factory)
    rows, tickets, watermark, gaps = await _follow_history(
        factory,
        watermark=watermark,
        gaps=gaps,
        now=stamp,
        wall_clock=wall_clock,
        step=step,
        offset=offset,
    )
    result.update(history_rows=rows, tickets=tickets, gaps=len(gaps))

    # 4. dirty queue
    async with factory() as session:
        dirty_ids, taken_at = await take_dirty_ticket_ids(session, DIRTY_BATCH)
    due = [i for i in dirty_ids if not _backing_off(i, wall_clock)]
    if due:
        failed = set(await _rebuild_isolated(factory, due, now=stamp, wall_clock=wall_clock))
        done_ids = [i for i in due if i not in failed]
        async with factory() as session:
            if done_ids:
                await clear_dirty(session, done_ids, taken_at)
            if failed:  # a fresh queued_at moves them behind newer marks
                await mark_tickets_dirty(session, sorted(failed))
            await session.commit()
        result["dirty"] = len(done_ids)
        result["tickets"] += len(done_ids)

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
                        .where(
                            TiqoraReportTicketFact.fact_version < FACT_VERSION,
                            TiqoraReportTicketFact.ticket_id.not_in(
                                [i for i in _failing if _backing_off(i, wall_clock)] or [0]
                            ),
                        )
                        .order_by(TiqoraReportTicketFact.ticket_id)
                        .limit(VERSION_UPGRADE_BATCH)
                    )
                )
                .scalars()
                .all()
            ]
        if old:
            failed_up = await _rebuild_isolated(factory, old, now=stamp, wall_clock=wall_clock)
            result["upgraded"] = len(old) - len(failed_up)

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
        if last_snapshot is not None and first <= yesterday:
            # rows that became visible late (within the gap TTL) belong to the day before
            first = min(first, yesterday - timedelta(days=1))
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
