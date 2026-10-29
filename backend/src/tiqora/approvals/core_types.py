"""Core approval subject type (``ticket``) and the approver resolvers every installation has."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import (
    ApprovalSubjectType,
    ApproverResolver,
    Principal,
    ResolveContext,
)

# Znuny's root@localhost: owner/responsible fallback, never a person who can approve.
_SYSTEM_USER_ID = 1


def ticket_subject_type() -> ApprovalSubjectType:
    return ApprovalSubjectType(
        key="ticket",
        title_key="approvals.subject.ticket",
        allow_customer_approvers=True,
        agent_requestable=True,
    )


async def _ticket_row(session: AsyncSession, ticket_id: int) -> tuple[str | None, int, int] | None:
    row = (
        await session.execute(
            text("SELECT customer_user_id, user_id, responsible_user_id FROM ticket WHERE id = :t"),
            {"t": ticket_id},
        )
    ).first()
    if row is None:
        return None
    return row[0], int(row[1] or 0), int(row[2] or 0)


async def _resolve_customer_user(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
    row = await _ticket_row(session, ctx.ticket_id)
    if row is None or not row[0]:
        return []
    return [Principal.customer(row[0])]


async def _resolve_owner(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
    row = await _ticket_row(session, ctx.ticket_id)
    if row is None or row[1] <= _SYSTEM_USER_ID:
        return []
    return [Principal.agent(row[1])]


async def _resolve_responsible(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
    row = await _ticket_row(session, ctx.ticket_id)
    if row is None or row[2] <= _SYSTEM_USER_ID:
        return []
    return [Principal.agent(row[2])]


def core_resolvers() -> list[ApproverResolver]:
    return [
        ApproverResolver(
            key="ticket.customer_user",
            title_key="approvals.resolver.ticket_customer_user",
            resolve=_resolve_customer_user,
            yields_customers=True,
        ),
        ApproverResolver(
            key="ticket.owner",
            title_key="approvals.resolver.ticket_owner",
            resolve=_resolve_owner,
        ),
        ApproverResolver(
            key="ticket.responsible",
            title_key="approvals.resolver.ticket_responsible",
            resolve=_resolve_responsible,
        ),
    ]
