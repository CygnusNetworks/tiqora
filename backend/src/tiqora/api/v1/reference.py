"""Agent-accessible reference data for ticket-zoom action pickers.

These are read-only lookups (priorities, states, agents, customers, queues,
compose-context) that the agent UI needs to populate the ticket action
toolbar's dropdowns/dialogs and the new-ticket compose form. Unlike the admin
CRUD under ``/admin/*`` (which is AdminUser-gated), these are guarded only by
``CurrentUser`` — any logged-in agent may read them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.channels.email.outbound_reply import queue_outbound_meta
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketStateType
from tiqora.db.legacy.user import Users
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/reference", tags=["reference"])

# Znuny's "valid" list id — 1 == valid, everything else is invalid/temporary.
_VALID = 1


class PriorityRefOut(BaseModel):
    id: int
    name: str


class StateRefOut(BaseModel):
    id: int
    name: str
    type_name: str


class AgentRefOut(BaseModel):
    id: int
    login: str
    full_name: str


class CustomerRefOut(BaseModel):
    login: str
    email: str
    customer_id: str
    full_name: str


class QueueRefOut(BaseModel):
    id: int
    name: str


class ComposeContextOut(BaseModel):
    from_address: str
    signature: str = ""
    signature_is_html: bool = False
    rich_text: bool = True


@router.get("/priorities", response_model=list[PriorityRefOut])
async def list_priorities(user: CurrentUser, session: DbSession) -> list[PriorityRefOut]:
    _ = user
    rows = (
        await session.execute(
            select(TicketPriority)
            .where(TicketPriority.valid_id == _VALID)
            .order_by(TicketPriority.id)
        )
    ).scalars()
    return [PriorityRefOut(id=p.id, name=p.name) for p in rows]


@router.get("/states", response_model=list[StateRefOut])
async def list_states(user: CurrentUser, session: DbSession) -> list[StateRefOut]:
    _ = user
    rows = (
        await session.execute(
            select(TicketState.id, TicketState.name, TicketStateType.name)
            .join(TicketStateType, TicketState.type_id == TicketStateType.id)
            .where(TicketState.valid_id == _VALID)
            .order_by(TicketState.id)
        )
    ).all()
    return [StateRefOut(id=r[0], name=r[1], type_name=r[2]) for r in rows]


@router.get("/agents", response_model=list[AgentRefOut])
async def list_agents(user: CurrentUser, session: DbSession) -> list[AgentRefOut]:
    """Valid agents for owner/responsible pickers.

    Kept simple: returns all valid users. Finer per-queue owner scoping (only
    agents with ``owner`` permission on the ticket's queue group) can come later.
    """
    _ = user
    rows = (
        await session.execute(select(Users).where(Users.valid_id == _VALID).order_by(Users.login))
    ).scalars()
    return [
        AgentRefOut(
            id=u.id,
            login=u.login,
            full_name=f"{u.first_name} {u.last_name}".strip(),
        )
        for u in rows
    ]


@router.get("/customers", response_model=list[CustomerRefOut])
async def search_customers(
    user: CurrentUser,
    session: DbSession,
    q: str = Query("", description="Substring matched against login, email, or name"),
    limit: int = Query(20, ge=1, le=100),
) -> list[CustomerRefOut]:
    """Search valid customer users for the customer-assignment picker."""
    _ = user
    stmt = select(CustomerUser).where(CustomerUser.valid_id == _VALID)
    term = q.strip()
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            CustomerUser.login.ilike(like)
            | CustomerUser.email.ilike(like)
            | CustomerUser.first_name.ilike(like)
            | CustomerUser.last_name.ilike(like)
        )
    stmt = stmt.order_by(CustomerUser.login).limit(limit)
    rows = (await session.execute(stmt)).scalars()
    return [
        CustomerRefOut(
            login=c.login,
            email=c.email,
            customer_id=c.customer_id,
            full_name=f"{c.first_name} {c.last_name}".strip(),
        )
        for c in rows
    ]


@router.get("/queues", response_model=list[QueueRefOut])
async def list_reference_queues(
    user: CurrentUser,
    session: DbSession,
    movable: bool = Query(
        False,
        description=(
            "If true, only queues the agent has ``rw`` on (for the "
            "Verschieben / move picker). Otherwise queues with at least ``ro``."
        ),
    ),
) -> list[QueueRefOut]:
    """Valid queues the current agent may access, filtered by permission.

    ``movable=true`` requires ``rw`` (move into the queue). Default is ``ro``.
    Always restricted to ``valid_id = 1``.
    """
    perm = "rw" if movable else "ro"
    pe = PermissionEngine(session)
    group_ids = await pe.groups_for_permission(user.id, perm)
    if not group_ids:
        return []
    rows = (
        await session.execute(
            select(Queue)
            .where(Queue.group_id.in_(group_ids), Queue.valid_id == _VALID)
            .order_by(Queue.name)
        )
    ).scalars()
    return [QueueRefOut(id=q.id, name=q.name) for q in rows]


@router.get("/compose-context", response_model=ComposeContextOut)
async def compose_context(
    user: CurrentUser,
    session: DbSession,
    queue_id: int = Query(..., description="Queue to resolve From-address/signature for"),
) -> ComposeContextOut:
    """From-address, signature preview, and rich-text flag for a new-ticket compose form.

    Reuses ``queue_outbound_meta``, the same queue lookup the agent-reply send
    path (``deliver_agent_email_reply``) resolves From/signature from, so the
    preview matches what an actual reply on that queue would show. The
    signature is returned RAW (placeholders unexpanded): a not-yet-created
    ticket has no ticket/customer context to expand OTRS_TICKET_*/
    OTRS_CUSTOMER_DATA_* tags against, so this preview is only approximate —
    the real expansion happens in ``prepare_outgoing_agent_email`` at send time.
    """
    _ = user
    try:
        from_line, _queue_name, sig_text, sig_ct = await queue_outbound_meta(session, queue_id)
    except InvalidInput as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    rich_text = bool(await SysConfig(session).get("Frontend::RichText", 1))
    return ComposeContextOut(
        from_address=from_line,
        signature=sig_text or "",
        signature_is_html="html" in (sig_ct or "").lower(),
        rich_text=rich_text,
    )
