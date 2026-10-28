"""The approval engine's service API (A7), stable for consumers.

Request, auto decision, cancel, voting, forwarding, refreshing and the read functions (C1); the
shared internals live in :mod:`tiqora.approvals._engine`. Every function takes an
``AsyncSession``, flushes and never commits: the caller owns the transaction. Every
state-changing function locks ``tiqora_approval`` first (``FOR UPDATE``, ``populate_existing``)
and reads the step/approver rows it is about to change with locking reads, so InnoDB hands out
the latest committed versions instead of the REPEATABLE-READ snapshot - a consumer calling from
inside its own transaction cannot roll back to please the engine. Lock order: consumer row,
``tiqora_approval``, step/approver/token rows, then the Znuny ``ticket`` row (history lines): a
consumer calls these functions before it writes the ticket in the same transaction (see
:mod:`tiqora.approvals.contract`). Timestamps are naive UTC (``utc_now``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import _engine, access, audit, delegation, rules, templates, tokens
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    COMMENT_MAX,
    EVENT_REQUESTED,
    EVENT_VOTED,
    Actor,
    ApprovalDefinition,
    ApprovalError,
    ApprovalInfo,
    ApprovalStatus,
    ApproverEntry,
    Channel,
    Decision,
    Principal,
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
    ResolvedApprover,
    canonical_principal,
)
from tiqora.approvals.settings import ApprovalSettings
from tiqora.config import Settings
from tiqora.db.legacy.user import Users
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now

_TITLE_MAX = 250
_KEY_MAX = 100
_SUBJECT_REF_MAX = 190  # tiqora_approval.subject_ref VARCHAR(190)
_CANCEL_REASONS = frozenset({"cancelled", "superseded", "ticket_deleted"})
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_VOTED = frozenset({"approved", "rejected"})


def _info(approval: TiqoraApproval) -> ApprovalInfo:
    status: ApprovalStatus = approval.status  # type: ignore[assignment]
    return ApprovalInfo(
        approval_id=approval.id,
        ticket_id=approval.ticket_id,
        subject_type=approval.subject_type,
        subject_ref=approval.subject_ref,
        approval_key=approval.approval_key,
        status=status,
        reason=approval.reason,
        current_stage=approval.current_stage,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
    )


# --- request ---------------------------------------------------------------------------------


def _check_text(title: str, approval_key: str, subject_ref: str | None) -> tuple[str, str]:
    title = title.strip()
    approval_key = approval_key.strip()
    if not title or len(title) > _TITLE_MAX:
        raise ApprovalError("invalid_definition", 422, f"title must be 1-{_TITLE_MAX} characters")
    if not approval_key or len(approval_key) > _KEY_MAX:
        raise ApprovalError(
            "invalid_definition", 422, f"approval_key must be 1-{_KEY_MAX} characters"
        )
    if subject_ref is not None and len(subject_ref) > _SUBJECT_REF_MAX:
        raise ApprovalError(
            "invalid_definition", 422, f"subject_ref must be at most {_SUBJECT_REF_MAX} characters"
        )
    return title, approval_key


async def _require_subject_and_ticket(
    session: AsyncSession, subject_type: str, ticket_id: int, registry: Registry
) -> None:
    if registry.get(APPROVAL_SUBJECT_TYPES, subject_type) is None:
        raise ApprovalError("unknown_subject_type", 422, subject_type)
    row = await session.execute(
        text("SELECT id, queue_id FROM ticket WHERE id = :t"), {"t": ticket_id}
    )
    if row.first() is None:
        raise ApprovalError("ticket_not_found", 404)


def _customer_entries(defn: ApprovalDefinition, registry: Registry) -> list[ApproverEntry]:
    entries: list[ApproverEntry] = []
    for step in defn.steps:
        for entry in (*step.approvers, *step.escalation):
            if entry.kind == "customer_user":
                entries.append(entry)
            elif entry.kind == "resolver":
                resolver = registry.get(APPROVER_RESOLVERS, entry.ref)
                if resolver is not None and resolver.yields_customers:
                    entries.append(entry)
    return entries


async def _validated_definition(
    session: AsyncSession,
    *,
    subject_type: str,
    definition: ApprovalDefinition | None,
    template_key: str | None,
    settings: ApprovalSettings,
    registry: Registry,
) -> tuple[ApprovalDefinition, int | None]:
    if (definition is None) == (template_key is None):
        raise ApprovalError(
            "invalid_definition", 422, "give exactly one of definition and template_key"
        )
    template_id: int | None = None
    if template_key is not None:
        template = await templates.get_template_by_key(session, template_key)
        if not template.valid:
            raise ApprovalError("template_not_found", 404)
        if template.subject_types and subject_type not in template.subject_types:
            raise ApprovalError(
                "invalid_definition",
                422,
                f"template {template_key!r} is not meant for subject type {subject_type!r}",
            )
        definition, template_id = template.definition, template.id
    assert definition is not None
    rules.validate_definition(definition)
    check = await templates.validate_definition_refs(
        session, definition, subject_types=[subject_type]
    )
    for error in check.errors:
        if error == "customer_entry_not_allowed":
            raise ApprovalError("customer_approvers_not_allowed", 422)
        if error.startswith("unknown_resolver:"):
            raise ApprovalError("unknown_resolver", 422, error.partition(":")[2])
        raise ApprovalError("invalid_definition", 422, error)
    if not settings.customer_approvers_enabled and _customer_entries(definition, registry):
        raise ApprovalError("customer_approvers_not_allowed", 422)
    return definition, template_id


async def _lock_pending_same_key(
    session: AsyncSession, ticket_id: int, approval_key: str
) -> list[TiqoraApproval]:
    result = await session.execute(
        select(TiqoraApproval)
        .where(
            TiqoraApproval.ticket_id == ticket_id,
            TiqoraApproval.approval_key == approval_key,
            TiqoraApproval.status == "pending",
        )
        .order_by(TiqoraApproval.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def _supersede(
    session: AsyncSession,
    olds: Sequence[TiqoraApproval],
    new: TiqoraApproval,
    *,
    actor: Actor,
    settings: ApprovalSettings,
    registry: Registry,
) -> None:
    for old in olds:
        old.superseded_by = new.id
        await _engine.finalize(
            session,
            old,
            "cancelled",
            "superseded",
            actor=actor,
            settings=settings,
            registry=registry,
        )


async def _requester_key(session: AsyncSession, requester: Principal | None, actor: Actor) -> str:
    principal = requester if requester is not None else actor.principal
    if principal is None:
        return "system"
    return (await canonical_principal(session, principal)).key


async def request_approval(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject_type: str,
    title: str,
    actor: Actor,
    requester: Principal | None = None,
    subject_ref: str | None = None,
    approval_key: str = "default",
    description: str | None = None,
    definition: ApprovalDefinition | None = None,
    template_key: str | None = None,
    allow_self_approval: bool = False,
    supersede: bool = True,
    settings: Settings | None = None,
    registry: Registry | None = None,
) -> ApprovalInfo:
    """Creates a request, activates stage one and queues the mails. Flushes, never commits.

    Raises ``unknown_subject_type``, ``ticket_not_found``, ``invalid_definition`` (also for a
    title, key or ``subject_ref`` that does not fit its column),
    ``template_not_found``, ``customer_approvers_not_allowed`` or ``unknown_resolver``. With
    *supersede* a pending request with the same ``(ticket_id, approval_key)`` is cancelled
    (reason ``superseded``) and points at the new one through ``superseded_by``.
    """
    reg = registry or get_registry()
    title, approval_key = _check_text(title, approval_key, subject_ref)
    await _require_subject_and_ticket(session, subject_type, ticket_id, reg)
    asettings = await _engine.load_settings(session, settings)
    defn, template_id = await _validated_definition(
        session,
        subject_type=subject_type,
        definition=definition,
        template_key=template_key,
        settings=asettings,
        registry=reg,
    )
    requester_key = await _requester_key(session, requester, actor)
    olds = await _lock_pending_same_key(session, ticket_id, approval_key) if supersede else []

    now = utc_now().replace(microsecond=0)  # MariaDB DATETIME has no microseconds
    approval = TiqoraApproval(
        ticket_id=ticket_id,
        subject_type=subject_type,
        subject_ref=subject_ref,
        approval_key=approval_key,
        title=title,
        description=description,
        template_id=template_id,
        status="pending",
        current_stage=None,
        allow_self_approval=allow_self_approval,
        requester=requester_key,
        created_by=actor.write_user_id,
        created_at=now,
    )
    session.add(approval)
    await session.flush()
    for position, spec in enumerate(defn.steps, start=1):
        session.add(
            TiqoraApprovalStep(
                approval_id=approval.id,
                ticket_id=ticket_id,
                position=position,
                stage=spec.stage,
                name=spec.name,
                rule=spec.rule,
                quorum=spec.quorum,
                status="waiting",
                spec=spec.model_dump_json(),
            )
        )
    await session.flush()
    await _supersede(session, olds, approval, actor=actor, settings=asettings, registry=reg)

    await audit.record(
        session,
        approval=approval,
        action="requested",
        actor=actor,
        detail={"steps": len(defn.steps), "template_id": template_id},
    )
    await audit.emit(session, approval, EVENT_REQUESTED)
    await audit.history(session, approval, "requested", actor=actor, settings=asettings)
    new_ids = await _engine.activate_next_stage(
        session, approval, settings=asettings, registry=reg, actor=actor
    )
    if approval.status == "pending":
        await enqueue_notification(
            session,
            approval_id=approval.id,
            kind="request",
            approver_ids=new_ids,
            registry=reg,
        )
    return _info(approval)


async def record_auto_decision(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject_type: str,
    title: str,
    actor: Actor,
    status: Literal["approved", "rejected"],
    reason: str = "auto",
    subject_ref: str | None = None,
    approval_key: str = "default",
    description: str | None = None,
    supersede: bool = True,
    settings: Settings | None = None,
    registry: Registry | None = None,
) -> ApprovalInfo:
    """Records an already decided request without steps (e.g. a pre-approved standard change).

    Audit ``auto_decided``, outbox ``TiqoraApprovalDecided``, history line and the hook job;
    no mails. Flushes, never commits.
    """
    if status not in ("approved", "rejected"):
        raise ApprovalError("invalid_definition", 422, "status must be approved or rejected")
    if not _REASON_RE.fullmatch(reason):
        raise ApprovalError("invalid_definition", 422, "reason must be a short lower-case code")
    reg = registry or get_registry()
    title, approval_key = _check_text(title, approval_key, subject_ref)
    await _require_subject_and_ticket(session, subject_type, ticket_id, reg)
    asettings = await _engine.load_settings(session, settings)
    requester_key = await _requester_key(session, None, actor)
    olds = await _lock_pending_same_key(session, ticket_id, approval_key) if supersede else []
    approval = TiqoraApproval(
        ticket_id=ticket_id,
        subject_type=subject_type,
        subject_ref=subject_ref,
        approval_key=approval_key,
        title=title,
        description=description,
        status="pending",
        allow_self_approval=False,
        requester=requester_key,
        created_by=actor.write_user_id,
        created_at=utc_now().replace(microsecond=0),
    )
    session.add(approval)
    await session.flush()
    await _supersede(session, olds, approval, actor=actor, settings=asettings, registry=reg)
    await _engine.finalize(
        session, approval, status, reason, actor=actor, settings=asettings, registry=reg, auto=True
    )
    return _info(approval)


async def cancel_approval(
    session: AsyncSession,
    approval_id: int,
    *,
    actor: Actor,
    reason: str = "cancelled",
    note: str | None = None,
    check_permission: bool = True,
    settings: Settings | None = None,
    registry: Registry | None = None,
) -> ApprovalInfo:
    """Cancels a pending request: open steps cancelled, approvers ``not_needed``, links dead.

    ``approval_closed`` (409) when it is already decided, ``forbidden`` (403) without
    :func:`access.can_cancel`. Consumers pass ``check_permission=False`` for their own
    automation. *note* is accepted for API compatibility but not stored: audit rows never
    carry free text, so a consumer that wants a note writes its own ticket note.
    """
    if reason not in _CANCEL_REASONS:
        raise ValueError(f"unknown cancel reason: {reason!r}")
    reg = registry or get_registry()
    approval = await _engine.lock_approval(session, approval_id)
    if check_permission and not await access.can_cancel(session, approval, actor):
        raise ApprovalError("forbidden", 403)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _engine.load_settings(session, settings)
    await _engine.finalize(
        session, approval, "cancelled", reason, actor=actor, settings=asettings, registry=reg
    )
    return _info(approval)


# --- decide ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DecisionResult:
    info: ApprovalInfo
    vote_id: int
    step_status: StepStatus
    on_behalf: bool
    #: agents whose inbox changed (for SSE)
    affected_user_ids: frozenset[int]


def _refusal(candidates: Sequence[TiqoraApprovalApprover]) -> ApprovalError:
    """Why none of the actor's rows can vote: already handled, step not active, or not theirs."""
    if any(r.status in _VOTED or r.status == "forwarded" for r in candidates):
        return ApprovalError("already_decided", 409)
    if any(r.status in ("pending", "not_needed") for r in candidates):  # pending: step not active
        return ApprovalError("step_not_active", 409)
    return ApprovalError("not_an_approver", 403)


async def _pick_approver(
    session: AsyncSession,
    approvers: Sequence[TiqoraApprovalApprover],
    steps: Sequence[TiqoraApprovalStep],
    *,
    actor: Actor,
    key: str,
    approver_id: int | None,
    voted_steps: frozenset[int],
) -> tuple[TiqoraApprovalApprover, bool]:
    """The row the actor votes with and whether that is on behalf of a delegator.

    Delegated rows count only in steps that allow delegation, and one person casts at most one
    vote per step (*voted_steps* holds the steps where the actor already voted, own or on behalf).
    """
    principal = actor.principal
    if principal is None:
        raise ApprovalError("not_an_approver", 403)
    delegators: set[int] = set()
    if principal.user_id is not None:
        delegators = await delegation.active_delegators(session, principal.user_id, at=utc_now())
        delegators.discard(principal.user_id)
    delegable = {s.id for s in steps if StepSpec.model_validate_json(s.spec).allow_delegation}

    def own(row: TiqoraApprovalApprover) -> bool:
        return row.principal == key

    def delegated(row: TiqoraApprovalApprover) -> bool:
        return row.user_id is not None and row.user_id in delegators and row.step_id in delegable

    if approver_id is not None:
        # an explicit id must be one of this request's rows and be the actor's or a delegator's
        candidates = [r for r in approvers if r.id == approver_id and (own(r) or delegated(r))]
    else:
        candidates = [r for r in approvers if own(r) or delegated(r)]
    active = {s.id for s in steps if s.status == "active"}
    usable = [r for r in candidates if r.status == "pending" and r.step_id in active]
    free = [r for r in usable if r.step_id not in voted_steps]
    if not free:
        if usable:  # F8: this person already voted in the step(s) of every row left
            raise ApprovalError("already_decided", 409)
        raise _refusal(candidates)
    free.sort(key=lambda r: (not own(r), r.id))
    return free[0], not own(free[0])


async def decide(
    session: AsyncSession,
    approval_id: int,
    *,
    actor: Actor,
    decision: Decision,
    channel: Channel,
    comment: str | None = None,
    approver_id: int | None = None,
    token_id: int | None = None,
    lock_held: bool = False,
    settings: Settings | None = None,
    registry: Registry | None = None,
) -> DecisionResult:
    """Records one vote (A7b) and moves the request on. Flushes, never commits.

    *lock_held* is accepted for ``redeem_link`` and has no effect: the request is always locked.

    The first statement is ``SELECT … FOR UPDATE`` on the request (callers roll back before
    calling, F1(a)); every later read of step/approver/vote rows is a locking read, so the
    latest committed state is evaluated, never a stale snapshot. Raises ``approval_not_found``,
    ``approval_closed``, ``not_an_approver``, ``already_decided``, ``step_not_active``,
    ``self_approval_forbidden``, ``customer_approver_outside_company``, ``forbidden`` (customer
    approvers switched off, or e-mail channel not allowed for the step) and ``comment_too_long``.
    """
    if decision not in _VOTED:
        raise ValueError(f"unknown decision: {decision!r}")
    text_ = comment.strip() if comment is not None else None
    if text_ is not None and len(text_) > COMMENT_MAX:
        raise ApprovalError("comment_too_long", 422, f"at most {COMMENT_MAX} characters")
    reg = registry or get_registry()
    # lock_held only documents that the caller took the lock; re-locking a row this transaction
    # holds is free, and honouring the keyword would let any caller skip the F1/F6 lock
    approval = await _engine.lock_approval(session, approval_id)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _engine.load_settings(session, settings)
    steps = await _engine.locked_steps(session, approval.id)
    approvers = await _engine.locked_approvers(session, approval.id)
    principal = actor.principal
    key = actor.key if principal is None else (await canonical_principal(session, principal)).key
    voted = await session.execute(
        select(TiqoraApprovalVote.step_id)
        .where(TiqoraApprovalVote.approval_id == approval.id, TiqoraApprovalVote.decided_by == key)
        .with_for_update()
    )
    row, on_behalf = await _pick_approver(
        session,
        approvers,
        steps,
        actor=actor,
        key=key,
        approver_id=approver_id,
        voted_steps=frozenset(int(i) for i in voted.scalars()),
    )
    step = next(s for s in steps if s.id == row.step_id)

    excluded = await _engine.excluded_keys(session, approval)
    if excluded & ({key, row.principal} if on_behalf else {key}):
        raise ApprovalError("self_approval_forbidden", 403)
    assert principal is not None
    if principal.kind == "customer":
        assert principal.login is not None
        await _engine.check_customer_approver(
            session, approval, principal.login, settings=asettings, registry=reg
        )
    if channel == "email" and not StepSpec.model_validate_json(step.spec).allow_email:
        raise ApprovalError("forbidden", 403, "this step does not accept decisions by e-mail")

    before = {a.id: a.status for a in approvers}
    now = utc_now().replace(microsecond=0)  # MariaDB DATETIME has no microseconds
    vote = TiqoraApprovalVote(
        approval_id=approval.id,
        step_id=step.id,
        approver_id=row.id,
        ticket_id=approval.ticket_id,
        decision=decision,
        comment=text_ or None,
        decided_by=key,
        on_behalf=on_behalf,
        channel=channel,
        decided_at=now,
    )
    session.add(vote)
    row.status = decision
    await session.flush()
    await tokens.revoke_links(session, approver_ids=[row.id], redeemed=token_id)
    await audit.record(
        session,
        approval=approval,
        action="voted",
        actor=actor,
        step_id=step.id,
        approver_id=row.id,
        channel=channel,
        detail={"decision": decision, "on_behalf": on_behalf},
    )
    await audit.emit(
        session, approval, EVENT_VOTED, step_id=step.id, decision=decision, channel=channel
    )
    await audit.history(session, approval, "voted", actor=actor, extra=decision, settings=asettings)

    # F8: this person voted in the step, so another row of theirs there can never be voted
    dead = [
        a
        for a in approvers
        if a.step_id == step.id and a.status == "pending" and a.principal == key
    ]
    for other in dead:
        other.status = "not_needed"
    if dead:
        await session.flush()
        await tokens.revoke_links(session, approver_ids=[a.id for a in dead])
        for other in dead:
            await audit.record(
                session,
                approval=approval,
                action="approver_removed",
                actor=actor,
                step_id=step.id,
                approver_id=other.id,
                detail={"reason": "already_voted"},
            )

    voters = await _engine.step_voters(session, step, approvers)
    outcome = rules.evaluate_step(step.rule, step.quorum, voters)  # type: ignore[arg-type]
    if outcome != "pending":
        await _engine.close_step(
            session,
            approval,
            step,
            outcome,
            "votes",
            settings=asettings,
            registry=reg,
            actor=actor,
            now=now,
        )

    affected = {principal.user_id} if principal.user_id is not None else set()
    for after in await _engine.locked_approvers(session, approval.id):
        if before.get(after.id) != after.status and after.user_id is not None:
            affected.add(after.user_id)
    return DecisionResult(
        info=_info(approval),
        vote_id=vote.id,
        step_status=step.status,  # type: ignore[arg-type]
        on_behalf=on_behalf,
        affected_user_ids=frozenset(affected),
    )


# --- forward / refresh -----------------------------------------------------------------------


async def forward(
    session: AsyncSession,
    approver_id: int,
    *,
    actor: Actor,
    to_user_id: int,
    comment: str | None = None,
    registry: Registry | None = None,
) -> int:
    """Hands the actor's own pending row to another agent; returns the new approver id.

    The old row becomes ``forwarded`` (its links die), the new one is a ``forward`` row of the
    same step (decisive like the old one) and gets a ``forwarded`` mail. Only the approver
    themselves forwards (never a delegate), a forward row cannot be forwarded again, and the
    step must allow delegation. *comment* is accepted for API compatibility but not stored:
    audit rows never carry free text. Raises ``forbidden`` (customers, system),
    ``approval_closed``, ``not_an_approver``, ``already_decided``, ``step_not_active``,
    ``forward_not_allowed``, ``forward_target_invalid`` and ``self_approval_forbidden``.
    """
    principal = actor.principal
    if principal is None or principal.kind != "agent" or principal.user_id is None:
        raise ApprovalError("forbidden", 403)
    reg = registry or get_registry()
    # the request lock is the first statement; the approver id only names which request
    approval = (
        await session.execute(
            select(TiqoraApproval)
            .where(
                TiqoraApproval.id
                == select(TiqoraApprovalApprover.approval_id)
                .where(TiqoraApprovalApprover.id == approver_id)
                .scalar_subquery()
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalError("not_an_approver", 403)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    steps = await _engine.locked_steps(session, approval.id)
    approvers = await _engine.locked_approvers(session, approval.id)
    row = next((a for a in approvers if a.id == approver_id), None)
    if row is None or row.principal != actor.key:
        raise ApprovalError("not_an_approver", 403)
    if row.status in ("approved", "rejected", "forwarded"):
        raise ApprovalError("already_decided", 409)
    step = next(s for s in steps if s.id == row.step_id)
    if row.status != "pending" or step.status != "active":
        raise ApprovalError("step_not_active", 409)
    if row.source_kind == "forward" or not StepSpec.model_validate_json(step.spec).allow_delegation:
        raise ApprovalError("forward_not_allowed", 409)

    if to_user_id == principal.user_id or to_user_id == delegation.SYSTEM_USER_ID:
        raise ApprovalError("forward_target_invalid", 422, "choose another agent")
    valid = (
        await session.execute(select(Users.valid_id).where(Users.id == to_user_id))
    ).scalar_one_or_none()
    if valid != 1:
        raise ApprovalError("forward_target_invalid", 422, "the agent is not valid")
    target = Principal.agent(to_user_id)
    if target.key in await _engine.excluded_keys(session, approval):
        raise ApprovalError("self_approval_forbidden", 403)
    if any(a.step_id == step.id and a.principal == target.key for a in approvers):
        raise ApprovalError("forward_target_invalid", 422, "the agent is already an approver")
    if target.key in await _engine.voters_in_step(session, step.id):
        raise ApprovalError("forward_target_invalid", 422, "the agent already voted in this step")

    new_id = await _engine.add_approver(
        session,
        approval,
        step,
        ResolvedApprover(target, "forward", str(row.id), None),
        actor=actor,
        now=utc_now(),
    )
    new = next(a for a in await _engine.locked_approvers(session, approval.id) if a.id == new_id)
    new.decisive = row.decisive
    row.status = "forwarded"
    await session.flush()
    await tokens.revoke_links(session, approver_ids=[row.id])
    await audit.record(
        session,
        approval=approval,
        action="forwarded",
        actor=actor,
        step_id=step.id,
        approver_id=row.id,
        detail={"to_user_id": to_user_id, "new_approver_id": new_id},
    )
    await enqueue_notification(
        session,
        approval_id=approval.id,
        kind="forwarded",
        approver_ids=[new_id],
        registry=reg,
    )
    return new_id


@dataclass(frozen=True, slots=True)
class RefreshResult:
    added: int
    removed: int


async def refresh_approvers(
    session: AsyncSession,
    approval_id: int,
    *,
    actor: Actor,
    settings: Settings | None = None,
    registry: Registry | None = None,
) -> RefreshResult:
    """Re-resolves the approvers of every active step ("Freigeber neu ermitteln").

    Uses the step's stored spec. Principals that now qualify and have no row in the step yet
    are added (``uq_tiqora_approval_approver``: a forwarded, removed or not-needed row counts as
    a row); pending rows that no longer qualify are ``removed`` unless they are escalation or
    forward rows or come from an entry that currently resolves to nothing; rows that voted stay.
    Each active step is then re-evaluated like after a vote (no voter left: ``on_no_approver``).
    Raises ``approval_not_found``, ``forbidden`` (without :func:`access.can_refresh`) and
    ``approval_closed``.
    """
    reg = registry or get_registry()
    approval = await _engine.lock_approval(session, approval_id)
    if not await access.can_refresh(session, approval, actor):
        raise ApprovalError("forbidden", 403)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _engine.load_settings(session, settings)
    steps = [s for s in await _engine.locked_steps(session, approval.id) if s.status == "active"]
    approvers = await _engine.locked_approvers(session, approval.id)
    now = utc_now()
    added_ids: list[int] = []
    removed = 0
    for step in steps:
        spec = StepSpec.model_validate_json(step.spec)
        resolution = await _engine.resolve_step(
            session, approval, spec, settings=asettings, registry=reg
        )
        await _engine.audit_unresolved(session, approval, step, resolution, actor=actor)
        wanted = {r.principal.key for r in resolution.approvers}
        silent = {(e.kind, e.ref) for e, _ in resolution.unresolved}
        rows = [a for a in approvers if a.step_id == step.id]
        for row in rows:
            if (
                row.status == "pending"
                and row.source_kind not in ("escalation", "forward")
                and row.principal not in wanted
                and (row.source_kind, row.source_ref) not in silent
            ):
                row.status = "removed"
                removed += 1
                await session.flush()
                await tokens.revoke_links(session, approver_ids=[row.id])
                await audit.record(
                    session,
                    approval=approval,
                    action="approver_removed",
                    actor=actor,
                    step_id=step.id,
                    approver_id=row.id,
                    detail={"reason": "departed"},
                )
        have = {a.principal for a in rows} | await _engine.voters_in_step(session, step.id)
        for resolved in resolution.approvers:
            if resolved.principal.key not in have:
                have.add(resolved.principal.key)
                added_ids.append(
                    await _engine.add_approver(
                        session, approval, step, resolved, actor=actor, now=now
                    )
                )
    await audit.record(
        session,
        approval=approval,
        action="refreshed",
        actor=actor,
        detail={"added": len(added_ids), "removed": removed},
    )

    await _engine.reevaluate_steps(
        session, approval, steps, settings=asettings, registry=reg, actor=actor, now=now
    )
    if approval.status == "pending" and added_ids:
        still_pending = [
            a.id
            for a in await _engine.locked_approvers(session, approval.id)
            if a.id in added_ids and a.status == "pending"
        ]
        await enqueue_notification(
            session,
            approval_id=approval.id,
            kind="request",
            approver_ids=still_pending,
            registry=reg,
        )
    return RefreshResult(added=len(added_ids), removed=removed)


# --- reads -----------------------------------------------------------------------------------


async def get_info(session: AsyncSession, approval_id: int) -> ApprovalInfo:
    approval = (
        await session.execute(select(TiqoraApproval).where(TiqoraApproval.id == approval_id))
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalError("approval_not_found", 404)
    return _info(approval)


async def approval_status(
    session: AsyncSession, *, ticket_id: int, approval_key: str
) -> ApprovalStatus | None:
    """Status of the newest request (highest id) for the key; None when there is none."""
    status = (
        await session.execute(
            select(TiqoraApproval.status)
            .where(
                TiqoraApproval.ticket_id == ticket_id, TiqoraApproval.approval_key == approval_key
            )
            .order_by(TiqoraApproval.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return status  # type: ignore[return-value]


async def approval_statuses(session: AsyncSession, *, ticket_id: int) -> dict[str, ApprovalStatus]:
    """``{approval_key: status}`` of the newest request per key."""
    rows = await session.execute(
        select(TiqoraApproval.approval_key, TiqoraApproval.status)
        .where(TiqoraApproval.ticket_id == ticket_id)
        .order_by(TiqoraApproval.id)
    )
    out: dict[str, ApprovalStatus] = {}
    for key, status in rows:
        out[str(key)] = status  # ascending ids: the last write per key is the newest
    return out


async def list_for_ticket(session: AsyncSession, ticket_id: int) -> list[ApprovalInfo]:
    """All requests of the ticket, newest first."""
    rows = await session.execute(
        select(TiqoraApproval)
        .where(TiqoraApproval.ticket_id == ticket_id)
        .order_by(TiqoraApproval.id.desc())
    )
    return [_info(a) for a in rows.scalars()]
