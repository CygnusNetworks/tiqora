"""Contract kit of ``ChangeApprovalPort``: run against the fake (Task 4) and the engine (Task 17).

``run`` takes a port factory, a session and an approval definition the port can resolve (the fake
accepts any; the engine needs real approver ids). It does not commit.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import Actor, ApprovalDefinition, Principal
from tiqora.itsm.change.approval_port import APPROVAL_KEY, ChangeApprovalPort


async def run(
    make_port: Callable[[], ChangeApprovalPort],
    session: AsyncSession,
    *,
    ticket_id: int,
    definition: ApprovalDefinition,
    user_id: int,
) -> None:
    port = make_port()
    actor = Actor.agent(user_id)

    assert await port.snapshot(session, approval_id=2_000_000_000) is None

    requested = await port.request(
        session,
        ticket_id=ticket_id,
        round=1,
        title="Change 2026-1: Switch tauschen",
        description="Der Switch srv-sw-01.example.org wird ersetzt.",
        definition=definition,
        actor=actor,
        requester=Principal.agent(user_id),
        allow_self_approval=True,
    )
    assert requested.status == "pending"
    assert requested.ticket_id == ticket_id
    assert requested.round == 1
    assert requested.approval_key == APPROVAL_KEY
    assert requested.decided_at is None

    seen = await port.snapshot(session, approval_id=requested.approval_id)
    assert seen is not None
    assert (seen.approval_id, seen.round, seen.status) == (requested.approval_id, 1, "pending")

    await port.cancel(session, approval_id=requested.approval_id, actor=actor, reason="rework")
    cancelled = await port.snapshot(session, approval_id=requested.approval_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.decided_at is not None
    # idempotent: a second cancel neither raises nor changes the final status
    await port.cancel(session, approval_id=requested.approval_id, actor=actor, reason="again")
    again = await port.snapshot(session, approval_id=requested.approval_id)
    assert again is not None and again.status == "cancelled"

    auto = await port.auto_decision(
        session, ticket_id=ticket_id, round=2, title="Change 2026-1: Switch tauschen", actor=actor
    )
    assert auto.status == "approved"
    assert auto.reason == "auto"
    assert auto.round == 2
    assert auto.approval_id != requested.approval_id
    assert auto.decided_at is not None

    # a decided approval is not cancellable
    await port.cancel(session, approval_id=auto.approval_id, actor=actor, reason="late")
    still = await port.snapshot(session, approval_id=auto.approval_id)
    assert still is not None and still.status == "approved"
