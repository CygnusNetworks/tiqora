"""Zoom action menu filtered by ``Action`` ACLs.

Znuny reference: ``AgentTicketZoom.pm`` builds the menu items, hands them to
``TicketObject->TicketAcl( ReturnType => 'Action', ReturnSubType => '-',
Data => { map { $_ => $_ } @Items } )`` and drops every item that
``TicketAclActionData`` no longer returns.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.ticket_acl import apply_ticket_acl

ZOOM_ACTIONS: tuple[str, ...] = (
    "AgentTicketCompose",
    "AgentTicketForward",
    "AgentTicketNote",
    "AgentTicketPhoneOutbound",
    "AgentTicketPhoneInbound",
    "AgentTicketClose",
    "AgentTicketPending",
    "AgentTicketPriority",
    "AgentTicketMove",
    "AgentTicketOwner",
    "AgentTicketResponsible",
    "AgentTicketCustomer",
    "AgentTicketLock",
    "AgentTicketWatcher",
    "AgentLinkObject",
    "AgentTicketMerge",
    "AgentTicketPrint",
    "AgentTicketFreeText",
    "AgentTicketProcess",
)


class TicketAclActionsOut(BaseModel):
    """Zoom menu entries the ticket's ACLs leave available (in ``ZOOM_ACTIONS`` order)."""

    allowed: list[str]


async def allowed_ticket_actions(
    session: AsyncSession, *, user_id: int, ticket_id: int
) -> list[str]:
    """``ZOOM_ACTIONS`` minus what the ACLs of *ticket_id* remove (one ACL pass, like Znuny)."""
    kept = await apply_ticket_acl(
        session,
        user_id=user_id,
        data={name: name for name in ZOOM_ACTIONS},
        return_type="Action",
        return_sub_type="-",
        ticket_id=ticket_id,
        action="AgentTicketZoom",
    )
    return [name for name in ZOOM_ACTIONS if name in kept]
