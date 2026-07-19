"""Znuny ticket index accelerator maintenance.

Behavioural port of ``Kernel/System/Ticket/IndexAccelerator/StaticDB.pm``:

- ``TicketAcceleratorAdd``: INSERT into ``ticket_index`` (queue/lock/state
  names + group_id + create_time) — only when the ticket's state type is
  viewable (``Ticket::ViewableStateType``) and the ticket is not archived.
- ``TicketAcceleratorDelete``: DELETE from ``ticket_lock_index`` and
  ``ticket_index``.
- ``TicketAcceleratorUpdate``: compare lock/state/queue against the index row
  and re-sync (delete + add) when they differ or the row is missing; delete
  when the ticket is no longer viewable.

When ``Ticket::IndexModule`` is RuntimeDB (the default and recommendation for
parallel operation), every function is a no-op — RuntimeDB computes queue
views directly from the ticket table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_STATIC_DB = "Kernel::System::Ticket::IndexAccelerator::StaticDB"

# Default of SysConfig Ticket::ViewableStateType (Ticket.xml).
_DEFAULT_VIEWABLE_STATE_TYPES = ("new", "open", "pending reminder", "pending auto")


async def _is_static_db(sysconfig: SysConfig) -> bool:
    return await sysconfig.ticket_index_module() == _STATIC_DB


async def _viewable_state_types(sysconfig: SysConfig) -> list[str]:
    raw = await sysconfig.get("Ticket::ViewableStateType")
    if isinstance(raw, list) and raw:
        return [str(v) for v in raw]
    return list(_DEFAULT_VIEWABLE_STATE_TYPES)


async def _ticket_row(session: AsyncSession, ticket_id: int) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT t.queue_id, q.name, q.group_id, tlt.name, ts.name, tst.name,"
                " t.archive_flag, t.create_time"
                " FROM ticket t"
                " JOIN queue q ON q.id = t.queue_id"
                " JOIN ticket_lock_type tlt ON tlt.id = t.ticket_lock_id"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "queue_id": int(row[0]),
        "queue": str(row[1]),
        "group_id": int(row[2]),
        "lock": str(row[3]),
        "state": str(row[4]),
        "state_type": str(row[5]),
        "archive_flag": int(row[6]),
        "create_time": row[7],
    }


def _is_viewable(ticket: dict[str, Any], viewable_state_types: list[str]) -> bool:
    return ticket["state_type"] in viewable_state_types and ticket["archive_flag"] == 0


async def ticket_accelerator_add(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Insert the ticket into ``ticket_index`` (StaticDB only, viewable only)."""
    if not await _is_static_db(sysconfig):
        return

    ticket = await _ticket_row(session, ticket_id)
    if ticket is None:
        return
    if not _is_viewable(ticket, await _viewable_state_types(sysconfig)):
        return

    await session.execute(
        text(
            "INSERT INTO ticket_index"
            " (ticket_id, queue_id, queue, group_id, s_lock, s_state, create_time)"
            " VALUES (:tid, :qid, :q, :gid, :lck, :st, :ct)"
        ),
        {
            "tid": ticket_id,
            "qid": ticket["queue_id"],
            "q": ticket["queue"],
            "gid": ticket["group_id"],
            "lck": ticket["lock"],
            "st": ticket["state"],
            "ct": ticket["create_time"],
        },
    )


async def ticket_accelerator_delete(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Remove the ticket from ``ticket_lock_index`` and ``ticket_index``."""
    if not await _is_static_db(sysconfig):
        return

    await session.execute(
        text("DELETE FROM ticket_lock_index WHERE ticket_id = :tid"), {"tid": ticket_id}
    )
    await session.execute(
        text("DELETE FROM ticket_index WHERE ticket_id = :tid"), {"tid": ticket_id}
    )


async def ticket_accelerator_update(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Re-sync ``ticket_index`` after a ticket change (StaticDB only)."""
    if not await _is_static_db(sysconfig):
        return

    ticket = await _ticket_row(session, ticket_id)
    if ticket is None:
        return

    idx_row = (
        await session.execute(
            text("SELECT s_lock, s_state, queue_id FROM ticket_index WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).first()

    if not _is_viewable(ticket, await _viewable_state_types(sysconfig)):
        if idx_row is not None:
            await ticket_accelerator_delete(session, ticket_id, sysconfig)
        return

    needs_resync = (
        idx_row is None
        or str(idx_row[0]) != ticket["lock"]
        or str(idx_row[1]) != ticket["state"]
        or int(idx_row[2]) != ticket["queue_id"]
    )
    if needs_resync:
        await ticket_accelerator_delete(session, ticket_id, sysconfig)
        await ticket_accelerator_add(session, ticket_id, sysconfig)


__all__ = [
    "ticket_accelerator_add",
    "ticket_accelerator_delete",
    "ticket_accelerator_update",
]
