"""Change phase machine (pure): the one place that says which phase may follow which.

No database, network or file system access. ``check_transition`` answers with stable guard codes;
the transition service (Task 12) builds the ``GuardContext`` from the stored change and applies the
side effects. Approval outcomes are the only SYSTEM transitions: a person cannot take them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, get_args

Phase = Literal[
    "draft",
    "submitted",
    "assessment",
    "approval",
    "scheduled",
    "implementing",
    "review",
    "closed",
    "rejected",
    "cancelled",
]
ChangeType = Literal["standard", "normal", "emergency"]
Outcome = Literal["successful", "failed", "backed_out"]

#: Public, ordered value lists (the statistics build their filters from these).
PHASES: Final[tuple[Phase, ...]] = get_args(Phase)
CHANGE_TYPES: Final[tuple[ChangeType, ...]] = get_args(ChangeType)
OUTCOMES: Final[tuple[Outcome, ...]] = get_args(Outcome)

ACTIVE_PHASES: Final[frozenset[Phase]] = frozenset(
    {"submitted", "assessment", "approval", "scheduled", "implementing", "review"}
)
#: ``rejected`` is not final: the change can be reworked ("Überarbeiten") and requested again.
FINAL_PHASES: Final[frozenset[Phase]] = frozenset({"closed", "cancelled"})
#: Phases a change can still leave or be reworked in (everything but the final ones).
REOPENABLE_PHASES: Final[frozenset[Phase]] = frozenset(PHASES) - FINAL_PHASES

_NORMAL: Final = frozenset[ChangeType]({"normal"})
_STANDARD: Final = frozenset[ChangeType]({"standard"})
_EMERGENCY: Final = frozenset[ChangeType]({"emergency"})
_NORMAL_EMERGENCY: Final = frozenset[ChangeType]({"normal", "emergency"})
_ALL: Final = frozenset[ChangeType](CHANGE_TYPES)

# (from, to) -> change types allowed to take the step
TRANSITIONS: Final[Mapping[tuple[Phase, Phase], frozenset[ChangeType]]] = MappingProxyType(
    {
        ("draft", "submitted"): _NORMAL,
        ("draft", "scheduled"): _STANDARD,  # pre-approved by the template
        ("draft", "approval"): _EMERGENCY,
        ("draft", "implementing"): _EMERGENCY,  # only with emergency_post_approval
        ("submitted", "assessment"): _NORMAL,
        ("submitted", "draft"): _NORMAL,
        ("assessment", "approval"): _NORMAL,
        ("assessment", "scheduled"): _NORMAL,  # auto-approved low risk (setting + level low)
        ("approval", "scheduled"): _NORMAL_EMERGENCY,  # SYSTEM: approved
        ("approval", "rejected"): _NORMAL_EMERGENCY,  # SYSTEM: rejected
        ("approval", "assessment"): _NORMAL,  # SYSTEM: approval cancelled
        ("approval", "draft"): _EMERGENCY,  # SYSTEM: approval cancelled
        ("rejected", "draft"): _NORMAL_EMERGENCY,
        ("scheduled", "implementing"): _ALL,
        ("scheduled", "draft"): _ALL,
        ("implementing", "review"): frozenset[ChangeType]({"normal", "emergency", "standard"}),
        ("implementing", "closed"): _STANDARD,
        ("review", "closed"): _ALL,
        ("draft", "cancelled"): _ALL,
        ("submitted", "cancelled"): _ALL,
        ("assessment", "cancelled"): _ALL,
        ("approval", "cancelled"): _ALL,
        ("scheduled", "cancelled"): _ALL,
        ("rejected", "cancelled"): _ALL,  # give up a rejected change (no approval to cancel)
    }
)

#: Taken only by the approval outcome handler (``system=True``).
SYSTEM_ONLY: Final[frozenset[tuple[Phase, Phase]]] = frozenset(
    {
        ("approval", "scheduled"),
        ("approval", "rejected"),
        ("approval", "assessment"),
        ("approval", "draft"),
    }
)
#: Pairs the system may take besides ``SYSTEM_ONLY`` (a person may take them as well).
#: Emergency, post-approval rejection: implementing -> review, ``force`` implied.
SYSTEM_ALSO: Final[Mapping[tuple[Phase, Phase], frozenset[ChangeType]]] = MappingProxyType(
    {("implementing", "review"): _EMERGENCY}
)


@dataclass(frozen=True, slots=True)
class GuardContext:
    change_type: ChangeType
    phase: Phase
    to: Phase
    is_manager: bool
    has_window: bool
    #: implementation_plan and backout_plan non-empty (normal/emergency)
    plans_complete: bool
    #: every valid question answered (normal only); true when no valid question exists
    questionnaire_complete: bool
    impact_computed: bool
    #: after emergency/allow_emergency downgrade
    block_conflicts: int
    #: manager override for block conflicts
    override_reason: str | None
    open_tasks: int
    #: manager force with open tasks (or closing while a post-approval decision is missing)
    force: bool
    outcome: Outcome | None
    pir_notes_present: bool
    #: emergency post-approval: a decision is still missing
    approval_pending: bool
    approval_rejected: bool
    cab_chosen: bool
    process_ticket: bool
    #: setting ``emergency_post_approval``
    emergency_post_approval: bool = False
    #: setting ``standard_requires_review``
    standard_requires_review: bool = False
    #: the pending approval of the current round was cancelled (not decided)
    approval_cancelled: bool = False
    #: manager forces the close past a CANCELLED approval; never past one still deciding
    force_approval: bool = False
    #: setting ``auto_approve_low_risk`` is on AND the effective risk level is ``low``
    auto_low_risk: bool = False


@dataclass(frozen=True, slots=True)
class GuardFailure:
    #: stable API code: "window_missing", "plans_missing", "questionnaire_incomplete",
    #: "impact_pending", "block_conflict", "tasks_open", "outcome_missing", "pir_missing",
    #: "approval_pending", "cab_missing", "transition_not_allowed", "system_only",
    #: "process_ticket", "auto_approval_not_eligible"
    code: str
    #: True: a manager may pass override_reason / force
    overridable: bool = False


def _pair_allowed(ctx: GuardContext, *, system: bool) -> bool:
    pair = (ctx.phase, ctx.to)
    types = TRANSITIONS.get(pair)
    if types is None or ctx.change_type not in types:
        return False
    if system and pair not in SYSTEM_ONLY:
        also = SYSTEM_ALSO.get(pair)
        if also is None or ctx.change_type not in also:
            return False
    if pair == ("draft", "implementing") and not ctx.emergency_post_approval:
        return False
    if ctx.change_type == "standard":
        if pair == ("implementing", "review") and not ctx.standard_requires_review:
            return False
        if pair == ("implementing", "closed") and ctx.standard_requires_review:
            return False
    return True


def check_transition(ctx: GuardContext, *, system: bool = False) -> tuple[GuardFailure, ...]:
    """Every guard failure of the step ``ctx.phase -> ctx.to`` (empty = allowed)."""
    # A change whose ticket later became a process ticket may only be cancelled by a manager,
    # from any phase that is not final (also implementing/review: that is its only exit).
    if ctx.process_ticket:
        if ctx.to == "cancelled" and ctx.is_manager and not system:
            if ctx.phase in REOPENABLE_PHASES:
                return ()
            return (GuardFailure("transition_not_allowed"),)
        return (GuardFailure("process_ticket"),)
    pair = (ctx.phase, ctx.to)
    if not _pair_allowed(ctx, system=system):
        return (GuardFailure("transition_not_allowed"),)
    if not system and pair in SYSTEM_ONLY:
        return (GuardFailure("system_only"),)
    if system:
        # outcomes of the approval are facts, not requests: no preconditions
        return ()

    failures: list[GuardFailure] = []
    to = ctx.to
    typ = ctx.change_type
    manager = ctx.is_manager

    if to in ("approval", "scheduled", "implementing"):
        if not ctx.has_window:
            failures.append(GuardFailure("window_missing"))
        if ctx.block_conflicts > 0:
            # the auto-approval route needs "no block finding": an override cannot open it
            # (the manager takes the CAB route instead)
            auto = pair == ("assessment", "scheduled")
            if auto or not (manager and (ctx.override_reason or "").strip()):
                failures.append(GuardFailure("block_conflict", overridable=not auto))

    if pair == ("assessment", "scheduled") and not ctx.auto_low_risk:
        failures.append(GuardFailure("auto_approval_not_eligible"))

    needs_plans = typ in ("normal", "emergency") and pair in {
        ("assessment", "approval"),
        ("assessment", "scheduled"),
        ("draft", "approval"),
    }
    if needs_plans and not ctx.plans_complete:
        failures.append(GuardFailure("plans_missing"))
    if typ == "normal" and pair in {("assessment", "approval"), ("assessment", "scheduled")}:
        if not ctx.questionnaire_complete:
            failures.append(GuardFailure("questionnaire_incomplete"))
        if not ctx.impact_computed:
            failures.append(GuardFailure("impact_pending"))
    if to == "approval" and not ctx.cab_chosen:
        failures.append(GuardFailure("cab_missing"))

    if (
        pair in {("implementing", "review"), ("implementing", "closed")}
        and ctx.open_tasks > 0
        and not (manager and ctx.force)
    ):
        failures.append(GuardFailure("tasks_open", overridable=True))

    if to == "closed":
        if ctx.outcome is None:
            failures.append(GuardFailure("outcome_missing"))
        if pair == ("review", "closed") and typ != "standard" and not ctx.pir_notes_present:
            failures.append(GuardFailure("pir_missing"))
        # only a cancelled approval may be forced past; a decision still pending never
        forced = manager and ctx.force_approval and ctx.approval_cancelled
        if ctx.approval_pending and not forced:
            failures.append(GuardFailure("approval_pending", overridable=ctx.approval_cancelled))
        if ctx.approval_rejected and ctx.outcome == "successful":
            failures.append(GuardFailure("transition_not_allowed"))

    return tuple(failures)


def allowed_targets(
    phase: Phase,
    change_type: ChangeType,
    *,
    emergency_post_approval: bool,
    standard_requires_review: bool,
    auto_approve_low_risk: bool,
) -> tuple[Phase, ...]:
    """Targets a person may offer for ``phase`` (SYSTEM-only targets are left out).

    ``auto_approve_low_risk`` has no default (fail-closed: callers state it). Preconditions are
    assumed met, including ``cab_chosen`` (for an emergency change: the emergency CAB, else the
    default CAB, is chosen); the guards of ``check_transition`` decide at the actual step.
    """
    out: list[Phase] = []
    for to in PHASES:
        pair = (phase, to)
        if pair in SYSTEM_ONLY:
            continue
        if pair == ("assessment", "scheduled") and not auto_approve_low_risk:
            continue
        ctx = GuardContext(
            change_type=change_type,
            phase=phase,
            to=to,
            is_manager=False,
            has_window=True,
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
            emergency_post_approval=emergency_post_approval,
            standard_requires_review=standard_requires_review,
        )
        if _pair_allowed(ctx, system=False):
            out.append(to)
    return tuple(out)
