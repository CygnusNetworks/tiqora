"""Change transitions (design I4/I5): the one way a change moves from phase to phase.

:func:`transition` is the agent path. It locks the change row (``FOR UPDATE``), builds the
``GuardContext`` from stored data (risk evaluated right before the approval and the low-risk
route, ruling F16; conflicts and tasks in the system view, unfiltered) and asks the pure phase
machine. Failures answer 409 ``transition_refused`` with the guard codes. A manager passes a block
conflict with ``override_reason`` (event ``conflict_override``) and open tasks or a cancelled
post-approval with ``force``, which needs a ``reason`` (event ``forced``).

The side effects run in this order inside one savepoint, so a plugin veto or a failing approval
call leaves nothing behind: phase and its fields -> events -> approval port -> ticket state ->
dynamic-field mirror -> calendar mirror -> listeners. The approval call comes before every write
of the Znuny ticket row (state, owner, lock, history): the engine locks ``tiqora_approval`` and
then writes ticket history, so a ticket written first deadlocks with a concurrent vote on MariaDB
(lock order of the approvals contract: change row -> approval -> ticket). The ticket state changes
only on leaving Entwurf (``new`` -> ``state_open``) and on entering Abgeschlossen / Abgebrochen;
Abgelehnt leaves it alone (F11). Agents write through ``TicketWriteService.change_state`` (``rw``,
plugin veto, the Znuny lock and owner on close); the system path never changes the ticket state.

:func:`on_approval_outcome` is the system path, called once per final status by the approval
engine's hook job ``core.approval_hook`` through :func:`on_cab_decided`, the ``on_decided`` of the
subject type ``itsm.change`` (:func:`change_subject_type`), possibly more than once (the job is
retried): it re-reads the change row ``FOR UPDATE`` and acts only when phase, ``approval_round``
and ``approval_id`` still match (ruling CX8).

Every phase change on every path writes exactly one ``event="phase"`` row (X-1). Services never
commit; the route or the hook job does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalOutcome,
    ApprovalSubjectType,
    ApproverEntry,
    Principal,
)
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.ticket_access import TicketFacts
from tiqora.domain.ticket_write_service import InvalidInput, TicketWriteService
from tiqora.domain.ticket_write_service import TicketAccessDenied as WriteAccessDenied
from tiqora.itsm.change.approval_port import (
    APPROVAL_KEY,
    SUBJECT_TYPE,
    ApprovalSnapshot,
    describe_change,
    get_approval_port,
)
from tiqora.itsm.change.calendar_mirror import sync_appointment
from tiqora.itsm.change.conflicts import load_conflicts
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import is_change_manager, require_change_mode
from tiqora.itsm.change.impact import impact_is_current
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmCabMember,
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
)
from tiqora.itsm.change.phases import (
    FINAL_PHASES,
    SYSTEM_ONLY,
    TRANSITIONS,
    ChangeType,
    GuardContext,
    GuardFailure,
    Outcome,
    Phase,
    check_transition,
)
from tiqora.itsm.change.risk import evaluate_risk, questionnaire_complete
from tiqora.itsm.change.service import (
    ChangeEvent,
    ChangeView,
    _clean,
    _loaded_view,
    _lookup_id,
    _row,
    _ticket,
    mirror_dynamic_fields,
    notify_listeners,
    record_event,
)
from tiqora.itsm.change.settings import ItsmChangeSettings, load_change_settings
from tiqora.itsm.change.tasks import task_state_counts
from tiqora.jobs.service import utc_now
from tiqora.plugins.sync_hooks import OperationVetoed
from tiqora.process.ticket_state import get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

#: writes of the approval outcome path are attributed to the system user (ruling F2)
SYSTEM_USER_ID: Final = 1
MAX_TITLE: Final = 250
MAX_DESCRIPTION: Final = 2000
#: the engine cancelled an older round itself when a new one was requested
SUPERSEDED: Final = "superseded"
#: failures that mean "this step does not exist here"; no further guard is worth reporting
_STRUCTURAL: Final = frozenset({"transition_not_allowed", "system_only", "process_ticket"})
_WINDOW_TARGETS: Final = frozenset({"approval", "scheduled", "implementing"})
#: steps that record the post-implementation review
PIR_TARGETS: Final = frozenset({"review", "closed"})
#: reason handed to the approval engine when the change itself ends a pending approval (a code;
#: German text belongs to the UI)
CANCEL_REASON: Final = "change_cancelled"

OutcomeStatus = Literal["approved", "rejected", "cancelled"]


class TransitionIn(BaseModel):
    to: Phase
    #: shown in the timeline; required when ``force`` passes a guard
    reason: str | None = Field(None, max_length=2000)
    #: manager: passes block conflicts
    override_reason: str | None = Field(None, max_length=1000)
    #: manager: passes open tasks, or a cancelled post-approval on close
    force: bool = False
    outcome: Outcome | None = None
    #: the post-implementation review; only on the steps that record it (``review``, ``closed``)
    pir_notes: str | None = Field(None, max_length=20000)

    @model_validator(mode="after")
    def _pir_only_on_review(self) -> TransitionIn:
        if self.pir_notes is not None and self.to not in PIR_TARGETS:
            raise ValueError("Die Nachbetrachtung wird erst beim Abschluss erfasst.")
        return self


# --- CAB -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cab:
    id: int
    name: str
    rule: str
    quorum: int | None
    members: tuple[tuple[str, str], ...]


def effective_cab_id(row: TiqoraItsmChange, cfg: ItsmChangeSettings) -> int | None:
    """The change's own CAB, else (emergency) the emergency CAB, else the default CAB."""
    if row.cab_id:
        return int(row.cab_id)
    if row.change_type == "emergency" and cfg.emergency_cab_id:
        return cfg.emergency_cab_id
    return cfg.default_cab_id or None


async def _effective_cab(
    session: AsyncSession, row: TiqoraItsmChange, cfg: ItsmChangeSettings
) -> _Cab | None:
    cab_id = effective_cab_id(row, cfg)
    cab = None if cab_id is None else await session.get(TiqoraItsmCab, cab_id)
    if cab is None or not cab.valid:
        return None
    members = await session.execute(
        select(TiqoraItsmCabMember.member_kind, TiqoraItsmCabMember.member_ref)
        .where(TiqoraItsmCabMember.cab_id == cab.id)
        .order_by(TiqoraItsmCabMember.id)
    )
    return _Cab(
        id=cab.id,
        name=cab.name,
        rule=cab.rule,
        quorum=cab.quorum,
        members=tuple((str(k), str(r)) for k, r in members),
    )


def _definition(cab: _Cab, cfg: ItsmChangeSettings) -> ApprovalDefinition:
    """One CAB step in the engine's vocabulary (ruling CX7)."""
    try:
        return ApprovalDefinition.model_validate(
            {
                "steps": [
                    {
                        "name": cab.name,
                        "rule": cab.rule,
                        "quorum": cab.quorum if cab.rule == "quorum" else None,
                        "approvers": [
                            ApproverEntry.model_validate({"kind": k, "ref": r})
                            for k, r in cab.members
                        ],
                        "due_hours": cfg.approval_due_hours,
                        "on_timeout": cfg.approval_timeout,
                        "on_no_approver": "reject",
                        "allow_delegation": True,
                        "allow_email": True,
                    }
                ]
            }
        )
    except ValidationError as exc:
        # an admin row the CAB editor would refuse (Task 13 validates on save)
        raise ChangeError("cab_invalid", 409, cab_id=cab.id) from exc


# --- reading the guard facts -----------------------------------------------------------------


async def _lock(session: AsyncSession, ticket_id: int) -> TiqoraItsmChange | None:
    """The change row ``FOR UPDATE``, fresh from the database (not the identity map's copy)."""
    stmt = (
        select(TiqoraItsmChange)
        .where(TiqoraItsmChange.ticket_id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class _PostApproval:
    pending: bool = False
    rejected: bool = False
    cancelled: bool = False


async def _post_approval(session: AsyncSession, row: TiqoraItsmChange) -> _PostApproval:
    """State of an emergency post-approval (``approval_via="post"``) from the engine."""
    if row.approval_via != "post" or row.approval_id is None:
        return _PostApproval()
    snap = await get_approval_port().snapshot(session, approval_id=int(row.approval_id))
    if snap is None:
        # the approval set was erased (GDPR). An approval handled before counts as approved
        # (``approved_at``); otherwise nobody can decide any more, so it counts as cancelled and
        # a manager may close past it with force and a reason (fail closed). Residual: a
        # rejection erased afterwards is no longer known here (the F19 bar on "successful").
        if row.approved_at is not None:
            return _PostApproval()
        return _PostApproval(pending=True, cancelled=True)
    if snap.status == "pending":
        return _PostApproval(pending=True)
    if snap.status == "cancelled":
        return _PostApproval(pending=True, cancelled=True)
    return _PostApproval(rejected=snap.status == "rejected")


def _has_window(row: TiqoraItsmChange) -> bool:
    start, end = row.planned_start, row.planned_end
    return start is not None and end is not None and end > start


def _state_setting(from_phase: str, to: str, outcome: str | None, facts: TicketFacts) -> str | None:
    """The settings field naming the ticket state of this step (open question 15, F11)."""
    if to == "closed":
        return "state_closed_successful" if outcome == "successful" else "state_closed_failed"
    if to == "cancelled":
        return "state_closed_cancelled"
    if from_phase == "draft" and facts.state_type == "new":
        return "state_open"
    return None


async def _ticket_owner(session: AsyncSession, ticket_id: int) -> int:
    owner = (
        await session.execute(text("SELECT user_id FROM ticket WHERE id = :t"), {"t": ticket_id})
    ).scalar_one()
    return int(owner)


def _refused(failures: list[GuardFailure]) -> ChangeError:
    return ChangeError(
        "transition_refused",
        409,
        failures=[{"code": f.code, "overridable": f.overridable} for f in failures],
    )


# --- the agent path --------------------------------------------------------------------------


async def transition(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    data: TransitionIn,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ChangeView:
    """Move a change to ``data.to`` for an agent with ``rw`` (403 without, 404 unreadable).

    SYSTEM steps (the approval outcomes) are only taken by :func:`on_approval_outcome`; asked
    for here they answer ``system_only``. Final changes answer 409 ``change_final``.
    """
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    facts = await _ticket(session, ticket_id, user_id=user_id, write=True)
    row = await _lock(session, ticket_id)
    if row is None:
        raise ChangeError("change_not_found", 404)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)

    from_phase: Phase = row.phase  # type: ignore[assignment]
    change_type: ChangeType = row.change_type  # type: ignore[assignment]
    to = data.to
    pair = (from_phase, to)
    manager = await is_change_manager(session, user_id, cfg)
    process = await get_ticket_process_state(session, ticket_id) is not None
    # facts are read only for a step that exists (bounded reads, nothing for a refused pair)
    known = (
        not process
        and pair not in SYSTEM_ONLY
        and change_type in TRANSITIONS.get(pair, frozenset())
    )
    auto_route = pair == ("assessment", "scheduled")
    requests_cab = to == "approval" or pair == ("draft", "implementing")

    auto_low_risk = False
    if known and (to == "approval" or auto_route):
        # F16: never decide on a stale level
        evaluation = await evaluate_risk(session, settings, ticket_id=ticket_id, user_id=user_id)
        auto_low_risk = (
            cfg.auto_approve_low_risk
            and evaluation.effective_level == "low"
            and not evaluation.blocked
            and not evaluation.incomplete
        )
    block_conflicts = 0
    if known and to in _WINDOW_TARGETS:
        conflicts = await load_conflicts(session, ticket_id=ticket_id, user_id=None)
        # freeze occurrences that were cut off count as a block (fail closed)
        block_conflicts = sum(
            1 for c in conflicts if c.severity == "block" or c.kind == "freeze_truncated"
        )
    open_tasks = 0
    if known and from_phase == "implementing":
        open_tasks, _total = await task_state_counts(session, ticket_id)
    post = await _post_approval(session, row) if known and to == "closed" else _PostApproval()
    cab = await _effective_cab(session, row, cfg) if known and requests_cab else None
    complete = (
        await questionnaire_complete(session, ticket_id=ticket_id)
        if known and change_type == "normal" and from_phase == "assessment"
        else True
    )

    ctx = GuardContext(
        change_type=change_type,
        phase=from_phase,
        to=to,
        is_manager=manager,
        has_window=_has_window(row),
        plans_complete=bool(_clean(row.implementation_plan) and _clean(row.backout_plan)),
        questionnaire_complete=complete,
        impact_computed=impact_is_current(row),
        block_conflicts=block_conflicts,
        override_reason=data.override_reason,
        open_tasks=open_tasks,
        force=data.force,
        outcome=data.outcome,
        pir_notes_present=bool(_clean(data.pir_notes) or _clean(row.pir_notes)),
        approval_pending=post.pending,
        approval_rejected=post.rejected,
        cab_chosen=cab is not None,
        process_ticket=process,
        emergency_post_approval=cfg.emergency_post_approval,
        standard_requires_review=cfg.standard_requires_review,
        approval_cancelled=post.cancelled,
        force_approval=data.force,
        auto_low_risk=auto_low_risk,
    )
    failures = list(check_transition(ctx))
    overridden = (
        manager and block_conflicts > 0 and bool(_clean(data.override_reason)) and not auto_route
    )
    forced_tasks = manager and data.force and open_tasks > 0 and from_phase == "implementing"
    forced_approval = manager and data.force and to == "closed" and post.pending and post.cancelled
    if not any(f.code in _STRUCTURAL for f in failures):
        if pair == ("draft", "implementing") and cab is None:
            failures.append(GuardFailure("cab_missing"))
        if cab is not None and not cab.members:
            failures.append(GuardFailure("cab_empty"))
        # CX9: forcing past a guard needs a reason (the phase machine cannot see it); listed
        # together with the other failures, so the manager learns everything at once
        if (forced_tasks or forced_approval) and not _clean(data.reason):
            failures.append(GuardFailure("reason_missing"))
    if failures:
        raise _refused(failures)

    state_id: int | None = None
    setting = _state_setting(from_phase, to, data.outcome, facts)
    if setting is not None:
        name: str = getattr(cfg, setting)
        if name != facts.state:
            state_id = await _lookup_id(session, "ticket_state", name)
            if state_id is None:
                raise ChangeError("change_state_not_found", 409, setting=setting, state=name)

    old_via = row.approval_via
    now = utc_now()
    async with session.begin_nested():  # a veto or a failing approval call leaves nothing
        _apply_phase(row, to=to, outcome=data.outcome, now=now, user_id=user_id)
        pir = _clean(data.pir_notes)
        if pir is not None:
            row.pir_notes = pir
        await session.flush()
        if overridden:
            await record_event(
                session,
                ticket_id=ticket_id,
                event="conflict_override",
                user_id=user_id,
                detail={
                    "block_conflicts": block_conflicts,
                    "reason": _clean(data.override_reason),
                },
            )
        if forced_tasks or forced_approval:
            await record_event(
                session,
                ticket_id=ticket_id,
                event="forced",
                user_id=user_id,
                detail={
                    **({"open_tasks": open_tasks} if forced_tasks else {}),
                    **({"approval": "cancelled"} if forced_approval else {}),
                    "reason": _clean(data.reason),
                },
            )
        reason = _clean(data.reason)
        await record_event(
            session,
            ticket_id=ticket_id,
            event="phase",
            user_id=user_id,
            from_phase=from_phase,
            to_phase=to,
            detail=None if reason is None else {"reason": reason},
        )
        # the approval engine before any write of the Znuny ticket row (contract lock order)
        actor = Actor.agent(user_id)
        if requests_cab:
            assert cab is not None  # noqa: S101 - guarded above (cab_missing)
            await _request(
                session, cfg, row, facts, cab, actor=actor, now=now, post=to != "approval"
            )
        elif pair in (("draft", "scheduled"), ("assessment", "scheduled")):
            await _auto_decide(
                session,
                row,
                facts,
                actor=actor,
                via="template" if from_phase == "draft" else "risk",
                now=now,
            )
        elif (
            to == "cancelled"
            and row.approval_id is not None
            and (from_phase == "approval" or old_via == "post")
        ):
            await get_approval_port().cancel(
                session,
                approval_id=int(row.approval_id),
                actor=actor,
                reason=reason or CANCEL_REASON,
            )
        if state_id is not None:
            writer = TicketWriteService(
                session, session_factory or get_session_factory(), sysconfig
            )
            try:
                await writer.change_state(user_id, ticket_id, state_id)
            except WriteAccessDenied as exc:
                raise ChangeError("forbidden", 403) from exc
            except OperationVetoed as exc:
                raise ChangeError("vetoed", 409, message=str(exc)) from exc
            except InvalidInput as exc:
                raise ChangeError("invalid_input", 422, message=str(exc)) from exc
        await mirror_dynamic_fields(
            session, ticket_id=ticket_id, change_type=change_type, phase=to, user_id=user_id
        )
        await sync_appointment(
            session, cfg, ticket_id=ticket_id, user_id=user_id, base_url=settings.public_base_url
        )
        await notify_listeners(
            session, ChangeEvent(ticket_id, "phase", from_phase, to, user_id, now)
        )
    return await _loaded_view(session, cfg, ticket_id, user_id)


def _apply_phase(
    row: TiqoraItsmChange, *, to: Phase, outcome: Outcome | None, now: datetime, user_id: int
) -> None:
    """Phase and the fields that follow it (not flushed)."""
    from_phase = row.phase
    row.phase = to
    if to == "implementing":
        row.actual_start = now
    if from_phase == "implementing":
        row.actual_end = now
    if to == "closed":
        row.outcome = outcome
    if to == "draft":
        # rework / re-plan: the next approval is a new round. ``approval_id`` stays and points
        # at the previous round's set (the trail) until the next request replaces it.
        row.approval_via = None
        row.approved_at = None
    row.updated_at = now
    row.updated_by = user_id


def _title(facts: TicketFacts) -> str:
    return f"Change {facts.tn}: {facts.title}"[:MAX_TITLE]


async def _request(
    session: AsyncSession,
    cfg: ItsmChangeSettings,
    row: TiqoraItsmChange,
    facts: TicketFacts,
    cab: _Cab,
    *,
    actor: Actor,
    now: datetime,
    post: bool,
) -> ApprovalSnapshot:
    """Open the CAB approval of the next round (CX7). A request decided at once (nobody to ask)
    comes back through the outcome hook like any decision (N3)."""
    definition = _definition(cab, cfg)
    row.approval_round = int(row.approval_round) + 1
    snap = await get_approval_port().request(
        session,
        ticket_id=int(row.ticket_id),
        round=int(row.approval_round),
        title=_title(facts),
        description=(row.reason or "")[:MAX_DESCRIPTION],
        definition=definition,
        actor=actor,
        requester=Principal.agent(await _ticket_owner(session, int(row.ticket_id))),
        allow_self_approval=cfg.cab_allow_self_approval,
    )
    row.approval_id = snap.approval_id
    row.approval_via = "post" if post else "cab"
    row.updated_at = now
    await session.flush()
    await record_event(
        session,
        ticket_id=int(row.ticket_id),
        event="approval_requested",
        user_id=actor.write_user_id,
        detail={
            "approval_id": snap.approval_id,
            #: CAB entries (a group or list is one entry), not people
            "cab_entries": len(cab.members),
            "cab_id": cab.id,
            "round": int(row.approval_round),
        },
    )
    return snap


async def _auto_decide(
    session: AsyncSession,
    row: TiqoraItsmChange,
    facts: TicketFacts,
    *,
    actor: Actor,
    via: Literal["template", "risk"],
    now: datetime,
) -> None:
    """Pre-approved (standard template) or low risk: an approved trail without a vote (F14)."""
    row.approval_round = int(row.approval_round) + 1
    snap = await get_approval_port().auto_decision(
        session,
        ticket_id=int(row.ticket_id),
        round=int(row.approval_round),
        title=_title(facts),
        actor=actor,
    )
    row.approval_id = snap.approval_id
    row.approval_via = via
    row.approved_at = now
    await session.flush()
    await record_event(
        session,
        ticket_id=int(row.ticket_id),
        event="approval_outcome",
        user_id=actor.write_user_id,
        detail={
            "approval_id": snap.approval_id,
            "round": int(row.approval_round),
            "status": snap.status,
            "via": via,
            **({} if snap.reason is None else {"reason": snap.reason}),
        },
    )


# --- the system path -------------------------------------------------------------------------


async def _outcome_recorded(session: AsyncSession, ticket_id: int, approval_id: int) -> bool:
    """An outcome of this approval set was already handled (the hook job is retried)."""
    rows = await session.execute(
        select(TiqoraItsmChangeEvent.detail).where(
            TiqoraItsmChangeEvent.ticket_id == ticket_id,
            TiqoraItsmChangeEvent.event == "approval_outcome",
        )
    )
    for (raw,) in rows:
        try:
            if raw and json.loads(raw).get("approval_id") == approval_id:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _system_target(
    row: TiqoraItsmChange, status: OutcomeStatus, reason: str | None
) -> Phase | None:
    if row.phase == "approval":
        if status == "approved":
            return "scheduled"
        if status == "rejected":
            return "rejected"
        # cancelled by an admin: back to where it was requested from (CX9)
        return "assessment" if row.change_type == "normal" else "draft"
    if row.phase == "implementing" and status == "rejected":
        return "review"  # F19: post-approval rejected while implementing
    return None


def _system_ctx(row: TiqoraItsmChange, to: Phase) -> GuardContext:
    return GuardContext(
        change_type=row.change_type,  # type: ignore[arg-type]
        phase=row.phase,  # type: ignore[arg-type]
        to=to,
        is_manager=False,
        has_window=_has_window(row),
        plans_complete=True,
        questionnaire_complete=True,
        impact_computed=True,
        block_conflicts=0,
        override_reason=None,
        open_tasks=0,
        force=False,
        outcome=None,
        pir_notes_present=True,
        approval_pending=False,
        approval_rejected=False,
        cab_chosen=True,
        process_ticket=False,
    )


async def on_approval_outcome(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    approval_id: int,
    round: int,
    status: OutcomeStatus,
    reason: str | None = None,
) -> bool:
    """Apply the engine's final decision of a CAB approval; True when something changed.

    In ``approval``: approved -> Geplant, rejected (also ``no_approvers``, N3) -> Abgelehnt,
    cancelled (not ``superseded``) -> Bewertung (normal) / Entwurf (emergency), CX9. After an
    emergency post-approval (``approval_via="post"``) a rejection while implementing forces
    Nachbetrachtung (F19, event ``forced``); everything else there is bookkeeping (event
    ``approval_outcome``; ``approved_at`` on approval). Ignored (False): a missing change, another
    round or approval set, a phase that moved on (e.g. the change cancelled it itself), an
    outcome already handled.
    """
    _ = sysconfig
    row = await _lock(session, ticket_id)
    if row is None or row.approval_id != approval_id or row.approval_round != round:
        return False
    in_approval = row.phase == "approval"
    post = row.approval_via == "post" and row.phase in ("implementing", "review")
    if not (in_approval or post):
        return False
    if in_approval and status == "cancelled" and reason == SUPERSEDED:
        return False
    if await _outcome_recorded(session, ticket_id, approval_id):
        return False
    to = _system_target(row, status, reason)
    if to is not None and check_transition(_system_ctx(row, to), system=True):
        logger.warning(
            "change_outcome_step_refused",
            extra={"ticket_id": ticket_id, "phase": row.phase, "to": to, "status": status},
        )
        return False

    now = utc_now()
    from_phase: Phase = row.phase  # type: ignore[assignment]
    async with session.begin_nested():
        if status == "approved":
            row.approved_at = now
        if to is not None:
            _apply_phase(row, to=to, outcome=None, now=now, user_id=SYSTEM_USER_ID)
        await session.flush()
        await record_event(
            session,
            ticket_id=ticket_id,
            event="approval_outcome",
            user_id=SYSTEM_USER_ID,
            detail={
                "approval_id": approval_id,
                "round": round,
                "status": status,
                **({} if reason is None else {"reason": reason}),
            },
        )
        if to is None:
            return True
        if from_phase == "implementing":
            await record_event(
                session,
                ticket_id=ticket_id,
                event="forced",
                user_id=SYSTEM_USER_ID,
                detail={"approval_id": approval_id, "reason": "approval_rejected"},
            )
        await record_event(
            session,
            ticket_id=ticket_id,
            event="phase",
            user_id=SYSTEM_USER_ID,
            from_phase=from_phase,
            to_phase=to,
        )
        await mirror_dynamic_fields(
            session,
            ticket_id=ticket_id,
            change_type=row.change_type,
            phase=to,
            user_id=SYSTEM_USER_ID,
        )
        await sync_appointment(
            session,
            await load_change_settings(session, settings),
            ticket_id=ticket_id,
            user_id=SYSTEM_USER_ID,
            base_url=settings.public_base_url,
        )
        await notify_listeners(
            session, ChangeEvent(ticket_id, "phase", from_phase, to, SYSTEM_USER_ID, now)
        )
    return True


async def on_cab_decided(session: AsyncSession, outcome: ApprovalOutcome) -> None:
    """``on_decided`` of the subject type ``itsm.change``: runs in job ``core.approval_hook``
    after the deciding commit, possibly more than once (retried up to 5 times) - idempotent
    through :func:`on_approval_outcome`. Never commits or rolls back (the job does). Sets of
    another key, or without a round in ``subject_ref``, are not CAB approvals of a change."""
    ref = outcome.subject_ref
    if outcome.approval_key != APPROVAL_KEY or ref is None or not (ref.isascii() and ref.isdigit()):
        return
    if outcome.status == "pending":  # never delivered; the contract says final statuses only
        return
    await on_approval_outcome(
        session,
        get_settings(),
        SysConfig(session),
        ticket_id=outcome.ticket_id,
        approval_id=outcome.approval_id,
        round=int(ref),
        status=outcome.status,
        reason=outcome.reason,
    )


def change_subject_type() -> ApprovalSubjectType:
    """The approval subject type of change CAB approvals (ruling CX7), registered by core."""
    return ApprovalSubjectType(
        key=SUBJECT_TYPE,
        title_key="approvals.subject.itsm_change",
        allow_customer_approvers=True,
        agent_requestable=False,
        describe=describe_change,
        on_decided=on_cab_decided,
    )


# --- approval read ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeApprovalView:
    approval_id: int | None
    approval_round: int
    approval_via: str | None
    approved_at: datetime | None
    #: None without an approval, or when the set is gone
    snapshot: ApprovalSnapshot | None
    #: ``approval_id`` is set but the engine no longer has the set (GDPR erasure, CX14/N6):
    #: "Freigabeverlauf nicht mehr vorhanden"
    history_missing: bool


async def read_approval(
    session: AsyncSession, settings: Settings, *, ticket_id: int, user_id: int
) -> ChangeApprovalView:
    """The approval state of a readable change. Never fails because of the approval engine."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    row = await _row(session, ticket_id)
    snap: ApprovalSnapshot | None = None
    if row.approval_id is not None:
        try:
            snap = await get_approval_port().snapshot(session, approval_id=int(row.approval_id))
        except ChangeError:
            logger.warning("change_approval_snapshot_failed", extra={"ticket_id": ticket_id})
    return ChangeApprovalView(
        approval_id=row.approval_id,
        approval_round=int(row.approval_round),
        approval_via=row.approval_via,
        approved_at=row.approved_at,
        snapshot=snap,
        history_missing=row.approval_id is not None and snap is None,
    )
