"""Visibility and permission rules of the approval engine (A8).

Only :func:`can_cancel` lives here so far; the viewing and requesting rules follow with the
decision and inbox tasks.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit
from tiqora.approvals.contract import Actor
from tiqora.approvals.models import TiqoraApproval
from tiqora.permissions.engine import PermissionEngine


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
    row = (
        await session.execute(
            text("SELECT queue_id FROM ticket WHERE id = :t"), {"t": approval.ticket_id}
        )
    ).first()
    return row is not None and await engine.check(user_id, int(row[0]), "rw")
