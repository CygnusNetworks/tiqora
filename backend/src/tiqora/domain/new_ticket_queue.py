"""Which queue a new agent ticket starts in.

Order (first hit wins; every step only yields a valid queue the agent may
``create`` tickets in -- the same check ``POST /tickets`` enforces):

1. ``customer`` -- queue of the customer user's newest ticket;
2. ``company`` -- queue of the newest ticket of the customer's company;
3. ``default`` -- ``Ticket::Frontend::AgentTicketPhone###QueueDefault`` (phone)
   or ``...AgentTicketEmail###QueueDefault`` (e-mail), a queue name;
4. ``fallback`` -- the first queue (tree order) that is not an intake queue,
   else simply the first queue.

Intake queues (Znuny's stock ``Junk``, ``Raw``, ``Postmaster`` and their
sub-queues) are where the postmaster parks unsorted or spam mail: they never
count as customer history and are never offered as the default. A history
queue the agent may not create in is skipped (next rule), not replaced by an
older ticket's queue. The ``?queue_id=`` the UI may carry comes before all of
this and is the frontend's business.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

NewTicketScreen = Literal["phone", "email"]
QueueSource = Literal["customer", "company", "default", "fallback"]

INTAKE_QUEUE_NAMES: Final[frozenset[str]] = frozenset({"junk", "raw", "postmaster"})


class QueueSuggestion(BaseModel):
    """Queue a new ticket starts in and which rule picked it (both null: none)."""

    queue_id: int | None
    source: QueueSource | None


NO_QUEUE: Final = QueueSuggestion(queue_id=None, source=None)


def is_intake_queue(name: str) -> bool:
    """True for Junk / Raw / Postmaster (top-level name, any case) and their children."""
    return name.split("::", 1)[0].strip().lower() in INTAKE_QUEUE_NAMES


def _tree_key(name: str) -> tuple[str, ...]:
    # Same order as the queue tree the form flattens (parents before children).
    return tuple(name.split("::"))


async def _creatable_queues(session: AsyncSession, user_id: int) -> dict[int, str]:
    group_ids = await PermissionEngine(session).groups_for_permission(user_id, "create")
    if not group_ids:
        return {}
    rows = await session.execute(
        select(Queue.id, Queue.name).where(Queue.group_id.in_(group_ids), Queue.valid_id == 1)
    )
    return {int(qid): str(name) for qid, name in rows.all()}


async def _intake_queue_ids(session: AsyncSession) -> list[int]:
    rows = await session.execute(select(Queue.id, Queue.name))
    return [int(qid) for qid, name in rows.all() if is_intake_queue(str(name))]


async def _newest_ticket_queue(
    session: AsyncSession, column: Literal["customer_user_id", "customer_id"], value: str
) -> int | None:
    """Queue of the newest ticket where *column* = *value*, outside intake queues."""
    intake = await _intake_queue_ids(session)
    sql = f"SELECT t.queue_id FROM ticket t WHERE t.{column} = :v"
    params: dict[str, object] = {"v": value}
    if intake:
        sql += " AND t.queue_id NOT IN (" + ", ".join(str(i) for i in intake) + ")"
    sql += " ORDER BY t.id DESC LIMIT 1"
    row = (await session.execute(text(sql), params)).first()
    return int(row[0]) if row is not None else None


async def _company_of(session: AsyncSession, login: str) -> str | None:
    company = (
        await session.execute(select(CustomerUser.customer_id).where(CustomerUser.login == login))
    ).scalar_one_or_none()
    if company is None:
        # Customer from a non-DB backend: the company its tickets were filed under.
        row = (
            await session.execute(
                text(
                    "SELECT customer_id FROM ticket WHERE customer_user_id = :cu"
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"cu": login},
            )
        ).first()
        company = row[0] if row is not None else None
    company = (company or "").strip()
    return company or None


async def _default_or_fallback(
    session: AsyncSession, screen: NewTicketScreen, allowed: dict[int, str]
) -> QueueSuggestion:
    wanted = await SysConfig(session).new_ticket_queue_default(screen)
    if wanted is not None:
        for qid, name in allowed.items():
            if name == wanted and not is_intake_queue(name):
                return QueueSuggestion(queue_id=qid, source="default")

    ordered = sorted(allowed.items(), key=lambda item: _tree_key(item[1]))
    for qid, name in ordered:
        if not is_intake_queue(name):
            return QueueSuggestion(queue_id=qid, source="fallback")
    if ordered:
        return QueueSuggestion(queue_id=ordered[0][0], source="fallback")
    return NO_QUEUE


async def default_new_ticket_queue(
    session: AsyncSession, user_id: int, screen: NewTicketScreen
) -> QueueSuggestion:
    """Queue for a new ticket before (or without) a customer: rules 3-4."""
    allowed = await _creatable_queues(session, user_id)
    if not allowed:
        return NO_QUEUE
    return await _default_or_fallback(session, screen, allowed)


async def suggest_new_ticket_queue(
    session: AsyncSession, user_id: int, screen: NewTicketScreen, customer_user_login: str
) -> QueueSuggestion:
    """Queue for a new ticket of *customer_user_login*: rules 1-4."""
    allowed = await _creatable_queues(session, user_id)
    if not allowed:
        return NO_QUEUE
    login = customer_user_login.strip()
    if login:
        qid = await _newest_ticket_queue(session, "customer_user_id", login)
        if qid is not None and qid in allowed:
            return QueueSuggestion(queue_id=qid, source="customer")
        company = await _company_of(session, login)
        if company is not None:
            qid = await _newest_ticket_queue(session, "customer_id", company)
            if qid is not None and qid in allowed:
                return QueueSuggestion(queue_id=qid, source="company")
    return await _default_or_fallback(session, screen, allowed)


__all__ = [
    "INTAKE_QUEUE_NAMES",
    "NewTicketScreen",
    "QueueSource",
    "QueueSuggestion",
    "default_new_ticket_queue",
    "is_intake_queue",
    "suggest_new_ticket_queue",
]
