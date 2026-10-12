"""Scheduler loop and run retention for report schedules (plan D13).

Every tick looks at the active schedules whose ``next_run_at`` has passed, advances each
one to its next occurrence strictly after *now* (so downtime yields exactly ONE catch-up
run, A8), and queues one ``core.report_delivery`` job per schedule. The advance is a
compare-and-set on ``next_run_at``: concurrent ticks (or worker replicas) race on the
row and only the winner enqueues. The job's dedupe key is a second line of defence.

``next_run_at`` is naive UTC, always truncated to whole seconds (schedules store whole
minutes; ``now`` is truncated here), so the compare-and-set compares exact values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraReportRun, TiqoraReportRunFile, TiqoraReportSchedule
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.reporting.delivery import DeliveryAlreadyActive, create_run, enqueue_delivery
from tiqora.reporting.recurrence import next_occurrence
from tiqora.reporting.schedules import load_schedule

logger = structlog.get_logger(__name__)

TICK_LIMIT: Final = 50
_CHUNK: Final = 500
_PAUSED_LEN: Final = 500


async def _pause_broken(session: AsyncSession, schedule_id: int, due: datetime, why: str) -> bool:
    """Deactivate a schedule whose stored recurrence/zone cannot be evaluated."""
    result = await session.execute(
        update(TiqoraReportSchedule)
        .where(
            TiqoraReportSchedule.id == schedule_id,
            TiqoraReportSchedule.next_run_at == due,
            TiqoraReportSchedule.active.is_(True),
        )
        .values(
            active=False,
            next_run_at=None,
            paused_reason=f"Zeitplan ungültig: {why}"[:_PAUSED_LEN],
        )
    )
    await session.commit()
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


async def _process(
    session: AsyncSession, schedule_id: int, due: datetime, now: datetime
) -> str | None:
    """``"enqueued"``, ``"paused"`` or ``None`` (lost the race / nothing to do)."""
    schedule = await load_schedule(session, schedule_id)
    if schedule is None or not schedule.active or schedule.next_run_at != due:
        await session.rollback()
        return None
    if schedule.recurrence is None:
        why = schedule.recurrence_error or "Wiederholung nicht lesbar."
        return "paused" if await _pause_broken(session, schedule_id, due, why) else None
    try:
        nxt = next_occurrence(schedule.recurrence, schedule.time_zone, max(now, due))
    except (ValueError, KeyError, OSError) as exc:
        return "paused" if await _pause_broken(session, schedule_id, due, str(exc)) else None
    nxt = nxt.replace(microsecond=0)
    won = await session.execute(
        update(TiqoraReportSchedule)
        .where(
            TiqoraReportSchedule.id == schedule_id,
            TiqoraReportSchedule.next_run_at == due,
            TiqoraReportSchedule.active.is_(True),
        )
        .values(next_run_at=nxt)
    )
    if won.rowcount != 1:  # type: ignore[attr-defined]
        await session.rollback()
        return None
    run = await create_run(
        session, schedule, run_kind="schedule", owner_only=False, due_at=due, now=now
    )
    try:
        await enqueue_delivery(
            session, run, dedupe_key=f"report_schedule:{schedule_id}:{due:%Y%m%dT%H%M}"
        )
    except DeliveryAlreadyActive:
        await session.rollback()
        return None
    await session.commit()
    return "enqueued"


async def run_report_scheduler_tick(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    registry: Registry | None = None,
) -> dict[str, int]:
    """Queue the delivery of every due schedule (at most :data:`TICK_LIMIT` per tick)."""
    del settings, registry  # kept for the tick signature shared with the other loops
    stamp = (now or utc_now()).replace(microsecond=0)
    async with session_factory() as session:
        due_rows = (
            await session.execute(
                select(TiqoraReportSchedule.id, TiqoraReportSchedule.next_run_at)
                .where(
                    TiqoraReportSchedule.active.is_(True),
                    TiqoraReportSchedule.next_run_at <= stamp,
                )
                .order_by(TiqoraReportSchedule.next_run_at, TiqoraReportSchedule.id)
                .limit(TICK_LIMIT)
            )
        ).all()
        await session.rollback()
    out = {"due": len(due_rows), "enqueued": 0, "paused": 0, "errors": 0}
    for schedule_id, due in due_rows:
        if due is None:
            continue
        async with session_factory() as session:
            try:
                outcome = await _process(session, int(schedule_id), due, stamp)
            except Exception:
                await session.rollback()
                logger.exception("report_schedule_tick_failed", schedule_id=int(schedule_id))
                out["errors"] += 1
                continue
        if outcome is not None:
            out[outcome] += 1
    return out


async def purge_report_runs(session: AsyncSession, *, older_than: datetime) -> int:
    """Delete finished runs older than ``older_than`` (and stuck queued/running ones) with files.

    Not committed; returns the number of runs.
    """
    ids = [
        int(i)
        for i in (
            await session.execute(
                select(TiqoraReportRun.id).where(
                    (TiqoraReportRun.finished_at < older_than)
                    | (
                        TiqoraReportRun.finished_at.is_(None)
                        & (TiqoraReportRun.created_at < older_than)
                    )
                )
            )
        ).scalars()
    ]
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        await session.execute(
            delete(TiqoraReportRunFile).where(TiqoraReportRunFile.run_id.in_(chunk))
        )
        await session.execute(delete(TiqoraReportRun).where(TiqoraReportRun.id.in_(chunk)))
    return len(ids)
