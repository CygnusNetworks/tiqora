"""Ticket facts and the ``ro``/``rw`` rule shared by the ITSM modules.

One place for the rule every ticket-scoped ITSM read and write follows: without ``ro`` on the
ticket's queue the ticket does not exist for the caller (404, the same answer as for a missing
ticket); a write without ``rw`` is 403. Lists simply leave out what the caller cannot read.

Only Znuny tables are read here (``ticket`` and its type/state lookups), in IN-list chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.permissions.engine import PermissionEngine

CHUNK = 500

_FACTS_SQL = (
    "SELECT t.id, t.tn, t.title, t.queue_id, t.service_id, tt.name, ts.name, tst.name"
    " FROM ticket t"
    " LEFT JOIN ticket_type tt ON tt.id = t.type_id"
    " LEFT JOIN ticket_state ts ON ts.id = t.ticket_state_id"
    " LEFT JOIN ticket_state_type tst ON tst.id = ts.type_id"
)


@dataclass(frozen=True, slots=True)
class TicketFacts:
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    service_id: int | None
    type_name: str | None
    state: str
    state_type: str


class TicketAccessDenied(Exception):
    """``ticket_not_found`` (404: missing or unreadable) or ``forbidden`` (403: write without
    ``rw``). Callers translate it into their own error type."""

    def __init__(self, code: str, http_status: int) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _facts(row: Sequence[object]) -> TicketFacts:
    tid, tn, title, queue_id, service_id, type_name, state, state_type = row
    return TicketFacts(
        ticket_id=int(str(tid)),
        tn=str(tn),
        title=str(title or ""),
        queue_id=int(str(queue_id)),
        service_id=None if service_id is None else int(str(service_id)),
        type_name=None if type_name is None else str(type_name),
        state=str(state or ""),
        state_type=str(state_type or ""),
    )


async def require_ticket(
    session: AsyncSession,
    ticket_id: int,
    *,
    user_id: int,
    write: bool = False,
    engine: PermissionEngine | None = None,
) -> TicketFacts:
    """Facts of a ticket the caller may read (and with ``write`` also change).

    Raises :class:`TicketAccessDenied`: ``ticket_not_found``/404 for a missing or unreadable
    ticket, ``forbidden``/403 for a write without ``rw``.
    """
    row = (await session.execute(text(f"{_FACTS_SQL} WHERE t.id = :t"), {"t": ticket_id})).first()
    engine = engine or PermissionEngine(session)
    if row is None:
        raise TicketAccessDenied("ticket_not_found", 404)
    facts = _facts(tuple(row))
    if not await engine.check(user_id, facts.queue_id, "ro"):
        raise TicketAccessDenied("ticket_not_found", 404)
    if write and not await engine.check(user_id, facts.queue_id, "rw"):
        raise TicketAccessDenied("forbidden", 403)
    return facts


async def readable_tickets(
    session: AsyncSession,
    ticket_ids: Sequence[int],
    *,
    user_id: int,
    engine: PermissionEngine | None = None,
) -> dict[int, TicketFacts]:
    """``{id: facts}`` of the tickets among ``ticket_ids`` the caller holds ``ro`` on, in id
    order; missing and unreadable ids are absent. Chunks of :data:`CHUNK`, one permission check
    per queue."""
    ids = sorted(set(ticket_ids))
    if not ids:
        return {}
    stmt = text(f"{_FACTS_SQL} WHERE t.id IN :ids").bindparams(bindparam("ids", expanding=True))
    engine = engine or PermissionEngine(session)
    allowed: dict[int, bool] = {}
    found: dict[int, TicketFacts] = {}
    for start in range(0, len(ids), CHUNK):
        for row in (await session.execute(stmt, {"ids": ids[start : start + CHUNK]})).all():
            facts = _facts(tuple(row))
            if facts.queue_id not in allowed:
                allowed[facts.queue_id] = await engine.check(user_id, facts.queue_id, "ro")
            if allowed[facts.queue_id]:
                found[facts.ticket_id] = facts
    return {tid: found[tid] for tid in sorted(found)}
