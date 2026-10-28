"""Internals of the approval engine shared by its modules (service, sweeper, links, reads).

Not part of the contract: consumers import ``contract`` and ``service`` only. The names here have
no leading underscore because ``service``, ``sweep``, ``tokens``, ``inbox`` and ``detail`` use
them; the module name keeps them out of the public surface.

The rules of :mod:`tiqora.approvals.service` hold here too: functions flush and never commit;
every state change locks ``tiqora_approval`` first (:func:`lock_approval`) and re-reads the
step/approver rows with locking reads (:func:`locked_steps`, :func:`locked_approvers`).
Timestamps are naive UTC (``utc_now``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit, rules, tokens
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    EVENT_CANCELLED,
    EVENT_DECIDED,
    JOB_HOOK,
    Actor,
    ApprovalError,
    ApprovalStatus,
    ApproverEntry,
    Principal,
    ResolveContext,
    StepSpec,
    StepStatus,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalStep,
    TiqoraApprovalVote,
)
from tiqora.approvals.notify import enqueue_notification
from tiqora.approvals.resolve import (
    Resolution,
    ResolvedApprover,
    canonical_principal,
    resolve_entries,
)
from tiqora.approvals.settings import ApprovalSettings, load_approval_settings
from tiqora.config import Settings, get_settings
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import enqueue_job, utc_now

SYSTEM = Actor.system()


async def load_settings(session: AsyncSession, settings: Settings | None) -> ApprovalSettings:
    return await load_approval_settings(session, settings or get_settings())


async def lock_approval(session: AsyncSession, approval_id: int) -> TiqoraApproval:
    """``SELECT … FOR UPDATE`` on the request, refreshing an instance the session already has."""
    approval = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalError("approval_not_found", 404)
    return approval


async def locked_steps(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalStep]:
    result = await session.execute(
        select(TiqoraApprovalStep)
        .where(TiqoraApprovalStep.approval_id == approval_id)
        .order_by(TiqoraApprovalStep.position)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def locked_approvers(
    session: AsyncSession, approval_id: int, *, step_ids: Sequence[int] | None = None
) -> list[TiqoraApprovalApprover]:
    stmt = select(TiqoraApprovalApprover).where(TiqoraApprovalApprover.approval_id == approval_id)
    if step_ids is not None:
        stmt = stmt.where(TiqoraApprovalApprover.step_id.in_(list(step_ids)))
    result = await session.execute(
        stmt.order_by(TiqoraApprovalApprover.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def excluded_keys(session: AsyncSession, approval: TiqoraApproval) -> frozenset[str]:
    """Requester and creator never approve their own request unless it allows self-approval.

    The creator comes from the audit trail: a system or portal actor (``created_by`` 1) does
    not make agent 1 a creator.
    """
    if approval.allow_self_approval:
        return frozenset()
    keys = {approval.requester}
    creator = await audit.creator_key(session, approval)
    if creator is not None:
        # a portal actor's key carries the login as typed; compare the stored spelling
        keys.add((await canonical_principal(session, Principal.parse(creator))).key)
    return frozenset(keys)


def allow_customers(
    approval: TiqoraApproval, settings: ApprovalSettings, registry: Registry
) -> bool:
    subject = registry.get(APPROVAL_SUBJECT_TYPES, approval.subject_type)
    return bool(
        settings.customer_approvers_enabled
        and subject is not None
        and subject.allow_customer_approvers
    )


async def close_open_steps(
    session: AsyncSession,
    approval: TiqoraApproval,
    steps: Sequence[TiqoraApprovalStep],
    reason: str,
) -> None:
    """Waiting/active steps -> cancelled, their pending approvers -> not_needed."""
    now = utc_now()
    open_steps = [s for s in steps if s.status in ("waiting", "active")]
    if not open_steps:
        return
    for approver in await locked_approvers(
        session, approval.id, step_ids=[s.id for s in open_steps]
    ):
        if approver.status == "pending":
            approver.status = "not_needed"
    for step in open_steps:
        step.status = "cancelled"
        step.reason = reason
        step.decided_at = now
    await session.flush()


async def finalize(
    session: AsyncSession,
    approval: TiqoraApproval,
    status: Literal["approved", "rejected", "cancelled"],
    reason: str,
    *,
    actor: Actor,
    settings: ApprovalSettings,
    registry: Registry | None = None,
    auto: bool = False,
) -> None:
    """Closes a request (A7c): row, open steps, tokens, audit, outbox, history, hook, mail."""
    reg = registry or get_registry()
    notified: list[int] = []
    if status == "cancelled" and approval.status == "pending":
        notified = [
            a.id
            for a in await locked_approvers(session, approval.id)
            if a.status == "pending" and a.last_notified_at is not None
        ]
    steps = await locked_steps(session, approval.id)
    await close_open_steps(session, approval, steps, reason)
    approval.status = status
    approval.reason = reason
    approval.decided_at = utc_now().replace(microsecond=0)
    approval.current_stage = None
    await session.flush()
    await tokens.revoke_approval_links(session, approval.id)

    detail: dict[str, object] = {"status": status, "reason": reason}
    if approval.superseded_by is not None:
        detail["superseded_by"] = approval.superseded_by
    if auto:
        action = "auto_decided"
    elif status == "cancelled":
        action = "superseded" if reason == "superseded" else "cancelled"
    else:
        action = "decided"
    await audit.record(session, approval=approval, action=action, actor=actor, detail=detail)
    if status == "cancelled":
        await audit.emit(session, approval, EVENT_CANCELLED, reason=reason)
        await audit.history(session, approval, "cancelled", actor=actor, settings=settings)
    else:
        await audit.emit(session, approval, EVENT_DECIDED, reason=reason)
        extra = f"{status} ({reason})" if reason != "votes" else status
        await audit.history(
            session, approval, "decided", actor=actor, extra=extra, settings=settings
        )
    await enqueue_job(
        session,
        kind=JOB_HOOK,
        payload={"approval_id": approval.id, "status": status},
        created_by=None,
        dedupe_key=f"approval:{approval.id}:{status}",
        registry=reg,
    )
    if status == "cancelled":
        await enqueue_notification(
            session,
            approval_id=approval.id,
            kind="cancelled",
            approver_ids=notified,
            registry=reg,
        )
    elif not auto and settings.mail_requester_on_decision and approval.requester != "system":
        await enqueue_notification(
            session,
            approval_id=approval.id,
            kind="decided",
            include_requester=True,
            registry=reg,
        )


def result_reason(steps: Sequence[TiqoraApprovalStep], result: ApprovalStatus) -> str:
    """The request's reason: that of the step that decided it (``votes`` when it has none)."""
    if result == "rejected":
        for step in steps:
            if step.status == "rejected":
                return step.reason or "votes"
        return "votes"
    reasons = {s.reason or "votes" for s in steps}
    if reasons == {"no_approvers"}:
        return "no_approvers"
    if reasons == {"timeout"}:
        return "timeout"
    return "votes"


async def add_approver(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    resolved: ResolvedApprover,
    *,
    actor: Actor,
    now: datetime,
) -> int:
    row = TiqoraApprovalApprover(
        approval_id=approval.id,
        step_id=step.id,
        ticket_id=approval.ticket_id,
        principal=resolved.principal.key,
        user_id=resolved.principal.user_id,
        customer_login=resolved.principal.login,
        source_kind=resolved.source_kind,
        source_ref=resolved.source_ref,
        source_label=resolved.source_label[:200] if resolved.source_label else None,
        decisive=False,
        status="pending",
        added_at=now,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        approval=approval,
        action="approver_added",
        actor=actor,
        step_id=step.id,
        approver_id=row.id,
        detail={"source": resolved.source_kind},
    )
    return row.id


async def resolve_step(
    session: AsyncSession,
    approval: TiqoraApproval,
    spec: StepSpec,
    *,
    settings: ApprovalSettings,
    registry: Registry,
    entries: Sequence[ApproverEntry] | None = None,
    source_kind_override: str | None = None,
) -> Resolution:
    """Resolves the step's approvers (or *entries*, e.g. its escalation list)."""
    return await resolve_entries(
        session,
        spec.approvers if entries is None else entries,
        ctx=ResolveContext(
            ticket_id=approval.ticket_id,
            subject_type=approval.subject_type,
            subject_ref=approval.subject_ref,
            approval_id=approval.id,
        ),
        excluded_principals=await excluded_keys(session, approval),
        allow_customers=allow_customers(approval, settings, registry),
        source_kind_override=source_kind_override,
        registry=registry,
    )


async def audit_excluded(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    resolution: Resolution,
    *,
    actor: Actor,
) -> None:
    for principal, why in resolution.excluded:
        who: dict[str, object] = (
            {"user_id": principal.user_id} if principal.kind == "agent" else {"kind": "customer"}
        )
        await audit.record(
            session,
            approval=approval,
            action="approver_excluded",
            actor=actor,
            step_id=step.id,
            detail={**who, "reason": why},
        )


async def audit_unresolved(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    resolution: Resolution,
    *,
    actor: Actor,
) -> None:
    for entry, why in resolution.unresolved:
        await audit.record(
            session,
            approval=approval,
            action="approver_excluded",
            actor=actor,
            step_id=step.id,
            detail={"kind": entry.kind, "ref": entry.ref, "reason": why},
        )


async def activate_step(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    *,
    settings: ApprovalSettings,
    registry: Registry,
    actor: Actor,
) -> list[int]:
    """Resolves and activates one step; returns the ids of the approver rows it added."""
    spec = StepSpec.model_validate_json(step.spec)
    resolution = await resolve_step(session, approval, spec, settings=settings, registry=registry)
    now = utc_now()
    due_hours = spec.due_hours if spec.due_hours is not None else settings.default_due_hours
    step.status = "active"
    step.activated_at = now
    step.due_at = now + timedelta(hours=due_hours) if due_hours else None
    approval.current_stage = step.stage
    await session.flush()
    await audit.record(
        session,
        approval=approval,
        action="step_activated",
        actor=actor,
        step_id=step.id,
        detail={"rule": step.rule, "quorum": step.quorum, "approvers": len(resolution.approvers)},
    )
    await audit_excluded(session, approval, step, resolution, actor=actor)
    await audit_unresolved(session, approval, step, resolution, actor=actor)
    count = len(resolution.approvers)
    if count == 0 or not rules.quorum_reachable(spec.rule, spec.quorum, count):
        step.status = "approved" if spec.on_no_approver == "approve" else "rejected"
        step.reason = "no_approvers"
        step.decided_at = now
        await session.flush()
        await audit.record(
            session,
            approval=approval,
            action="step_decided",
            actor=actor,
            step_id=step.id,
            detail={"decision": step.status, "reason": "no_approvers", "valid": count},
        )
        return []
    added: list[int] = []
    for resolved in resolution.approvers:
        added.append(await add_approver(session, approval, step, resolved, actor=actor, now=now))
    return added


async def activate_next_stage(
    session: AsyncSession,
    approval: TiqoraApproval,
    *,
    settings: ApprovalSettings,
    registry: Registry,
    actor: Actor = SYSTEM,
) -> list[int]:
    """Activates the next stage (A7a) and returns the approver ids that are now pending.

    Does nothing while a step is still active. A stage whose steps all end at activation
    (no valid approver, unreachable quorum) is followed by the next one in the same call; a
    rejected step closes the request, the last approved step approves it.
    """
    added: list[int] = []
    while approval.status == "pending":
        steps = await locked_steps(session, approval.id)
        pairs: list[tuple[int, StepStatus]] = [(s.stage, s.status) for s in steps]  # type: ignore[misc]
        result = rules.request_result(pairs)
        if result == "rejected":
            await finalize(
                session,
                approval,
                "rejected",
                result_reason(steps, result),
                actor=actor,
                settings=settings,
                registry=registry,
            )
            break
        if any(s.status == "active" for s in steps):
            break
        stage = rules.next_stage(pairs)
        if stage is None:
            if result == "approved":
                await finalize(
                    session,
                    approval,
                    "approved",
                    result_reason(steps, result),
                    actor=actor,
                    settings=settings,
                    registry=registry,
                )
            break
        for step in steps:
            if step.stage == stage and step.status == "waiting":
                added += await activate_step(
                    session, approval, step, settings=settings, registry=registry, actor=actor
                )
    if not added:
        return []
    still_pending = {
        a.id for a in await locked_approvers(session, approval.id) if a.status == "pending"
    }
    return [i for i in added if i in still_pending]


async def step_voters(
    session: AsyncSession, step: TiqoraApprovalStep, approvers: Sequence[TiqoraApprovalApprover]
) -> list[rules.VoterState]:
    """Eligible voters of the step in vote order, the ones still pending last."""
    votes = await session.execute(
        select(TiqoraApprovalVote)
        .where(TiqoraApprovalVote.step_id == step.id)
        .order_by(TiqoraApprovalVote.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    voted_at = {v.approver_id: (v.decided_at, v.id) for v in votes.scalars()}
    eligible = [
        a
        for a in approvers
        if a.step_id == step.id and a.status in ("pending", "approved", "rejected")
    ]
    eligible.sort(key=lambda a: (a.status == "pending", voted_at.get(a.id, (utc_now(), 0)), a.id))
    by_id = {a.id: a for a in approvers}
    return [
        rules.VoterState(a.status, a.decisive, in_rule(a, by_id))  # type: ignore[arg-type]
        for a in eligible
    ]


def in_rule(row: TiqoraApprovalApprover, by_id: dict[int, TiqoraApprovalApprover]) -> bool:
    """False for voters that exist only through an escalation (and their forward rows).

    A regular approver that an escalation made decisive keeps counting in the step's rule.
    """
    if not row.decisive:
        return True
    if row.source_kind == "escalation":
        return False
    if row.source_kind == "forward":
        ref = row.source_ref or ""
        origin = by_id.get(int(ref)) if ref.isdigit() else None
        return True if origin is None else in_rule(origin, by_id)
    return True


async def voters_in_step(session: AsyncSession, step_id: int) -> set[str]:
    """Principal keys that cast a vote in the step, on their own or on behalf (F8)."""
    rows = await session.execute(
        select(TiqoraApprovalVote.decided_by)
        .where(TiqoraApprovalVote.step_id == step_id)
        .with_for_update()
    )
    return {str(r) for r in rows.scalars()}


async def close_step(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    outcome: Literal["approved", "rejected"],
    reason: str,
    *,
    settings: ApprovalSettings,
    registry: Registry,
    actor: Actor,
    now: datetime,
) -> None:
    """A step has its result (A7b step 7): leftovers ``not_needed``, links dead, next stage."""
    step.status = outcome
    step.reason = reason
    step.decided_at = now
    leftover = [
        a
        for a in await locked_approvers(session, approval.id, step_ids=[step.id])
        if a.status == "pending"
    ]
    for other in leftover:
        other.status = "not_needed"
    await session.flush()
    await tokens.revoke_links(session, approver_ids=[a.id for a in leftover])
    await audit.record(
        session,
        approval=approval,
        action="step_decided",
        actor=actor,
        step_id=step.id,
        detail={"decision": outcome, "reason": reason},
    )
    new_ids = await activate_next_stage(
        session, approval, settings=settings, registry=registry, actor=actor
    )
    if approval.status == "pending":
        await enqueue_notification(
            session,
            approval_id=approval.id,
            kind="request",
            approver_ids=new_ids,
            registry=registry,
        )


async def reevaluate_steps(
    session: AsyncSession,
    approval: TiqoraApproval,
    steps: Sequence[TiqoraApprovalStep],
    *,
    settings: ApprovalSettings,
    registry: Registry,
    actor: Actor,
    now: datetime,
) -> None:
    """Re-evaluates active steps after their voters changed, like after a vote.

    No voter left: the step's ``on_no_approver``. Used by the refresh and by the sweeper.
    """
    for step in steps:
        if approval.status != "pending" or step.status != "active":
            continue
        spec = StepSpec.model_validate_json(step.spec)
        voters = await step_voters(session, step, await locked_approvers(session, approval.id))
        outcome = rules.evaluate_step(step.rule, step.quorum, voters)  # type: ignore[arg-type]
        reason = "votes"
        if outcome == "pending" and not voters:
            outcome = "approved" if spec.on_no_approver == "approve" else "rejected"
            reason = "no_approvers"
        if outcome != "pending":
            await close_step(
                session,
                approval,
                step,
                outcome,
                reason,
                settings=settings,
                registry=registry,
                actor=actor,
                now=now,
            )
