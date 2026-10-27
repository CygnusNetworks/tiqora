from __future__ import annotations

from dataclasses import replace
from itertools import product

import pytest

from tiqora.itsm.change.phases import (
    ACTIVE_PHASES,
    CHANGE_TYPES,
    FINAL_PHASES,
    OUTCOMES,
    PHASES,
    REOPENABLE_PHASES,
    SYSTEM_ONLY,
    TRANSITIONS,
    ChangeType,
    GuardContext,
    Phase,
    allowed_targets,
    check_transition,
)

ALL: frozenset[ChangeType] = frozenset(CHANGE_TYPES)
NE: frozenset[ChangeType] = frozenset({"normal", "emergency"})

# Written out independently of TRANSITIONS: (from, to) -> types.
EXPECTED: dict[tuple[Phase, Phase], frozenset[ChangeType]] = {
    ("draft", "submitted"): frozenset({"normal"}),
    ("draft", "scheduled"): frozenset({"standard"}),
    ("draft", "approval"): frozenset({"emergency"}),
    ("draft", "implementing"): frozenset({"emergency"}),
    ("submitted", "assessment"): frozenset({"normal"}),
    ("submitted", "draft"): frozenset({"normal"}),
    ("assessment", "approval"): frozenset({"normal"}),
    ("assessment", "scheduled"): frozenset({"normal"}),
    ("approval", "scheduled"): NE,
    ("approval", "rejected"): NE,
    ("approval", "assessment"): frozenset({"normal"}),
    ("approval", "draft"): frozenset({"emergency"}),
    ("rejected", "draft"): NE,
    ("scheduled", "implementing"): ALL,
    ("scheduled", "draft"): ALL,
    ("implementing", "review"): ALL,
    ("implementing", "closed"): frozenset({"standard"}),
    ("review", "closed"): ALL,
    **{
        (p, "cancelled"): ALL for p in ("draft", "submitted", "assessment", "approval", "scheduled")
    },
}
SYSTEM_PAIRS = {
    ("approval", "scheduled"),
    ("approval", "rejected"),
    ("approval", "assessment"),
    ("approval", "draft"),
}


def ctx(**kw: object) -> GuardContext:
    """A context in which every precondition holds (a normal change, assessment -> approval)."""
    base: dict[str, object] = dict(
        change_type="normal",
        phase="assessment",
        to="approval",
        is_manager=False,
        has_window=True,
        plans_complete=True,
        questionnaire_complete=True,
        impact_computed=True,
        block_conflicts=0,
        override_reason=None,
        open_tasks=0,
        force=False,
        outcome="successful",
        pir_notes_present=True,
        approval_pending=False,
        approval_rejected=False,
        cab_chosen=True,
        process_ticket=False,
        emergency_post_approval=True,
        standard_requires_review=False,
        auto_low_risk=True,
    )
    base.update(kw)
    return GuardContext(**base)  # type: ignore[arg-type]


def codes(c: GuardContext, *, system: bool = False) -> set[str]:
    return {f.code for f in check_transition(c, system=system)}


def test_public_value_lists() -> None:
    assert PHASES == (
        "draft", "submitted", "assessment", "approval", "scheduled",
        "implementing", "review", "closed", "rejected", "cancelled",
    )  # fmt: skip
    assert CHANGE_TYPES == ("standard", "normal", "emergency")
    assert OUTCOMES == ("successful", "failed", "backed_out")
    assert set(ACTIVE_PHASES) == {
        "submitted",
        "assessment",
        "approval",
        "scheduled",
        "implementing",
        "review",
    }
    assert set(FINAL_PHASES) == {"closed", "cancelled"}  # rejected is reworkable


def test_transitions_constant_matches_expected() -> None:
    assert dict(TRANSITIONS) == EXPECTED
    assert SYSTEM_ONLY == SYSTEM_PAIRS


@pytest.mark.parametrize("standard_requires_review", [False, True])
def test_transition_table_exhaustive(standard_requires_review: bool) -> None:
    for frm, to, typ in product(PHASES, PHASES, CHANGE_TYPES):
        pair_ok = typ in EXPECTED.get((frm, to), frozenset())
        if (frm, to) == ("implementing", "review") and typ == "standard":
            pair_ok = standard_requires_review
        if (frm, to) == ("implementing", "closed") and typ == "standard":
            pair_ok = not standard_requires_review
        c = ctx(
            change_type=typ,
            phase=frm,
            to=to,
            is_manager=True,
            standard_requires_review=standard_requires_review,
        )
        got = codes(c)
        if not pair_ok:
            assert got == {"transition_not_allowed"}, (frm, to, typ, got)
        elif (frm, to) in SYSTEM_PAIRS:
            assert got == {"system_only"}, (frm, to, typ, got)
        else:
            assert got == set(), (frm, to, typ, got)


def test_system_only_targets() -> None:
    for pair in SYSTEM_PAIRS:
        typ = sorted(EXPECTED[pair])[0]
        c = ctx(change_type=typ, phase=pair[0], to=pair[1])
        assert codes(c) == {"system_only"}
        assert codes(c, system=True) == set()
    # the system takes nothing else, except the emergency post-approval rejection pair
    for frm, to, typ in product(PHASES, PHASES, CHANGE_TYPES):
        if (frm, to) in SYSTEM_PAIRS and typ in EXPECTED[(frm, to)]:
            continue
        also = (frm, to, typ) == ("implementing", "review", "emergency")
        got = codes(ctx(change_type=typ, phase=frm, to=to), system=True)
        assert got == (set() if also else {"transition_not_allowed"}), (frm, to, typ)


def test_system_cancelled_outcomes_return_to_start() -> None:
    assert codes(ctx(phase="approval", to="assessment"), system=True) == set()
    assert codes(ctx(change_type="emergency", phase="approval", to="draft"), system=True) == set()
    assert codes(ctx(change_type="normal", phase="approval", to="draft"), system=True) == {
        "transition_not_allowed"
    }


def test_system_transitions_skip_preconditions() -> None:
    c = ctx(phase="approval", to="scheduled", has_window=False, block_conflicts=3)
    assert codes(c, system=True) == set()
    f19 = ctx(change_type="emergency", phase="implementing", to="review", open_tasks=4, force=False)
    assert codes(f19, system=True) == set()
    assert codes(f19) == {"tasks_open"}


def test_guards_for_approval_normal() -> None:
    assert check_transition(ctx()) == ()
    cases = {
        "window_missing": dict(has_window=False),
        "plans_missing": dict(plans_complete=False),
        "questionnaire_incomplete": dict(questionnaire_complete=False),
        "impact_pending": dict(impact_computed=False),
        "block_conflict": dict(block_conflicts=1),
        "cab_missing": dict(cab_chosen=False),
    }
    for code, kw in cases.items():
        assert codes(ctx(**kw)) == {code}, code
    everything = ctx(
        has_window=False, plans_complete=False, questionnaire_complete=False,
        impact_computed=False, block_conflicts=2, cab_chosen=False,
    )  # fmt: skip
    assert codes(everything) == set(cases)


def test_guards_for_other_targets() -> None:
    # emergency request: window, plans, CAB; no questionnaire or impact needed
    em = ctx(
        change_type="emergency", phase="draft", to="approval",
        questionnaire_complete=False, impact_computed=False,
    )  # fmt: skip
    assert check_transition(em) == ()
    assert codes(replace(em, plans_complete=False, cab_chosen=False)) == {
        "plans_missing",
        "cab_missing",
    }
    # standard: pre-approved, window and conflicts only
    std = ctx(
        change_type="standard",
        phase="draft",
        to="scheduled",
        plans_complete=False,
        cab_chosen=False,
    )
    assert check_transition(std) == ()
    assert codes(replace(std, has_window=False)) == {"window_missing"}
    # scheduled -> implementing re-checks block conflicts
    go = ctx(phase="scheduled", to="implementing")
    assert check_transition(go) == ()
    assert codes(replace(go, block_conflicts=1)) == {"block_conflict"}


def test_auto_low_risk_pair() -> None:
    c = ctx(phase="assessment", to="scheduled", cab_chosen=False)
    assert check_transition(c) == ()  # no CAB needed
    assert codes(replace(c, auto_low_risk=False)) == {"auto_approval_not_eligible"}
    assert codes(replace(c, plans_complete=False, impact_computed=False, has_window=False)) == {
        "plans_missing", "impact_pending", "window_missing",
    }  # fmt: skip
    assert codes(replace(c, change_type="emergency")) == {"transition_not_allowed"}


def test_block_conflict_override_needs_manager_and_reason() -> None:
    base = ctx(block_conflicts=2)
    assert check_transition(base)[0].overridable is True
    assert codes(replace(base, override_reason="Fenster abgestimmt")) == {"block_conflict"}
    assert codes(replace(base, is_manager=True)) == {"block_conflict"}
    assert codes(replace(base, is_manager=True, override_reason="   ")) == {"block_conflict"}
    assert codes(replace(base, is_manager=True, override_reason="Fenster abgestimmt")) == set()


def test_close_needs_outcome_and_pir() -> None:
    review = ctx(phase="review", to="closed")
    assert check_transition(review) == ()
    assert codes(replace(review, outcome=None)) == {"outcome_missing"}
    assert codes(replace(review, pir_notes_present=False)) == {"pir_missing"}
    assert codes(replace(review, outcome=None, pir_notes_present=False)) == {
        "outcome_missing", "pir_missing",
    }  # fmt: skip
    std = replace(review, change_type="standard", pir_notes_present=False)
    assert check_transition(std) == ()  # standard without PIR
    assert codes(replace(std, outcome=None)) == {"outcome_missing"}
    direct = ctx(change_type="standard", phase="implementing", to="closed", pir_notes_present=False)
    assert check_transition(direct) == ()
    assert codes(replace(direct, outcome=None)) == {"outcome_missing"}


def test_tasks_open_force_only_manager() -> None:
    for to, typ in (("review", "normal"), ("closed", "standard")):
        c = ctx(change_type=typ, phase="implementing", to=to, open_tasks=3)
        assert check_transition(c)[0].code == "tasks_open"
        assert check_transition(c)[0].overridable is True
        assert codes(replace(c, force=True)) == {"tasks_open"}  # not a manager
        assert codes(replace(c, is_manager=True)) == {"tasks_open"}  # no force
        assert codes(replace(c, is_manager=True, force=True)) == set()


def test_emergency_post_approval_paths() -> None:
    c = ctx(change_type="emergency", phase="draft", to="implementing")
    assert check_transition(c) == ()
    assert codes(replace(c, emergency_post_approval=False)) == {"transition_not_allowed"}
    assert codes(replace(c, has_window=False, block_conflicts=1)) == {
        "window_missing", "block_conflict",
    }  # fmt: skip
    # closing is blocked while the approval is still pending; a manager may force it
    close = ctx(change_type="emergency", phase="review", to="closed", approval_pending=True)
    assert codes(close) == {"approval_pending"}
    assert check_transition(close)[0].overridable is False
    # a decision still pending is never bypassed: not by force, not by force_approval
    for kw in (dict(force=True), dict(force_approval=True), dict(force=True, force_approval=True)):
        assert codes(replace(close, is_manager=True, **kw)) == {"approval_pending"}  # type: ignore[arg-type]
    # cancelled approval: a manager may force past it, only with force_approval
    cancelled = replace(close, approval_cancelled=True)
    assert check_transition(cancelled)[0].overridable is True
    assert codes(replace(cancelled, is_manager=True, force=True)) == {"approval_pending"}
    assert codes(replace(cancelled, force_approval=True)) == {"approval_pending"}  # not a manager
    assert codes(replace(cancelled, is_manager=True, force_approval=True)) == set()
    # forcing past open tasks does not force past the approval
    both = replace(cancelled, phase="implementing", to="review", open_tasks=2, is_manager=True)
    assert codes(replace(both, force=True)) == set()  # review is no close: approval not asked
    done = replace(close, phase="review", approval_cancelled=True, is_manager=True, force=True)
    assert codes(done) == {"approval_pending"}
    # the post-approval flag does not matter for the normal path
    assert allowed_targets(
        "draft",
        "emergency",
        emergency_post_approval=False,
        standard_requires_review=False,
        auto_approve_low_risk=False,
    ) == ("approval", "cancelled")
    assert allowed_targets(
        "draft",
        "emergency",
        emergency_post_approval=True,
        standard_requires_review=False,
        auto_approve_low_risk=False,
    ) == ("approval", "implementing", "cancelled")


def test_rejected_approval_forbids_successful_outcome() -> None:
    c = ctx(change_type="emergency", phase="review", to="closed", approval_rejected=True)
    assert codes(c) == {"transition_not_allowed"}
    assert codes(replace(c, outcome="failed")) == set()
    assert codes(replace(c, outcome="backed_out")) == set()
    assert codes(replace(c, is_manager=True, force=True)) == {"transition_not_allowed"}


def test_process_ticket_refused_everywhere() -> None:
    for frm, to, typ in product(PHASES, PHASES, CHANGE_TYPES):
        for system in (False, True):
            c = ctx(change_type=typ, phase=frm, to=to, process_ticket=True, is_manager=False)
            assert codes(c, system=system) == {"process_ticket"}, (frm, to, typ, system)
    # a manager may still cancel a change whose ticket became a process ticket
    cancel = ctx(phase="approval", to="cancelled", process_ticket=True, is_manager=True)
    assert codes(cancel) == set()
    assert codes(replace(cancel, to="scheduled"), system=True) == {"process_ticket"}
    assert codes(replace(cancel, to="draft", phase="scheduled")) == {"process_ticket"}


def test_allowed_targets_respects_flags() -> None:
    def at(phase: Phase, typ: ChangeType, **kw: bool) -> tuple[Phase, ...]:
        flags = dict(
            emergency_post_approval=False,
            standard_requires_review=False,
            auto_approve_low_risk=False,
        )
        flags.update(kw)
        return allowed_targets(phase, typ, **flags)

    # system-only targets are never offered
    assert at("approval", "normal") == ("cancelled",)
    assert at("approval", "emergency") == ("cancelled",)
    assert at("assessment", "normal") == ("approval", "cancelled")
    assert at("assessment", "normal", auto_approve_low_risk=True) == (
        "approval",
        "scheduled",
        "cancelled",
    )
    assert at("implementing", "standard") == ("closed",)
    assert at("implementing", "standard", standard_requires_review=True) == ("review",)
    assert at("implementing", "normal") == ("review",)
    assert at("review", "normal") == ("closed",)
    assert at("rejected", "normal") == ("draft",)
    assert at("scheduled", "normal") == ("draft", "implementing", "cancelled")  # PHASES order
    assert at("draft", "standard") == ("scheduled", "cancelled")
    assert at("draft", "normal") == ("submitted", "cancelled")
    assert at("closed", "normal") == ()
    assert at("cancelled", "emergency") == ()
    # every offered target passes the pair check of check_transition
    for phase, typ in product(PHASES, CHANGE_TYPES):
        for to in at(phase, typ, emergency_post_approval=True, auto_approve_low_risk=True):
            assert "transition_not_allowed" not in codes(ctx(change_type=typ, phase=phase, to=to))


def test_auto_low_risk_route_ignores_block_override() -> None:
    c = ctx(
        phase="assessment", to="scheduled", block_conflicts=1,
        is_manager=True, override_reason="Fenster abgestimmt",
    )  # fmt: skip
    failure = check_transition(c)
    assert [f.code for f in failure] == ["block_conflict"]
    assert failure[0].overridable is False
    # the manager can still take the CAB route with the override
    assert check_transition(replace(c, to="approval")) == ()


def test_reopenable_phases() -> None:
    assert frozenset(PHASES) - FINAL_PHASES == REOPENABLE_PHASES
    assert "rejected" in REOPENABLE_PHASES and "draft" in REOPENABLE_PHASES
    assert not {"closed", "cancelled"} & REOPENABLE_PHASES


def test_process_ticket_exit_from_every_non_final_phase() -> None:
    for phase, typ in product(PHASES, CHANGE_TYPES):
        c = ctx(change_type=typ, phase=phase, to="cancelled", process_ticket=True, is_manager=True)
        expected = set() if phase in REOPENABLE_PHASES else {"transition_not_allowed"}
        assert codes(c) == expected, (phase, typ)
        assert codes(replace(c, is_manager=False)) == {"process_ticket"}
        assert codes(c, system=True) == {"process_ticket"}


def test_allowed_targets_needs_explicit_auto_flag() -> None:
    with pytest.raises(TypeError):
        allowed_targets(  # type: ignore[call-arg]
            "assessment", "normal", emergency_post_approval=False, standard_requires_review=False
        )
