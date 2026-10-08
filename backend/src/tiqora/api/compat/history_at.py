"""Ticket queue at a point in time (``HistoryTicketGet`` Queue rule) for TimeAccountingGet."""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NEW_TICKET_PERCENT = re.compile(r"^%%(.+?)%%(.+?)%%(.+?)%%(.+?)%%(.+?)$")
_NEW_TICKET_OLD = re.compile(r"Ticket=\[(.+?)\],.+?Q=(.+?);P=(.+?);S=(.+?)")
_MOVE_PERCENT = re.compile(r"^%%(.+?)%%(.+?)%%(.+?)%%(.+?)")
_MOVE_OLD = re.compile(r"^Ticket moved to Queue '(.+?)'")


async def queue_at(session: AsyncSession, ticket_id: int, at: datetime) -> str | None:
    """Queue name of *ticket_id* as of *at*; ``None`` when no history row exists that early.

    Falls back to the ticket's current queue when the history carries no parsable queue.
    """
    rows = (
        await session.execute(
            text(
                "SELECT th.name, tht.name FROM ticket_history th"
                " JOIN ticket_history_type tht ON th.history_type_id = tht.id"
                " WHERE th.ticket_id = :tid AND th.create_time <= :at"
                " ORDER BY th.create_time, th.id LIMIT 3000"
            ),
            {"tid": ticket_id, "at": at},
        )
    ).all()
    if not rows:
        return None
    queue: str | None = None
    for name, type_name in rows:
        name = name or ""
        if type_name == "NewTicket":
            m = _NEW_TICKET_PERCENT.search(name)
            if m is not None:
                queue = m.group(2)
            else:
                m2 = _NEW_TICKET_OLD.search(name)
                if m2 is not None:
                    queue = m2.group(2)
        elif type_name == "Move":
            m = _MOVE_PERCENT.search(name) or _MOVE_OLD.search(name)
            if m is not None:
                queue = m.group(1)
    if queue:
        return queue
    current = (
        await session.execute(
            text("SELECT q.name FROM ticket t JOIN queue q ON q.id = t.queue_id WHERE t.id = :tid"),
            {"tid": ticket_id},
        )
    ).scalar_one_or_none()
    return str(current) if current is not None else None
