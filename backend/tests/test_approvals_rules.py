import pytest

from tiqora.approvals.contract import ApprovalDefinition, ApprovalError, StepSpec
from tiqora.approvals.rules import (
    VoterState,
    evaluate_step,
    next_stage,
    quorum_reachable,
    request_result,
    required_votes,
    validate_definition,
)

P, A, R = VoterState("pending"), VoterState("approved"), VoterState("rejected")


@pytest.mark.parametrize(
    ("rule", "quorum", "voters", "expected"),
    [
        ("any", None, [P, P], "pending"),
        ("any", None, [P, A], "approved"),
        ("any", None, [R, P], "rejected"),
        ("any", None, [A, R], "approved"),  # the first decision counts
        ("all", None, [A, P], "pending"),
        ("all", None, [A, A], "approved"),
        ("all", None, [A, R, P], "rejected"),
        ("quorum", 2, [A, P, P], "pending"),
        ("quorum", 2, [A, A, P], "approved"),
        ("quorum", 2, [A, R, R], "rejected"),
        ("quorum", 2, [R, P, P], "pending"),
        ("majority", None, [A, A, P], "approved"),
        ("majority", None, [A, R, P], "pending"),
        ("majority", None, [R, R, P], "rejected"),
        ("majority", None, [A, P, P, P], "pending"),
        ("majority", None, [A, A, R, R], "rejected"),  # k=4 -> n=3, approved 2 + pending 0 < 3
        ("all", None, [], "pending"),
    ],
)
def test_evaluate_step(rule, quorum, voters, expected) -> None:  # type: ignore[no-untyped-def]
    assert evaluate_step(rule, quorum, voters) == expected


def test_regular_decisive_voter_stays_in_the_rule() -> None:
    """An approver an escalation made decisive counts in the rule AND can decide alone."""
    up = {"decisive": True, "regular": True}
    # rule all: the other regular voter alone does not approve while the upgraded one is pending
    assert evaluate_step("all", None, [A, VoterState("pending", **up)]) == "pending"
    assert evaluate_step("all", None, [VoterState("approved", **up), P]) == "approved"
    assert evaluate_step("all", None, [VoterState("rejected", **up), A]) == "rejected"
    # quorum 2 of 2: one approval plus the pending upgraded voter must not flip to rejected
    assert evaluate_step("quorum", 2, [A, VoterState("pending", **up)]) == "pending"
    # an escalation-only voter (regular=False) stays outside the rule
    assert (
        evaluate_step("all", None, [A, VoterState("pending", decisive=True, regular=False)])
        == "approved"
    )


def test_decisive_voter_decides_alone() -> None:
    assert evaluate_step("all", None, [P, P, VoterState("approved", decisive=True)]) == "approved"
    assert evaluate_step("quorum", 3, [A, A, VoterState("rejected", decisive=True)]) == "rejected"
    # a pending decisive voter never blocks: the non-decisive voters decide
    assert evaluate_step("all", None, [A, VoterState("pending", decisive=True)]) == "approved"
    assert evaluate_step("all", None, [R, VoterState("pending", decisive=True)]) == "rejected"
    # no non-decisive voter and the decisive one has not voted
    assert evaluate_step("majority", None, [VoterState("pending", decisive=True)]) == "pending"
    # first decisive vote in caller order wins
    first = VoterState("rejected", decisive=True)
    assert evaluate_step("any", None, [first, VoterState("approved", decisive=True)]) == "rejected"


def test_majority_counts_only_non_decisive_voters() -> None:
    # k = 3 regular voters -> n = 2; the pending decisive voter is not counted
    voters = [A, A, P, VoterState("pending", decisive=True)]
    assert evaluate_step("majority", None, voters) == "approved"


def test_quorum_rule_without_quorum_raises() -> None:
    with pytest.raises(ValueError):
        evaluate_step("quorum", None, [A, P])


def test_required_votes_and_reachability() -> None:
    assert required_votes("all", None, 3) == 3 and required_votes("any", None, 3) == 1
    assert required_votes("quorum", 2, 3) == 2 and required_votes("majority", None, 4) == 3
    assert required_votes("all", None, 0) is None
    assert quorum_reachable("quorum", 3, 2) is False and quorum_reachable("all", None, 0) is False
    assert quorum_reachable("any", None, 1) is True and quorum_reachable("quorum", 2, 2) is True


def test_stage_progression() -> None:
    assert next_stage([(1, "approved"), (1, "approved"), (2, "waiting")]) == 2
    assert next_stage([(1, "approved"), (3, "waiting"), (2, "waiting")]) == 2
    assert next_stage([(1, "approved"), (2, "approved")]) is None
    assert request_result([(1, "approved"), (2, "rejected"), (3, "cancelled")]) == "rejected"
    assert request_result([(1, "approved"), (2, "active")]) == "pending"
    assert request_result([(1, "approved"), (2, "approved")]) == "approved"
    assert request_result([]) == "pending"


def test_validate_definition_semantics() -> None:
    group = {"kind": "group", "ref": "970201"}
    users = [{"kind": "user", "ref": "970103"}, {"kind": "user", "ref": "970104"}]
    # a group expands later, so a quorum above the entry count is fine
    validate_definition(
        ApprovalDefinition(steps=[StepSpec(name="a", rule="quorum", quorum=5, approvers=[group])])
    )
    validate_definition(
        ApprovalDefinition(steps=[StepSpec(name="a", rule="quorum", quorum=2, approvers=users)])
    )
    # only individual entries and quorum > their count -> invalid_definition
    with pytest.raises(ApprovalError) as exc:
        validate_definition(
            ApprovalDefinition(steps=[StepSpec(name="a", rule="quorum", quorum=3, approvers=users)])
        )
    assert exc.value.code == "invalid_definition" and exc.value.http_status == 422
    with pytest.raises(ApprovalError) as dup:  # duplicate step names
        validate_definition(
            ApprovalDefinition(
                steps=[
                    StepSpec(name="a", approvers=users),
                    StepSpec(name="a", stage=2, approvers=users),
                ]
            )
        )
    assert dup.value.code == "invalid_definition"
    dup_users = [{"kind": "user", "ref": "970103"}] * 2  # one person twice is one voter
    with pytest.raises(ApprovalError):
        validate_definition(
            ApprovalDefinition(
                steps=[StepSpec(name="a", rule="quorum", quorum=2, approvers=dup_users)]
            )
        )
    # 10 steps in one stage is the limit and fine
    validate_definition(
        ApprovalDefinition(steps=[StepSpec(name=f"s{i}", approvers=users) for i in range(10)])
    )
