"""Inbox queries of the approval engine (A8): "Meine Freigaben".

*pending* = the actor's approver rows (and those of agents who delegated to the actor, where the
step allows delegation) in active steps of pending requests. *decided* = rows the actor voted
on, on their own or on behalf (``vote.decided_by``), plus their own forwarded rows. Ticket
numbers and titles come from a second query on ``ticket`` (IN list, never a JOIN with the
``tiqora_*`` tables: mixed collations on production MariaDB).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import delegation, rules
from tiqora.approvals.contract import Actor, StepSpec
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalStep,
    TiqoraApprovalVote,
)
from tiqora.approvals.resolve import canonical_principal
from tiqora.approvals.service import _excluded_keys, _in_rule
from tiqora.db.legacy.ticket import Ticket
from tiqora.jobs.service import utc_now

_CHUNK = 500
A, S, P, V = TiqoraApprovalApprover, TiqoraApprovalStep, TiqoraApproval, TiqoraApprovalVote


@dataclass(frozen=True, slots=True)
class InboxItem:
    approval_id: int
    approver_id: int
    ticket_id: int
    ticket_number: str | None
    ticket_title: str | None
    title: str
    subject_type: str
    step_name: str
    rule: str
    #: approvals the step's rule needs ("2 von 3"); None when it cannot be told
    required: int | None
    approved_count: int
    due_at: datetime | None
    overdue: bool
    #: user id of the delegator when the row is decided in their place
    on_behalf_of: int | None
    #: status of the request
    status: str
    decided_at: datetime | None
    #: the vote on this row ("approved" | "rejected"), None while it is open or was forwarded
    my_decision: str | None
    #: False for rows the actor cannot vote on any more (F8) and for the decided tab
    can_vote: bool = False
    #: "already_voted" (voted in this step) | "own_request" (delegated row, actor is requester)
    blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class InboxPage:
    items: list[InboxItem]
    total: int


@dataclass(frozen=True, slots=True)
class _Row:
    approver: TiqoraApprovalApprover
    step: TiqoraApprovalStep
    approval: TiqoraApproval
    vote: TiqoraApprovalVote | None


async def _actor_key(session: AsyncSession, actor: Actor) -> str | None:
    if actor.principal is None:
        return None
    return (await canonical_principal(session, actor.principal)).key


async def _pending_rows(
    session: AsyncSession, actor: Actor, *, approval_ids: Sequence[int] | None = None
) -> list[_Row]:
    """Pending rows of the actor in inbox order (deadline first, NULL last, then id).

    *approval_ids* limits the scan to those requests (detail and ticket views).
    """
    key = await _actor_key(session, actor)
    if key is None:
        return []
    user_id = actor.principal.user_id if actor.principal is not None else None
    delegators: set[int] = set()
    if user_id is not None:
        delegators = await delegation.active_delegators(session, user_id, at=utc_now())
        delegators.discard(user_id)
    mine: ColumnElement[bool] = A.principal == key
    if delegators:
        mine = or_(mine, A.user_id.in_(sorted(delegators)))
    scope: list[ColumnElement[bool]] = []
    if approval_ids is not None:
        scope.append(P.id.in_(sorted(set(approval_ids))))
    result = await session.execute(
        select(A, S, P)
        .join(S, S.id == A.step_id)
        .join(P, P.id == A.approval_id)
        .where(P.status == "pending", S.status == "active", A.status == "pending", mine, *scope)
        .order_by(S.due_at.is_(None), S.due_at, A.id)
        .execution_options(populate_existing=True)
    )
    rows: list[_Row] = []
    for approver, step, approval in result:
        if (
            approver.principal != key
            and not StepSpec.model_validate_json(step.spec).allow_delegation
        ):
            continue  # a step without delegation: only the approver decides
        rows.append(_Row(approver, step, approval, None))
    return rows


def _decided_filter(key: str) -> ColumnElement[bool]:
    return or_(
        and_(A.principal == key, A.status.in_(("approved", "rejected", "forwarded"))),
        V.decided_by == key,
    )


async def _decided_rows(
    session: AsyncSession, key: str, *, limit: int, offset: int
) -> tuple[list[_Row], int]:
    total = (
        await session.execute(
            select(func.count(A.id)).outerjoin(V, V.approver_id == A.id).where(_decided_filter(key))
        )
    ).scalar_one()
    result = await session.execute(
        select(A, S, P, V)
        .join(S, S.id == A.step_id)
        .join(P, P.id == A.approval_id)
        .outerjoin(V, V.approver_id == A.id)
        .where(_decided_filter(key))
        .order_by(A.id.desc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    return [_Row(a, s, p, v) for a, s, p, v in result], int(total)


async def _progress(session: AsyncSession, step_ids: Sequence[int]) -> dict[int, tuple[int, int]]:
    """``{step_id: (voters in the rule, approved)}`` over the eligible rows.

    Escalation-only approvers are outside the rule; a regular approver an escalation made
    decisive still counts (same notion as ``service._voters``).
    """
    voters: Counter[int] = Counter()
    approved: Counter[int] = Counter()
    ids = sorted(set(step_ids))
    for start in range(0, len(ids), _CHUNK):
        rows = (
            await session.execute(
                select(A)
                .where(A.step_id.in_(ids[start : start + _CHUNK]))
                .execution_options(populate_existing=True)
            )
        ).scalars()
        all_rows = list(rows)
        by_id = {a.id: a for a in all_rows}
        for row in all_rows:
            if row.status in ("pending", "approved", "rejected") and _in_rule(row, by_id):
                voters[row.step_id] += 1
                if row.status == "approved":
                    approved[row.step_id] += 1
    return {i: (voters[i], approved[i]) for i in ids}


async def _tickets(
    session: AsyncSession, ticket_ids: Sequence[int]
) -> dict[int, tuple[str, str | None]]:
    ids = sorted(set(ticket_ids))
    out: dict[int, tuple[str, str | None]] = {}
    for start in range(0, len(ids), _CHUNK):
        rows = await session.execute(
            select(Ticket.id, Ticket.tn, Ticket.title).where(
                Ticket.id.in_(ids[start : start + _CHUNK])
            )
        )
        for ticket_id, tn, title in rows:
            out[int(ticket_id)] = (str(tn), title)
    return out


async def _blocked(session: AsyncSession, key: str, rows: Sequence[_Row]) -> dict[int, str | None]:
    """``{approver_id: reason}`` for pending rows the actor cannot vote on (None = can vote)."""
    step_ids = sorted({r.step.id for r in rows})
    voted: set[int] = set()
    for start in range(0, len(step_ids), _CHUNK):
        found = await session.execute(
            select(V.step_id).where(
                V.step_id.in_(step_ids[start : start + _CHUNK]), V.decided_by == key
            )
        )
        voted.update(int(i) for i in found.scalars())
    excluded: dict[int, frozenset[str]] = {}
    out: dict[int, str | None] = {}
    for row in rows:
        if row.step.id in voted:
            out[row.approver.id] = "already_voted"
        elif row.approver.principal != key:  # delegated: requester/creator never vote
            if row.approval.id not in excluded:
                excluded[row.approval.id] = await _excluded_keys(session, row.approval)
            keys = excluded[row.approval.id]
            out[row.approver.id] = (
                "own_request" if key in keys or row.approver.principal in keys else None
            )
        else:
            out[row.approver.id] = None
    return out


async def _items(
    session: AsyncSession, key: str, rows: Sequence[_Row], *, pending: bool = False
) -> list[InboxItem]:
    blocked = await _blocked(session, key, rows) if pending else {}
    progress = await _progress(session, [r.step.id for r in rows])
    tickets = await _tickets(session, [r.approval.ticket_id for r in rows])
    now = utc_now()
    items: list[InboxItem] = []
    for row in rows:
        voters, approved = progress[row.step.id]
        tn, title = tickets.get(row.approval.ticket_id, (None, None))
        open_now = row.approval.status == "pending" and row.step.status == "active"
        items.append(
            InboxItem(
                approval_id=row.approval.id,
                approver_id=row.approver.id,
                ticket_id=row.approval.ticket_id,
                ticket_number=tn,
                ticket_title=title,
                title=row.approval.title,
                subject_type=row.approval.subject_type,
                step_name=row.step.name,
                rule=row.step.rule,
                required=rules.required_votes(row.step.rule, row.step.quorum, voters),  # type: ignore[arg-type]
                approved_count=approved,
                due_at=row.step.due_at,
                overdue=open_now
                and (
                    row.step.overdue_at is not None
                    or (row.step.due_at is not None and row.step.due_at < now)
                ),
                on_behalf_of=row.approver.user_id if row.approver.principal != key else None,
                status=row.approval.status,
                decided_at=row.vote.decided_at if row.vote is not None else None,
                my_decision=row.vote.decision if row.vote is not None else None,
                can_vote=pending and blocked[row.approver.id] is None,
                blocked_reason=blocked.get(row.approver.id),
            )
        )
    return items


async def inbox(
    session: AsyncSession,
    actor: Actor,
    *,
    state: Literal["pending", "decided"],
    limit: int,
    offset: int,
) -> InboxPage:
    """One page of the inbox: by deadline (NULL last) then id; the decided tab newest first."""
    key = await _actor_key(session, actor)
    if key is None:
        return InboxPage([], 0)
    if state == "pending":
        rows = await _pending_rows(session, actor)
        items = await _items(session, key, rows[offset : offset + limit], pending=True)
        return InboxPage(items, len(rows))
    page, total = await _decided_rows(session, key, limit=limit, offset=offset)
    return InboxPage(await _items(session, key, page), total)


async def pending_count(session: AsyncSession, actor: Actor) -> int:
    """Open rows the actor can still vote on (own and delegated) - the sidebar badge."""
    key = await _actor_key(session, actor)
    if key is None:
        return 0
    rows = await _pending_rows(session, actor)
    return sum(1 for reason in (await _blocked(session, key, rows)).values() if reason is None)
