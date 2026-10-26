import pytest
from pydantic import ValidationError

from tiqora.approvals.contract import (
    APPROVAL_EVENTS,
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    ERROR_STATUS,
    PORTAL_WRITE_USER_ID,
    Actor,
    ApprovalDefinition,
    ApprovalError,
    ApprovalOutcome,
    Principal,
    StepSpec,
)
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID

ENTRY = {"kind": "user", "ref": "970101"}


def test_principal_keys_round_trip() -> None:
    assert Principal.agent(12).key == "agent:12"
    assert Principal.customer("kunde1@example.org").key == "customer:kunde1@example.org"
    assert Principal.parse("agent:12") == Principal.agent(12)
    assert Principal.parse("customer:a:b") == Principal.customer(
        "a:b"
    )  # split on the first ":" only
    for bad in ("system", "agent:x", "agent:", "customer:", "foo:1", "", "agent:0", "agent:-3"):
        with pytest.raises(ValueError):
            Principal.parse(bad)


def test_actor_write_user() -> None:
    assert Actor.agent(5).write_user_id == 5 and Actor.agent(5).key == "agent:5"
    assert (
        Actor.customer("k@example.org").write_user_id
        == PORTAL_WRITE_USER_ID
        == PORTAL_SYSTEM_USER_ID
    )
    assert Actor.customer("k@example.org").key == "customer:k@example.org"
    assert Actor.system().key == "system" and Actor.system().principal is None
    assert Actor.system().write_user_id == 1


def test_step_spec_validation() -> None:
    StepSpec(name="CAB", rule="quorum", quorum=2, approvers=[ENTRY])
    with pytest.raises(ValidationError):
        StepSpec(name="CAB", rule="quorum", approvers=[ENTRY])  # quorum missing
    with pytest.raises(ValidationError):
        StepSpec(
            name="CAB", rule="any", quorum=2, approvers=[ENTRY]
        )  # quorum only with rule quorum
    with pytest.raises(ValidationError):
        StepSpec(name="X", on_timeout="escalate", approvers=[ENTRY])  # escalation missing
    with pytest.raises(ValidationError):
        StepSpec(
            name="X", on_timeout="notify", escalation=[ENTRY], approvers=[ENTRY]
        )  # escalation only on escalate
    StepSpec(name="X", on_timeout="escalate", escalation=[ENTRY], approvers=[ENTRY])
    with pytest.raises(ValidationError):
        StepSpec(name="X", approvers=[])  # at least one entry
    with pytest.raises(ValidationError):
        StepSpec(name="X", approvers=[ENTRY], unknown=1)  # type: ignore[call-arg]  # extra forbidden


def test_step_spec_defaults() -> None:
    s = StepSpec(name="X", approvers=[ENTRY])
    assert (s.stage, s.rule, s.quorum, s.due_hours, s.on_timeout, s.on_no_approver) == (
        1,
        "any",
        None,
        None,
        "notify",
        "reject",
    )
    assert s.escalation == [] and s.allow_delegation is True and s.allow_email is True


def test_definition_stage_numbers_contiguous() -> None:
    e = [ENTRY]
    ApprovalDefinition(
        steps=[
            StepSpec(name="a", stage=1, approvers=e),
            StepSpec(name="b", stage=1, approvers=e),
            StepSpec(name="c", stage=2, approvers=e),
        ]
    )
    with pytest.raises(ValidationError):
        ApprovalDefinition(
            steps=[
                StepSpec(name="a", stage=1, approvers=e),
                StepSpec(name="b", stage=3, approvers=e),
            ]
        )
    with pytest.raises(ValidationError):
        ApprovalDefinition(steps=[StepSpec(name="a", stage=2, approvers=e)])


def test_error_codes_have_status() -> None:
    err = ApprovalError("self_approval_forbidden", ERROR_STATUS["self_approval_forbidden"])
    assert err.code == "self_approval_forbidden" and err.http_status == 403 and err.detail is None
    assert set(ERROR_STATUS) >= {
        "approval_not_found",
        "link_used",
        "link_expired",
        "link_invalid",
        "not_an_approver",
    }
    assert ERROR_STATUS["invalid_definition"] == 422 and ERROR_STATUS["already_decided"] == 409


def test_event_names_are_not_internal_or_legacy() -> None:
    from tiqora.domain.ticket_write_service import LEGACY_NOTIFICATION_EVENTS

    assert len(APPROVAL_EVENTS) == 5
    assert all(n.startswith("TiqoraApproval") for n in APPROVAL_EVENTS)
    assert not set(APPROVAL_EVENTS) & set(LEGACY_NOTIFICATION_EVENTS)


def test_extension_points_keys() -> None:
    assert (
        APPROVAL_SUBJECT_TYPES.key == "core.approval_subject_types"
        and not APPROVAL_SUBJECT_TYPES.sdk_stable
    )
    assert APPROVER_RESOLVERS.key == "core.approver_resolvers" and not APPROVER_RESOLVERS.sdk_stable


def test_outcome_comment_is_last_optional_field() -> None:
    from dataclasses import fields
    from datetime import datetime

    assert fields(ApprovalOutcome)[-1].name == "comment"
    out = ApprovalOutcome(
        1, 2, "ticket", None, "default", "rejected", "timeout", datetime(2026, 1, 1)
    )
    assert out.comment is None
