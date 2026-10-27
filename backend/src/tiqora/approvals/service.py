"""The approval engine's service API (A7), stable for consumers.

Request, activation, auto decision, cancel and the read functions live here; voting, forwarding
and refreshing are added by the decision tasks. Every function takes an ``AsyncSession``,
flushes and never commits: the caller owns the transaction. Every state-changing function locks
``tiqora_approval`` first (``FOR UPDATE``, ``populate_existing``) and reads the step/approver rows
it is about to change with locking reads, so InnoDB hands out the latest committed versions
instead of the REPEATABLE-READ snapshot - a consumer calling from inside its own transaction
cannot roll back to please the engine. Lock order: consumer row, ``tiqora_approval``,
step/approver/token rows. Timestamps are naive UTC (``utc_now``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import access, audit, delegation, rules, templates, tokens
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    EVENT_CANCELLED,
    EVENT_DECIDED,
    EVENT_REQUESTED,
    EVENT_VOTED,
    JOB_HOOK,
    Actor,
    ApprovalDefinition,
    ApprovalError,
    ApprovalInfo,
    ApprovalStatus,
    ApproverEntry,
    Channel,
    Decision,
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
    customer_in_ticket_company,
    resolve_entries,
)
from tiqora.approvals.settings import ApprovalSettings, load_approval_settings
from tiqora.config import Settings, get_settings
from tiqora.db.legacy.user import Users
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import enqueue_job, utc_now

_TITLE_MAX = 250
_KEY_MAX = 100
_SYSTEM = Actor.system()
_CANCEL_REASONS = frozenset({"cancelled", "superseded", "ticket_deleted"})
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_COMMENT_MAX = 2000
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


async def _settings(session: AsyncSession, settings: Settings | None) -> ApprovalSettings:
    return await load_approval_settings(session, settings or get_settings())


async def _lock_approval(session: AsyncSession, approval_id: int) -> TiqoraApproval:
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


async def _locked_steps(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalStep]:
    result = await session.execute(
        select(TiqoraApprovalStep)
        .where(TiqoraApprovalStep.approval_id == approval_id)
        .order_by(TiqoraApprovalStep.position)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def _locked_approvers(
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


async def _excluded_keys(session: AsyncSession, approval: TiqoraApproval) -> frozenset[str]:
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


def _allow_customers(
    approval: TiqoraApproval, settings: ApprovalSettings, registry: Registry
) -> bool:
    subject = registry.get(APPROVAL_SUBJECT_TYPES, approval.subject_type)
    return bool(
        settings.customer_approvers_enabled
        and subject is not None
        and subject.allow_customer_approvers
    )


async def _close_open_steps(
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
    for approver in await _locked_approvers(
        session, approval.id, step_ids=[s.id for s in open_steps]
    ):
        if approver.status == "pending":
            approver.status = "not_needed"
    for step in open_steps:
        step.status = "cancelled"
        step.reason = reason
        step.decided_at = now
    await session.flush()


async def _finalize(
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
            for a in await _locked_approvers(session, approval.id)
            if a.status == "pending" and a.last_notified_at is not None
        ]
    steps = await _locked_steps(session, approval.id)
    await _close_open_steps(session, approval, steps, reason)
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


def _result_reason(steps: Sequence[TiqoraApprovalStep], result: ApprovalStatus) -> str:
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


async def _add_approver(
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


async def _resolve_step(
    session: AsyncSession,
    approval: TiqoraApproval,
    spec: StepSpec,
    *,
    settings: ApprovalSettings,
    registry: Registry,
) -> Resolution:
    return await resolve_entries(
        session,
        spec.approvers,
        ctx=ResolveContext(
            ticket_id=approval.ticket_id,
            subject_type=approval.subject_type,
            subject_ref=approval.subject_ref,
            approval_id=approval.id,
        ),
        excluded_principals=await _excluded_keys(session, approval),
        allow_customers=_allow_customers(approval, settings, registry),
        registry=registry,
    )


async def _audit_unresolved(
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


async def _activate_step(
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
    resolution = await _resolve_step(session, approval, spec, settings=settings, registry=registry)
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
    await _audit_unresolved(session, approval, step, resolution, actor=actor)
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
        added.append(await _add_approver(session, approval, step, resolved, actor=actor, now=now))
    return added


async def _activate_next_stage(
    session: AsyncSession,
    approval: TiqoraApproval,
    *,
    settings: ApprovalSettings,
    registry: Registry,
    actor: Actor = _SYSTEM,
) -> list[int]:
    """Activates the next stage (A7a) and returns the approver ids that are now pending.

    Does nothing while a step is still active. A stage whose steps all end at activation
    (no valid approver, unreachable quorum) is followed by the next one in the same call; a
    rejected step closes the request, the last approved step approves it.
    """
    added: list[int] = []
    while approval.status == "pending":
        steps = await _locked_steps(session, approval.id)
        pairs: list[tuple[int, StepStatus]] = [(s.stage, s.status) for s in steps]  # type: ignore[misc]
        result = rules.request_result(pairs)
        if result == "rejected":
            await _finalize(
                session,
                approval,
                "rejected",
                _result_reason(steps, result),
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
                await _finalize(
                    session,
                    approval,
                    "approved",
                    _result_reason(steps, result),
                    actor=actor,
                    settings=settings,
                    registry=registry,
                )
            break
        for step in steps:
            if step.stage == stage and step.status == "waiting":
                added += await _activate_step(
                    session, approval, step, settings=settings, registry=registry, actor=actor
                )
    if not added:
        return []
    still_pending = {
        a.id for a in await _locked_approvers(session, approval.id) if a.status == "pending"
    }
    return [i for i in added if i in still_pending]


# --- request ---------------------------------------------------------------------------------


def _check_text(title: str, approval_key: str) -> tuple[str, str]:
    title = title.strip()
    approval_key = approval_key.strip()
    if not title or len(title) > _TITLE_MAX:
        raise ApprovalError("invalid_definition", 422, f"title must be 1-{_TITLE_MAX} characters")
    if not approval_key or len(approval_key) > _KEY_MAX:
        raise ApprovalError(
            "invalid_definition", 422, f"approval_key must be 1-{_KEY_MAX} characters"
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
        await _finalize(
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

    Raises ``unknown_subject_type``, ``ticket_not_found``, ``invalid_definition``,
    ``template_not_found``, ``customer_approvers_not_allowed`` or ``unknown_resolver``. With
    *supersede* a pending request with the same ``(ticket_id, approval_key)`` is cancelled
    (reason ``superseded``) and points at the new one through ``superseded_by``.
    """
    reg = registry or get_registry()
    title, approval_key = _check_text(title, approval_key)
    await _require_subject_and_ticket(session, subject_type, ticket_id, reg)
    asettings = await _settings(session, settings)
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
    new_ids = await _activate_next_stage(
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
    title, approval_key = _check_text(title, approval_key)
    await _require_subject_and_ticket(session, subject_type, ticket_id, reg)
    asettings = await _settings(session, settings)
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
    await _finalize(
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
    approval = await _lock_approval(session, approval_id)
    if check_permission and not await access.can_cancel(session, approval, actor):
        raise ApprovalError("forbidden", 403)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _settings(session, settings)
    await _finalize(
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


async def _voters(
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
    return [rules.VoterState(a.status, a.decisive) for a in eligible]  # type: ignore[arg-type]


async def _voters_in_step(session: AsyncSession, step_id: int) -> set[str]:
    """Principal keys that cast a vote in the step, on their own or on behalf (F8)."""
    rows = await session.execute(
        select(TiqoraApprovalVote.decided_by)
        .where(TiqoraApprovalVote.step_id == step_id)
        .with_for_update()
    )
    return {str(r) for r in rows.scalars()}


async def _close_step(
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
        for a in await _locked_approvers(session, approval.id, step_ids=[step.id])
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
    new_ids = await _activate_next_stage(
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
    if text_ is not None and len(text_) > _COMMENT_MAX:
        raise ApprovalError("comment_too_long", 422, f"at most {_COMMENT_MAX} characters")
    reg = registry or get_registry()
    # lock_held only documents that the caller took the lock; re-locking a row this transaction
    # holds is free, and honouring the keyword would let any caller skip the F1/F6 lock
    approval = await _lock_approval(session, approval_id)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _settings(session, settings)
    steps = await _locked_steps(session, approval.id)
    approvers = await _locked_approvers(session, approval.id)
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

    excluded = await _excluded_keys(session, approval)
    if excluded & ({key, row.principal} if on_behalf else {key}):
        raise ApprovalError("self_approval_forbidden", 403)
    assert principal is not None
    if principal.kind == "customer":
        assert principal.login is not None
        if not _allow_customers(approval, asettings, reg):
            raise ApprovalError("forbidden", 403, "customer approvers are not enabled")
        if not await customer_in_ticket_company(session, principal.login, approval.ticket_id):
            raise ApprovalError("customer_approver_outside_company", 403)
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

    outcome = rules.evaluate_step(step.rule, step.quorum, await _voters(session, step, approvers))  # type: ignore[arg-type]
    if outcome != "pending":
        await _close_step(
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
    for after in await _locked_approvers(session, approval.id):
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
    steps = await _locked_steps(session, approval.id)
    approvers = await _locked_approvers(session, approval.id)
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
    if target.key in await _excluded_keys(session, approval):
        raise ApprovalError("self_approval_forbidden", 403)
    if any(a.step_id == step.id and a.principal == target.key for a in approvers):
        raise ApprovalError("forward_target_invalid", 422, "the agent is already an approver")
    if target.key in await _voters_in_step(session, step.id):
        raise ApprovalError("already_decided", 409, "the agent already voted in this step")

    new_id = await _add_approver(
        session,
        approval,
        step,
        ResolvedApprover(target, "forward", str(row.id), None),
        actor=actor,
        now=utc_now(),
    )
    new = next(a for a in await _locked_approvers(session, approval.id) if a.id == new_id)
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
    approval = await _lock_approval(session, approval_id)
    if not await access.can_refresh(session, approval, actor):
        raise ApprovalError("forbidden", 403)
    if approval.status != "pending":
        raise ApprovalError("approval_closed", 409)
    asettings = await _settings(session, settings)
    steps = [s for s in await _locked_steps(session, approval.id) if s.status == "active"]
    approvers = await _locked_approvers(session, approval.id)
    now = utc_now()
    added_ids: list[int] = []
    removed = 0
    for step in steps:
        spec = StepSpec.model_validate_json(step.spec)
        resolution = await _resolve_step(session, approval, spec, settings=asettings, registry=reg)
        await _audit_unresolved(session, approval, step, resolution, actor=actor)
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
        have = {a.principal for a in rows} | await _voters_in_step(session, step.id)
        for resolved in resolution.approvers:
            if resolved.principal.key not in have:
                have.add(resolved.principal.key)
                added_ids.append(
                    await _add_approver(session, approval, step, resolved, actor=actor, now=now)
                )
    await audit.record(
        session,
        approval=approval,
        action="refreshed",
        actor=actor,
        detail={"added": len(added_ids), "removed": removed},
    )

    for step in steps:
        if approval.status != "pending" or step.status != "active":
            continue
        spec = StepSpec.model_validate_json(step.spec)
        current = await _locked_approvers(session, approval.id)
        voters = await _voters(session, step, current)
        outcome = rules.evaluate_step(step.rule, step.quorum, voters)  # type: ignore[arg-type]
        reason = "votes"
        if outcome == "pending" and not voters:
            outcome = "approved" if spec.on_no_approver == "approve" else "rejected"
            reason = "no_approvers"
        if outcome != "pending":
            await _close_step(
                session,
                approval,
                step,
                outcome,
                reason,
                settings=asettings,
                registry=reg,
                actor=actor,
                now=now,
            )
    if approval.status == "pending" and added_ids:
        still_pending = [
            a.id
            for a in await _locked_approvers(session, approval.id)
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
