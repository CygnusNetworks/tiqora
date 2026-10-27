"""Read models of the agent API (A13): request detail, ticket summaries, display labels.

All reads: nothing is locked or written. Names come from ``users`` / ``customer_user`` in a
second query with an IN list (never a JOIN with the ``tiqora_*`` tables: mixed collations on
production MariaDB). Visibility is decided by the caller (:func:`access.can_view`); this module
only shapes what the caller may see.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import access, inbox, notify, rules
from tiqora.approvals.contract import Actor, Principal, StepSpec
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalVote,
)
from tiqora.approvals.schemas import (
    ApprovalDetailOut,
    ApprovalEventOut,
    ApprovalStepOut,
    ApprovalSummaryOut,
    ApproverOut,
    MyApproverRowOut,
    VoteOut,
)
from tiqora.approvals.service import _in_rule
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.legacy.user import Users
from tiqora.jobs.service import utc_now

_CHUNK = 500
SYSTEM_LABEL = "System"


def _chunks[T](items: Sequence[T]) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _name(first: str | None, last: str | None) -> str:
    return " ".join(p for p in ((first or "").strip(), (last or "").strip()) if p)


async def user_labels(session: AsyncSession, user_ids: Iterable[int]) -> dict[int, str]:
    """``{user_id: "Vorname Nachname"}`` (the login when the name is empty)."""
    ids = sorted(set(user_ids))
    out: dict[int, str] = {}
    for chunk in _chunks(ids):
        rows = await session.execute(
            select(Users.id, Users.first_name, Users.last_name, Users.login).where(
                Users.id.in_(list(chunk))
            )
        )
        for uid, first, last, login in rows:
            out[int(uid)] = _name(first, last) or str(login)
    return out


async def principal_labels(session: AsyncSession, keys: Iterable[str]) -> dict[str, str]:
    """``{principal key: label}``; agents "Vorname Nachname", customers "Vorname Nachname
    (Kunde)", the system "System". Keys that cannot be resolved fall back to a neutral label.
    """
    wanted = sorted(set(keys))
    agent_ids: dict[int, str] = {}
    customer_logins: dict[str, str] = {}
    out: dict[str, str] = {}
    for key in wanted:
        if key == "system":
            out[key] = SYSTEM_LABEL
            continue
        try:
            principal = Principal.parse(key)
        except ValueError:
            out[key] = key
            continue
        if principal.kind == "agent" and principal.user_id is not None:
            agent_ids[principal.user_id] = key
        elif principal.login is not None:
            customer_logins[principal.login] = key
    for uid, label in (await user_labels(session, agent_ids)).items():
        out[agent_ids[uid]] = label
    logins = sorted(customer_logins)
    for chunk in _chunks(logins):
        rows = await session.execute(
            select(CustomerUser.login, CustomerUser.first_name, CustomerUser.last_name).where(
                CustomerUser.login.in_(list(chunk))
            )
        )
        for login, first, last in rows:
            label = _name(first, last) or str(login)
            out[customer_logins[str(login)]] = f"{label} (Kunde)"
    for key in (*agent_ids.values(), *customer_logins.values()):
        out.setdefault(key, "?" if key.startswith("agent:") else f"{key.partition(':')[2]} (Kunde)")
    return out


def _detail_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _load_ticket(session: AsyncSession, ticket_id: int) -> tuple[str | None, str | None]:
    row = (
        await session.execute(select(Ticket.tn, Ticket.title).where(Ticket.id == ticket_id))
    ).first()
    return (None, None) if row is None else (str(row[0]), row[1])


def _step_overdue(step: TiqoraApprovalStep, now: datetime) -> bool:
    return step.status == "active" and (
        step.overdue_at is not None or (step.due_at is not None and step.due_at < now)
    )


async def _my_rows(
    session: AsyncSession, approval: TiqoraApproval, actor: Actor
) -> list[MyApproverRowOut]:
    """Open rows of this request the caller can vote on right now (own and delegated)."""
    if actor.principal is None or actor.principal.kind != "agent":
        return []
    key = await inbox._actor_key(session, actor)
    if key is None:
        return []
    rows = await inbox._pending_rows(session, actor, approval_ids=[approval.id])
    if not rows:
        return []
    blocked = await inbox._blocked(session, key, rows)
    mine = [r for r in rows if blocked[r.approver.id] is None]
    labels = await user_labels(session, [r.approver.user_id for r in mine if r.approver.user_id])
    out: list[MyApproverRowOut] = []
    for row in mine:
        own = row.approver.principal == key
        out.append(
            MyApproverRowOut(
                approver_id=row.approver.id,
                step_id=row.step.id,
                on_behalf_of_label=(
                    None
                    if own or row.approver.user_id is None
                    else labels.get(row.approver.user_id, "?")
                ),
                can_forward=own
                and row.approver.source_kind != "forward"
                and StepSpec.model_validate_json(row.step.spec).allow_delegation,
            )
        )
    return out


async def build_detail(
    session: AsyncSession, approval: TiqoraApproval, actor: Actor
) -> ApprovalDetailOut:
    """Everything the request panel shows, for a caller who may view the request."""
    steps = list(
        (
            await session.execute(
                select(TiqoraApprovalStep)
                .where(TiqoraApprovalStep.approval_id == approval.id)
                .order_by(TiqoraApprovalStep.position)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    approvers = list(
        (
            await session.execute(
                select(TiqoraApprovalApprover)
                .where(TiqoraApprovalApprover.approval_id == approval.id)
                .order_by(TiqoraApprovalApprover.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    votes = {
        v.approver_id: v
        for v in (
            await session.execute(
                select(TiqoraApprovalVote)
                .where(TiqoraApprovalVote.approval_id == approval.id)
                .order_by(TiqoraApprovalVote.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    events = list(
        (
            await session.execute(
                select(TiqoraApprovalEvent)
                .where(TiqoraApprovalEvent.approval_id == approval.id)
                .order_by(TiqoraApprovalEvent.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    keys = {approval.requester, *(a.principal for a in approvers)}
    keys.update(v.decided_by for v in votes.values())
    keys.update(e.actor for e in events)
    labels = await principal_labels(session, keys)
    by_id = {a.id: a for a in approvers}
    by_step: dict[int, list[TiqoraApprovalApprover]] = defaultdict(list)
    for approver in approvers:
        by_step[approver.step_id].append(approver)
    now = utc_now()

    step_out: list[ApprovalStepOut] = []
    for step in steps:
        rows = by_step[step.id]
        in_rule = [a for a in rows if a.status in ("pending", "approved", "rejected")]
        voters = [a for a in in_rule if _in_rule(a, by_id)]
        step_out.append(
            ApprovalStepOut(
                id=step.id,
                position=step.position,
                stage=step.stage,
                name=step.name,
                rule=step.rule,
                quorum=step.quorum,
                required=rules.required_votes(
                    step.rule,  # type: ignore[arg-type]
                    step.quorum,
                    len(voters),
                ),
                approved_count=sum(1 for a in voters if a.status == "approved"),
                status=step.status,
                reason=step.reason,
                due_at=step.due_at,
                overdue=_step_overdue(step, now),
                approvers=[
                    ApproverOut(
                        id=a.id,
                        kind=Principal.parse(a.principal).kind,
                        display_name=labels[a.principal],
                        source_kind=a.source_kind,
                        source_label=a.source_label,
                        decisive=a.decisive,
                        status=a.status,
                        vote=(
                            None
                            if a.id not in votes
                            else VoteOut(
                                decision=votes[a.id].decision,
                                comment=votes[a.id].comment,
                                decided_by_label=labels[votes[a.id].decided_by],
                                on_behalf=votes[a.id].on_behalf,
                                channel=votes[a.id].channel,
                                decided_at=votes[a.id].decided_at,
                            )
                        ),
                    )
                    for a in rows
                ],
            )
        )

    tn, title = await _load_ticket(session, approval.ticket_id)
    pending = approval.status == "pending"
    return ApprovalDetailOut(
        id=approval.id,
        ticket_id=approval.ticket_id,
        ticket_number=tn,
        ticket_title=title,
        ticket_link=await access.ticket_link_allowed(session, approval.ticket_id, actor),
        subject_type=approval.subject_type,
        subject_ref=approval.subject_ref,
        approval_key=approval.approval_key,
        title=approval.title,
        description=approval.description,
        describe_lines=await notify._describe_lines(session, approval),
        status=approval.status,
        reason=approval.reason,
        current_stage=approval.current_stage,
        requester_label=labels[approval.requester],
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        steps=step_out,
        events=[
            ApprovalEventOut(
                at=e.at,
                action=e.action,
                actor_label=labels[e.actor],
                channel=e.channel,
                detail=_detail_json(e.detail),
            )
            for e in events
        ],
        my_rows=await _my_rows(session, approval, actor) if pending else [],
        can_cancel=pending and await access.can_cancel(session, approval, actor),
        can_refresh=pending and await access.can_refresh(session, approval, actor),
    )


async def build_summaries(
    session: AsyncSession, approvals: Sequence[TiqoraApproval], actor: Actor
) -> list[ApprovalSummaryOut]:
    """One summary per request (newest first as given)."""
    labels = await principal_labels(session, [a.requester for a in approvals])
    mine: set[int] = set()
    if actor.principal is not None and actor.principal.kind == "agent":
        key = await inbox._actor_key(session, actor)
        rows = await inbox._pending_rows(session, actor, approval_ids=[a.id for a in approvals])
        if key is not None and rows:
            blocked = await inbox._blocked(session, key, rows)
            mine = {r.approval.id for r in rows if blocked[r.approver.id] is None}
    out: list[ApprovalSummaryOut] = []
    for approval in approvals:
        out.append(
            ApprovalSummaryOut(
                id=approval.id,
                ticket_id=approval.ticket_id,
                subject_type=approval.subject_type,
                subject_ref=approval.subject_ref,
                approval_key=approval.approval_key,
                title=approval.title,
                status=approval.status,
                reason=approval.reason,
                current_stage=approval.current_stage,
                requester_label=labels[approval.requester],
                created_at=approval.created_at,
                decided_at=approval.decided_at,
                my_pending=approval.id in mine,
                can_cancel=approval.status == "pending"
                and await access.can_cancel(session, approval, actor),
            )
        )
    return out
