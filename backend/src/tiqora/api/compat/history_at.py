"""Ticket queue at a point in time (``HistoryTicketGet`` Queue rule) for TimeAccountingGet."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NEW_TICKET_PERCENT = re.compile(r"^%%(.+?)%%(.+?)%%(.+?)%%(.+?)%%(.+?)$")
_NEW_TICKET_OLD = re.compile(r"Ticket=\[(.+?)\],.+?Q=(.+?);P=(.+?);S=(.+?)")
_MOVE_PERCENT = re.compile(r"^%%(.+?)%%(.+?)%%(.+?)%%(.+?)")
_MOVE_OLD = re.compile(r"^Ticket moved to Queue '(.+?)'")


_ROW_LIMIT = 3000


def _queue_from_rows(rows: Sequence[tuple[Any, Any]]) -> str | None:
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
    return queue or None


async def _current_queue(session: AsyncSession, ticket_id: int) -> str | None:
    current = (
        await session.execute(
            text("SELECT q.name FROM ticket t JOIN queue q ON q.id = t.queue_id WHERE t.id = :tid"),
            {"tid": ticket_id},
        )
    ).scalar_one_or_none()
    return str(current) if current is not None else None


async def queues_at(
    session: AsyncSession, ticket_id: int, moments: Sequence[datetime]
) -> dict[datetime, str | None]:
    """:func:`queue_at` for several moments of one ticket with ONE history read.

    The rows up to the latest moment are read once (ordered, at most 3000 like
    ``HistoryTicketGet``); the rows up to an earlier moment are a prefix of them, so every
    moment gets exactly what a single :func:`queue_at` call would answer.
    """
    if not moments:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT th.name, tht.name, th.create_time FROM ticket_history th"
                " JOIN ticket_history_type tht ON th.history_type_id = tht.id"
                " WHERE th.ticket_id = :tid AND th.create_time <= :at"
                f" ORDER BY th.create_time, th.id LIMIT {_ROW_LIMIT}"
            ),
            {"tid": ticket_id, "at": max(moments)},
        )
    ).all()
    out: dict[datetime, str | None] = {}
    current: str | None = None
    current_loaded = False
    for moment in moments:
        prefix = [(r[0], r[1]) for r in rows if r[2] <= moment]
        if not prefix:
            out[moment] = None
            continue
        queue = _queue_from_rows(prefix)
        if queue is None:
            if not current_loaded:
                current = await _current_queue(session, ticket_id)
                current_loaded = True
            queue = current
        out[moment] = queue
    return out


async def queue_at(session: AsyncSession, ticket_id: int, at: datetime) -> str | None:
    """Queue name of *ticket_id* as of *at*; ``None`` when no history row exists that early.

    Falls back to the ticket's current queue when the history carries no parsable queue.
    """
    return (await queues_at(session, ticket_id, [at]))[at]
