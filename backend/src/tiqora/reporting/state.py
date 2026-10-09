"""The single ``tiqora_report_state`` row."""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraReportState
from tiqora.jobs.service import utc_now

STATE_NAME: Final = "facts"


async def load_state(session: AsyncSession, *, for_update: bool = False) -> TiqoraReportState:
    """Return the state row, creating it on first use.

    The insert runs in a SAVEPOINT so losing the first-start race rolls back only
    the insert, never the caller's pending work.
    """
    stmt = select(TiqoraReportState).where(TiqoraReportState.name == STATE_NAME)
    if for_update:
        stmt = stmt.with_for_update()
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
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one()
