from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests import approval_port_contract
from tests._change_fixtures import FakeApprovalPort, use_fake_approval_port  # noqa: F401
from tiqora.approvals.contract import Actor, ApprovalDefinition, ApproverEntry, Principal, StepSpec
from tiqora.itsm.change import approval_port
from tiqora.itsm.change.approval_port import (
    APPROVAL_KEY,
    SUBJECT_TYPE,
    UnavailableApprovalPort,
    get_approval_port,
    set_approval_port_for_tests,
)
from tiqora.itsm.change.errors import ChangeError

SESSION = cast(AsyncSession, cast(Any, None))
DEFINITION = ApprovalDefinition(
    steps=[StepSpec(name="CAB", approvers=[ApproverEntry(kind="user", ref="7")])]
)


async def test_fake_port_contract() -> None:
    await approval_port_contract.run(
        FakeApprovalPort, SESSION, ticket_id=11, definition=DEFINITION, user_id=7
    )


async def test_fake_port_records_calls_and_decides() -> None:
    port = FakeApprovalPort()
    snap = await port.request(
        SESSION,
        ticket_id=5,
        round=3,
        title="t",
        description="d",
        definition=DEFINITION,
        actor=Actor.system(),
        requester=Principal.agent(7),
        allow_self_approval=False,
    )
    assert [(r.ticket_id, r.round, r.allow_self_approval) for r in port.requests] == [(5, 3, False)]
    assert port.auto_decisions == []
    decided = port.decide(snap.approval_id, "rejected", "timeout")
    assert (decided.status, decided.reason) == ("rejected", "timeout")
    # a final status does not change again
    assert port.decide(snap.approval_id, "approved").status == "rejected"


def test_constants() -> None:
    assert (APPROVAL_KEY, SUBJECT_TYPE) == ("change_cab", "itsm.change")


async def test_unavailable_port_raises() -> None:
    set_approval_port_for_tests(None)
    port = get_approval_port()
    assert isinstance(port, UnavailableApprovalPort)
    with pytest.raises(ChangeError) as exc:
        await port.request(
            SESSION,
            ticket_id=1,
            round=1,
            title="t",
            description="d",
            definition=DEFINITION,
            actor=Actor.system(),
            requester=Principal.agent(7),
            allow_self_approval=False,
        )
    assert exc.value.error_code == "approvals_unavailable"
    assert exc.value.http_status == 409
    for call in (
        port.auto_decision(SESSION, ticket_id=1, round=1, title="t", actor=Actor.system()),
        port.cancel(SESSION, approval_id=1, actor=Actor.system(), reason="x"),
        port.snapshot(SESSION, approval_id=1),
    ):
        with pytest.raises(ChangeError):
            await call


def test_registered_port_is_returned_and_reset() -> None:
    fake = FakeApprovalPort()
    set_approval_port_for_tests(fake)
    try:
        assert get_approval_port() is fake
    finally:
        set_approval_port_for_tests(None)
    assert isinstance(get_approval_port(), UnavailableApprovalPort)
    assert approval_port._port is None


async def test_use_fake_fixture(
    use_fake_approval_port: FakeApprovalPort,  # noqa: F811
) -> None:
    assert get_approval_port() is use_fake_approval_port
