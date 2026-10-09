"""The single ``tiqora_report_state`` row and the status derived from it."""

from __future__ import annotations

import json
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.ticket import TicketHistory
from tiqora.db.tiqora.models import (
    TiqoraReportDirtyTicket,
    TiqoraReportState,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.jobs.service import utc_now
from tiqora.reporting.schemas import FactsStatusOut, ReportingStatusOut

STATE_NAME: Final = "facts"
_MARIADB_DEADLOCK: Final = 1213
_MARIADB_NO_SAVEPOINT: Final = 1305
_PG_DEADLOCK: Final = "40P01"


def _error_code(exc: DBAPIError) -> tuple[object, object]:
    """``(sqlstate, first arg)`` of the driver error behind *exc*."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    args = getattr(orig, "args", ())
    return state, args[0] if args else None


def is_deadlock(exc: DBAPIError) -> bool:
    """MariaDB/MySQL error 1213 or PostgreSQL SQLSTATE 40P01."""
    state, code = _error_code(exc)
    return state == _PG_DEADLOCK or code == _MARIADB_DEADLOCK


def _lost_savepoint(exc: DBAPIError) -> bool:
    """MariaDB 1305: the savepoint is gone because InnoDB aborted the whole transaction."""
    return _error_code(exc)[1] == _MARIADB_NO_SAVEPOINT


def _deadlock_behind(exc: DBAPIError) -> bool:
    """*exc* is a deadlock, or the failed ``ROLLBACK TO SAVEPOINT`` that followed one."""
    if is_deadlock(exc):
        return True
    cause = exc.__context__
    return _lost_savepoint(exc) and isinstance(cause, DBAPIError) and is_deadlock(cause)


async def load_state(
    session: AsyncSession, *, for_update: bool = False, retry_on_deadlock: bool = False
) -> TiqoraReportState:
    """Return the state row, creating it on first use.

    The insert runs in a SAVEPOINT so losing the first-start race rolls back only
    the insert, never the caller's pending work.

    Two first starts that both take a locking read of the missing row can deadlock on
    the insert (MariaDB gap locks, error 1213; PostgreSQL 40P01). InnoDB then aborts
    the victim's WHOLE transaction (``ROLLBACK TO SAVEPOINT`` fails with 1305), so a
    savepoint-only retry is impossible there and everything the caller did earlier in
    the transaction is gone. Therefore a deadlock always rolls the session back, and:

    * ``retry_on_deadlock=False`` (default): the deadlock is re-raised, so the caller's
      unit of work fails visibly instead of silently losing flushed writes;
    * ``retry_on_deadlock=True``: the whole select/insert runs once more, which then
      finds the winner's committed row; a second deadlock is raised. Only pass this
      when ``load_state`` is the FIRST statement of a fresh session (nothing to lose).
    """
    stmt = select(TiqoraReportState).where(TiqoraReportState.name == STATE_NAME)
    if for_update:
        stmt = stmt.with_for_update()
    attempt = 1
    while True:
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return row
        try:
            async with session.begin_nested():
                session.add(
                    TiqoraReportState(
                        name=STATE_NAME,
                        last_history_id=0,
                        backfill_status="pending",
                        updated_at=utc_now(),
                    )
                )
        except IntegrityError:  # another process created it concurrently
            # A locking read sees the rival's committed row even under MariaDB's
            # REPEATABLE READ snapshot, which a plain re-select would not.
            return (await session.execute(stmt.with_for_update())).scalar_one()
        except DBAPIError as exc:
            if not _deadlock_behind(exc):
                raise
            # InnoDB/PostgreSQL abort the whole transaction on a deadlock, whatever
            # error form reaches us (bare 1213/40P01 or the 1305 that follows): the
            # session must be rolled back before it is reused for a retry.
            await session.rollback()
            if not retry_on_deadlock or attempt >= 2:
                raise
            attempt += 1
            continue
        return (await session.execute(stmt)).scalar_one()


def _progress(state: TiqoraReportState) -> float | None:
    if state.backfill_status != "running":
        return None
    start = state.backfill_start_ticket_id
    cursor = state.backfill_cursor_ticket_id
    target = state.backfill_target_ticket_id
    if start is None or cursor is None or target is None or target <= start:
        return 0.0
    return min(1.0, max(0.0, (cursor - start) / (target - start)))


def _status_fields(state: TiqoraReportState) -> dict[str, Any]:
    status = state.backfill_status if state.backfill_status in ("running", "done") else "pending"
    return {
        "ready": status == "done",
        "as_of": state.last_tick_at or state.backfill_finished_at,
        "backfill_status": status,
        "backfill_job_id": state.backfill_job_id,
        "backfill_progress": _progress(state),
    }


def facts_status_of(state: TiqoraReportState) -> FactsStatusOut:
    """Readiness of the report data from an already loaded state row."""
    return FactsStatusOut.model_validate(_status_fields(state))


async def facts_status(session: AsyncSession) -> FactsStatusOut:
    """Readiness of the report data (creates the state row when missing)."""
    return facts_status_of(await load_state(session))


async def reporting_status(session: AsyncSession) -> ReportingStatusOut:
    """Admin status: :func:`facts_status` plus watermark, lag, queue and row counts."""
    state = await load_state(session)
    max_history = (await session.execute(select(func.max(TicketHistory.id)))).scalar_one()
    try:
        gaps = json.loads(state.gap_ids) if state.gap_ids else []
    except ValueError:
        gaps = []

    async def count(model: Any) -> int:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())

    return ReportingStatusOut.model_validate(
        {
            **_status_fields(state),
            "last_history_id": state.last_history_id,
            "history_lag": max(0, int(max_history or 0) - state.last_history_id),
            "gap_count": len(gaps) if isinstance(gaps, list) else 0,
            "dirty_count": await count(TiqoraReportDirtyTicket),
            "last_snapshot_date": state.last_snapshot_date,
            "snapshot_time_zone": state.snapshot_time_zone,
            "fact_rows": await count(TiqoraReportTicketFact),
            "event_rows": await count(TiqoraReportTicketEvent),
        }
    )
