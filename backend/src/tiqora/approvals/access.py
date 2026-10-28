"""Visibility and permission rules of the approval engine (A8).

Who may see a request (:func:`can_view`), cancel it, refresh its approvers, request one on a
ticket, and who gets a link to the ticket. All checks work on principal keys; the system actor
is trusted (sweeper, consumer automation), customers only ever see requests they approve.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit, delegation
from tiqora.approvals.contract import Actor
from tiqora.approvals.models import TiqoraApproval, TiqoraApprovalApprover
from tiqora.approvals.resolve import canonical_principal
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine


async def _queue_id(session: AsyncSession, ticket_id: int) -> int | None:
    row = (
        await session.execute(text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": ticket_id})
    ).first()
    return None if row is None else int(row[0])


async def _agent_may(session: AsyncSession, actor: Actor, ticket_id: int, permission: str) -> bool:
    """Agent with *permission* on the ticket's queue, or an admin; nobody else."""
    principal = actor.principal
    if principal is None or principal.kind != "agent" or principal.user_id is None:
        return False
    queue_id = await _queue_id(session, ticket_id)
    if queue_id is None:
        return False
    engine = PermissionEngine(session)
    return await engine.is_admin(principal.user_id) or await engine.check(
        principal.user_id, queue_id, permission
    )


async def can_cancel(session: AsyncSession, approval: TiqoraApproval, actor: Actor) -> bool:
    """Requester (agent), creator, an agent with ``rw`` on the ticket's queue, or an admin.

    The system actor (sweeper, consumer automation) may always cancel; customers never.
    """
    principal = actor.principal
    if principal is None:
        return True
    if principal.kind != "agent" or principal.user_id is None:
        return False
    user_id = principal.user_id
    if actor.key == approval.requester or actor.key == await audit.creator_key(session, approval):
        return True
    engine = PermissionEngine(session)
    if await engine.is_admin(user_id):
        return True
    queue_id = await _queue_id(session, approval.ticket_id)
    return queue_id is not None and await engine.check(user_id, queue_id, "rw")


async def can_refresh(session: AsyncSession, approval: TiqoraApproval, actor: Actor) -> bool:
    """ "Freigeber neu ermitteln": an agent with ``rw`` on the ticket, an admin, or the system."""
    if actor.principal is None:
        return True
    return await _agent_may(session, actor, approval.ticket_id, "rw")


async def can_view(session: AsyncSession, approval: TiqoraApproval, actor: Actor) -> bool:
    """Admin, ``ro`` on the ticket's queue, the requester or creator (who may cancel), anyone
    with an approver row on the request (any status), an active delegate of such an agent.
    Customers: own rows only.
    """
    principal = actor.principal
    if principal is None:
        return True
    key = (await canonical_principal(session, principal)).key
    if principal.kind == "agent" and (
        key == approval.requester
        or key == await audit.creator_key(session, approval)
        or await _agent_may(session, actor, approval.ticket_id, "ro")
    ):
        return True
    rows = await session.execute(
        select(TiqoraApprovalApprover.principal, TiqoraApprovalApprover.user_id).where(
            TiqoraApprovalApprover.approval_id == approval.id
        )
    )
    approvers = [(str(p), u) for p, u in rows]
    if any(p == key for p, _ in approvers):
        return True
    if principal.kind != "agent" or principal.user_id is None:
        return False
    delegators = await delegation.active_delegators(session, principal.user_id, at=utc_now())
    return any(u is not None and u in delegators for _, u in approvers)


async def can_request(session: AsyncSession, ticket_id: int, actor: Actor) -> bool:
    """Ad-hoc request on a ticket: ``rw`` on its queue (or admin)."""
    return await _agent_may(session, actor, ticket_id, "rw")


async def ticket_link_allowed(session: AsyncSession, ticket_id: int, actor: Actor) -> bool:
    """Show a link to the ticket only to agents who may read it (``ro``); approvers without
    access see the request but get no link.
    """
    return await _agent_may(session, actor, ticket_id, "ro")
