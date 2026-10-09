"""Dirty queue: tickets whose facts must be rebuilt without a new history row.

Used by the GDPR erasure hook and the version-upgrade sweep. A mark made while a
rebuild is running must survive that rebuild, so the consumer takes ids together
with a snapshot time and clears only marks that are not newer than it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraReportDirtyTicket
from tiqora.jobs.service import utc_now

_IN_CHUNK: Final = 1000


async def mark_tickets_dirty(
    session: AsyncSession, ticket_ids: Iterable[int], *, now: datetime | None = None
) -> None:
    """Queue tickets; flush only.

    Delete-then-insert so a re-marked ticket gets a fresh ``queued_at`` (a plain
    "insert if missing" would keep the old time and a concurrent clear could then
    swallow the new mark) and the call never raises on an existing row.
    """
    ids = sorted({int(i) for i in ticket_ids})
    if not ids:
        return
    queued_at = now if now is not None else utc_now()
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        await session.execute(
            delete(TiqoraReportDirtyTicket).where(TiqoraReportDirtyTicket.ticket_id.in_(chunk))
        )
        await session.execute(
            insert(TiqoraReportDirtyTicket),
            [{"ticket_id": i, "queued_at": queued_at} for i in chunk],
        )
    await session.flush()


async def take_dirty_ticket_ids(session: AsyncSession, limit: int) -> tuple[list[int], datetime]:
    """Oldest marks first (``queued_at``, ``ticket_id``); does not delete.

    Returns the ids and the snapshot time to hand to :func:`clear_dirty`: the newest
    ``queued_at`` among the returned rows, so it has the stored precision (``DATETIME(6)`` on
    MariaDB, set in the model and migration). With no rows the snapshot is the current time.
    """
    rows = (
        await session.execute(
            select(TiqoraReportDirtyTicket.ticket_id, TiqoraReportDirtyTicket.queued_at)
            .order_by(TiqoraReportDirtyTicket.queued_at, TiqoraReportDirtyTicket.ticket_id)
            .limit(limit)
        )
    ).all()
    if not rows:
        return [], utc_now()
    return [int(r[0]) for r in rows], max(r[1] for r in rows)


async def clear_dirty(session: AsyncSession, ticket_ids: Sequence[int], taken_at: datetime) -> None:
    """Remove the marks that were taken; flush only.

    Only rows with ``queued_at <= taken_at`` go: a ticket marked again while its
    rebuild ran (for example by a GDPR erasure) keeps its newer mark.
    """
    ids = sorted({int(i) for i in ticket_ids})
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        await session.execute(
            delete(TiqoraReportDirtyTicket).where(
                TiqoraReportDirtyTicket.ticket_id.in_(chunk),
                TiqoraReportDirtyTicket.queued_at <= taken_at,
            )
        )
    await session.flush()


__all__ = ["clear_dirty", "mark_tickets_dirty", "take_dirty_ticket_ids"]
