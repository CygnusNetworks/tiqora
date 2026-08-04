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
from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue, Service, ServiceSla, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketStateType, TicketType
from tiqora.db.legacy.user import Users
from tiqora.domain.customer_service import CustomerService
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/reference", tags=["reference"])

# Znuny's "valid" list id — 1 == valid, everything else is invalid/temporary.
_VALID = 1


class PriorityRefOut(BaseModel):
    id: int
    name: str


class TypeRefOut(BaseModel):
    id: int
    name: str


class ServiceRefOut(BaseModel):
    id: int
    name: str


class SlaRefOut(BaseModel):
    id: int
    name: str
    service_ids: list[int] = []


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


class CustomerCompanyRefOut(BaseModel):
    customer_id: str
    name: str


class CustomerContactRefOut(BaseModel):
    login: str
    email: str
    first_name: str
    last_name: str
    customer_id: str
    company_name: str | None = None


class CustomerSearchOut(BaseModel):
    companies: list[CustomerCompanyRefOut]
    contacts: list[CustomerContactRefOut]


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


@router.get("/types", response_model=list[TypeRefOut])
async def list_types(user: CurrentUser, session: DbSession) -> list[TypeRefOut]:
    """Valid ticket types for create/zoom pickers."""
    _ = user
    rows = (
        await session.execute(
            select(TicketType).where(TicketType.valid_id == _VALID).order_by(TicketType.name)
        )
    ).scalars()
    return [TypeRefOut(id=r.id, name=r.name) for r in rows]


@router.get("/services", response_model=list[ServiceRefOut])
async def list_services(user: CurrentUser, session: DbSession) -> list[ServiceRefOut]:
    """Valid services for create/zoom pickers."""
    _ = user
    rows = (
        await session.execute(
            select(Service).where(Service.valid_id == _VALID).order_by(Service.name)
        )
    ).scalars()
    return [ServiceRefOut(id=r.id, name=r.name) for r in rows]


@router.get("/slas", response_model=list[SlaRefOut])
async def list_slas(
    user: CurrentUser,
    session: DbSession,
    service_id: int | None = Query(default=None, ge=1),
) -> list[SlaRefOut]:
    """Valid SLAs; optionally filtered to those linked to ``service_id``."""
    _ = user
    # Map service -> SLA ids for client-side filtering convenience.
    link_rows = (await session.execute(select(ServiceSla.service_id, ServiceSla.sla_id))).all()
    sla_to_services: dict[int, list[int]] = {}
    for sid, sla_id in link_rows:
        sla_to_services.setdefault(int(sla_id), []).append(int(sid))

    stmt = select(Sla).where(Sla.valid_id == _VALID).order_by(Sla.name)
    if service_id is not None:
        linked = {int(sla_id) for sid, sla_id in link_rows if int(sid) == int(service_id)}
        if not linked:
            return []
        stmt = stmt.where(Sla.id.in_(linked))
    rows = (await session.execute(stmt)).scalars()
    return [
        SlaRefOut(id=r.id, name=r.name, service_ids=sla_to_services.get(r.id, [])) for r in rows
    ]


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


@router.get("/customer-search", response_model=CustomerSearchOut)
async def customer_search(
    user: CurrentUser,
    session: DbSession,
    q: str = Query(
        "", description="Substring matched against company name/id or contact login/email/name"
    ),
    limit: int = Query(10, ge=1, le=25),
) -> CustomerSearchOut:
    """Quick search across customer companies and contacts.

    Distinct from ``/customers`` (contact-only picker for ticket assignment):
    this also matches companies and resolves each returned contact's company
    name via a single follow-up query (no N+1). Below a 2-character query,
    returns empty results.
    """
    _ = user
    companies, contacts = await CustomerService(session).quick_search(q, limit)
    return CustomerSearchOut(
        companies=[
            CustomerCompanyRefOut(customer_id=c.customer_id, name=c.name) for c in companies
        ],
        contacts=[
            CustomerContactRefOut(
                login=c.login,
                email=c.email,
                first_name=c.first_name,
                last_name=c.last_name,
                customer_id=c.customer_id,
                company_name=c.company_name,
            )
            for c in contacts
        ],
    )


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
    preview matches what an actual reply on that queue would show. Agent tags
    (``<OTRS_First_Name>`` etc.) are expanded against the current agent, same
    as ``TicketService.get_reply_draft``. A not-yet-created ticket has no
    ticket/customer context, so ticket_id is omitted and OTRS_TICKET_*/
    OTRS_CUSTOMER_DATA_* tags resolve empty here — the real expansion happens
    in ``prepare_outgoing_agent_email`` at send time once the ticket exists.
    """
    try:
        from_line, queue_name, sig_text, sig_ct = await queue_outbound_meta(session, queue_id)
    except InvalidInput as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    sysconfig = SysConfig(session)
    rich_text = bool(await sysconfig.get("Frontend::RichText", 1))
    signature = sig_text or ""
    if signature.strip():
        signature = await expand_placeholders(
            session,
            sysconfig,
            signature,
            user_id=user.id,
            queue_name=queue_name or "",
        )
    return ComposeContextOut(
        from_address=from_line,
        signature=signature,
        signature_is_html="html" in (sig_ct or "").lower(),
        rich_text=rich_text,
    )


# ---------------------------------------------------------------------------
# ACL-filtered field options (new-ticket / form context — no ticket_id)
# ---------------------------------------------------------------------------

_FIELD_TO_SUBTYPE: dict[str, str] = {
    "state": "State",
    "queue": "Queue",
    "priority": "Priority",
    "type": "Type",
    "service": "Service",
    "sla": "SLA",
}


class TicketFieldOptionsOut(BaseModel):
    """ACL-filtered id→name maps (string keys for JSON).

    Only requested fields are populated; others are omitted (empty dict).
    """

    state: dict[str, str] = {}
    queue: dict[str, str] = {}
    priority: dict[str, str] = {}
    type: dict[str, str] = {}
    service: dict[str, str] = {}
    sla: dict[str, str] = {}


async def _base_field_maps(
    session: DbSession,
    user: CurrentUser,
    fields: set[str],
    *,
    queue_id: int | None = None,
) -> dict[str, dict[int, str]]:
    """Unfiltered valid id→name maps for the requested fields.

    Queues are still permission-gated (``ro``); ACL is additional filtering.
    """
    out: dict[str, dict[int, str]] = {}
    if "state" in fields:
        rows = (
            await session.execute(
                select(TicketState.id, TicketState.name)
                .where(TicketState.valid_id == _VALID)
                .order_by(TicketState.id)
            )
        ).all()
        out["state"] = {int(r[0]): r[1] for r in rows}
    if "priority" in fields:
        rows = (
            await session.execute(
                select(TicketPriority.id, TicketPriority.name)
                .where(TicketPriority.valid_id == _VALID)
                .order_by(TicketPriority.id)
            )
        ).all()
        out["priority"] = {int(r[0]): r[1] for r in rows}
    if "type" in fields:
        rows = (
            await session.execute(
                select(TicketType.id, TicketType.name)
                .where(TicketType.valid_id == _VALID)
                .order_by(TicketType.name)
            )
        ).all()
        out["type"] = {int(r[0]): r[1] for r in rows}
    if "service" in fields:
        rows = (
            await session.execute(
                select(Service.id, Service.name)
                .where(Service.valid_id == _VALID)
                .order_by(Service.name)
            )
        ).all()
        out["service"] = {int(r[0]): r[1] for r in rows}
    if "sla" in fields:
        rows = (
            await session.execute(
                select(Sla.id, Sla.name).where(Sla.valid_id == _VALID).order_by(Sla.name)
            )
        ).all()
        out["sla"] = {int(r[0]): r[1] for r in rows}
    if "queue" in fields:
        pe = PermissionEngine(session)
        group_ids = await pe.groups_for_permission(user.id, "ro")
        if not group_ids:
            out["queue"] = {}
        else:
            rows = (
                await session.execute(
                    select(Queue.id, Queue.name)
                    .where(Queue.group_id.in_(group_ids), Queue.valid_id == _VALID)
                    .order_by(Queue.name)
                )
            ).all()
            out["queue"] = {int(r[0]): r[1] for r in rows}
    _ = queue_id  # reserved for form-side Queue checks when callers pass it
    return out


async def collect_ticket_field_options(
    session: DbSession,
    user: CurrentUser,
    *,
    fields: set[str],
    ticket_id: int | None = None,
    action: str | None = None,
    queue_id: int | None = None,
) -> TicketFieldOptionsOut:
    """Load base maps then apply Ticket ACL for each requested field."""
    from tiqora.domain.ticket_acl import filter_id_name_map

    base = await _base_field_maps(session, user, fields, queue_id=queue_id)
    checks: dict | None = None
    if queue_id is not None:
        queue_row = await session.get(Queue, queue_id)
        checks = {
            "Ticket": {
                "QueueID": str(queue_id),
                **({"Queue": queue_row.name} if queue_row is not None else {}),
            }
        }

    result = TicketFieldOptionsOut()
    for field, mapping in base.items():
        subtype = _FIELD_TO_SUBTYPE[field]
        filtered = await filter_id_name_map(
            session,
            user_id=user.id,
            items=mapping,
            return_sub_type=subtype,
            ticket_id=ticket_id,
            action=action,
            checks=checks,
        )
        setattr(result, field, {str(k): v for k, v in filtered.items()})
    return result


@router.get("/ticket-field-options", response_model=TicketFieldOptionsOut)
async def ticket_field_options(
    user: CurrentUser,
    session: DbSession,
    fields: str = Query(
        "state,queue,priority,type,service,sla",
        description="Comma-separated field names to return",
    ),
    action: str | None = Query(
        "AgentTicketPhone",
        description="Frontend Action for ACL Properties (e.g. AgentTicketPhone)",
    ),
    queue_id: int | None = Query(default=None, ge=1, description="Optional form QueueID context"),
) -> TicketFieldOptionsOut:
    """ACL-filtered field options for new-ticket forms (no ticket_id).

    Group/role queue permissions still apply to the queue list; Ticket ACL
    further restricts selectable values for the current agent and action.
    """
    requested = {f.strip().lower() for f in fields.split(",") if f.strip()}
    requested &= set(_FIELD_TO_SUBTYPE)
    if not requested:
        return TicketFieldOptionsOut()
    return await collect_ticket_field_options(
        session,
        user,
        fields=requested,
        action=action,
        queue_id=queue_id,
    )
