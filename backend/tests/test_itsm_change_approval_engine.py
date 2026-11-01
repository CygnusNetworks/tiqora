"""CAB decisions through the real approval engine (Task 17): adapter, outcome listener, CAB.

Seed band 947500-947599: CABs 947501-947506, voters 947511-947514, the CAB group 947521, the CAB
role 947531, the committed lock-order world 947550-947559. Everything else comes from the
savepoint world of ``tests._change_fixtures`` (``savepoints=True``): the approval sweep and the
hook job commit and roll back on the test session, which only ever touches a savepoint, and the
outer transaction is rolled back at the end. Outcomes travel the production way: the engine's
decision enqueues ``core.approval_hook``, :func:`_hook` runs that job (``run_hook_now``), and the
job calls the ``itsm.change`` subject type's ``on_decided``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests import approval_port_contract
from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    Q_A,
    SERVICE,
    SERVICE_B,
    U_FULL,
    U_MANAGER,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
    make_cab,
)
from tests._itsm_mode_helpers import make_factory, plain_url
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.approvals_consumer_kit import (
    approve_all,
    assert_subject_registered,
    race_consumer_and_vote,
    reject_first,
    run_hook_now,
    shared_session_factory,
    touch_ticket,
)
from tiqora.approvals import lists as approval_lists
from tiqora.approvals import service as approval_service
from tiqora.approvals import sweep
from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalOutcome,
    ApproverEntry,
    Principal,
    StepSpec,
)
from tiqora.approvals.models import TiqoraApproval, TiqoraApprovalApprover
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.itsm.change import transitions as transitions_module
from tiqora.itsm.change.approval_port import (
    APPROVAL_KEY,
    SUBJECT_TYPE,
    EngineApprovalPort,
    describe_change,
    get_approval_port,
    set_approval_port_for_tests,
)
from tiqora.itsm.change.cab import CabMemberIn, list_cabs, resolved_members, set_members
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeService,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.service import ChangeCreateIn, ChangeView, create_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.transitions import TransitionIn, transition
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

CAB, ECAB, CAB_GROUPS, CAB_QUORUM, CAB_SELF, CAB_OWNERS = (
    947501,
    947502,
    947503,
    947504,
    947505,
    947506,
)
V1, V2, V3, V4 = 947511, 947512, 947513, 947514
G_CAB = 947521
R_CAB = 947531
WINDOW = (datetime(2026, 11, 2, 8, 0, 0), datetime(2026, 11, 2, 10, 0, 0))
_T = datetime(2026, 10, 1)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    set_approval_port_for_tests(None)  # the real engine adapter
    async with change_world(request, str(request.param), savepoints=True) as world:
        await _seed_voters(world.session)
        await world.session.commit()
        yield world


# --- helpers ---------------------------------------------------------------------------------


async def _seed_voters(session: AsyncSession) -> None:
    for uid in (V1, V2, V3, V4):
        await session.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, :l, 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
            ),
            {"i": uid, "l": f"chg94-voter{uid}", "t": _T},
        )
    await session.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'chg94-cab', 1, :t, 1, :t, 1)"
        ),
        {"id": G_CAB, "t": _T},
    )
    for uid in (V1, V2):
        await session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"u": uid, "g": G_CAB, "t": _T},
        )
    await session.execute(
        text(
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'chg94-cab-role', 1, :t, 1, :t, 1)"
        ),
        {"id": R_CAB, "t": _T},
    )
    await session.execute(
        text(
            "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
            " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
        ),
        {"u": V3, "r": R_CAB, "t": _T},
    )


async def _settings(w: ChangeWorld, **values: Any) -> None:
    await save_section_values(
        w.session, ITSM_CHANGE_SECTION, values, secret_key=APP_SETTINGS.secret_key
    )


async def _cabs(w: ChangeWorld) -> None:
    three = [("user", str(V1)), ("user", str(V2)), ("user", str(V3))]
    await make_cab(w.session, cab_id=CAB, rule="majority", members=three)
    await make_cab(w.session, cab_id=ECAB, rule="any", members=[("user", str(V4))])
    await make_cab(
        w.session,
        cab_id=CAB_GROUPS,
        rule="all",
        members=[("group", str(G_CAB)), ("role", str(R_CAB))],
    )
    # stored past the admin check (e.g. a member erased since): quorum 2, one person left
    await make_cab(
        w.session, cab_id=CAB_QUORUM, rule="quorum", quorum=2, members=[("user", str(V1))]
    )
    # nobody but the requester: the engine finds no approver
    await make_cab(w.session, cab_id=CAB_SELF, rule="any", members=[("user", str(U_FULL))])
    await make_cab(
        w.session, cab_id=CAB_OWNERS, rule="all", members=[("resolver", "itsm.service_owner")]
    )


async def _create(w: ChangeWorld, change_type: str = "normal", **fields: Any) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": change_type, "queue_id": Q_A, **fields}
    )
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    return view.record.ticket_id


async def _row(w: ChangeWorld, tid: int) -> TiqoraItsmChange:
    row = (
        await w.session.execute(
            select(TiqoraItsmChange)
            .where(TiqoraItsmChange.ticket_id == tid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return row


async def _ready(w: ChangeWorld, tid: int, *, cab_id: int | None, phase: str | None = None) -> None:
    """Plans, window, CAB and a current impact set: everything the guards ask for."""
    row = await _row(w, tid)
    row.implementation_plan = "Switch tauschen"
    row.backout_plan = "Alten Switch wieder einbauen"
    row.planned_start, row.planned_end = WINDOW
    row.cab_id = cab_id
    row.impact_computed_at = max(row.impact_requested_at or utc_now(), utc_now()) + timedelta(
        seconds=5
    )
    if phase is not None:
        row.phase = phase
    await w.session.flush()


async def _go(
    w: ChangeWorld, tid: int, to: str, user_id: int = U_FULL, **fields: Any
) -> ChangeView:
    return await transition(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        data=TransitionIn.model_validate({"to": to, **fields}),
        user_id=user_id,
        session_factory=w.factory,
    )


async def _to_approval(w: ChangeWorld, cab_id: int = CAB, **settings: Any) -> tuple[int, int]:
    """A normal change in Bewertung moved to Freigabe; returns (ticket id, approval id)."""
    await _cabs(w)
    if settings:
        await _settings(w, **settings)
    tid = await _create(w)
    await _ready(w, tid, cab_id=cab_id, phase="assessment")
    await _go(w, tid, "approval")
    await w.session.commit()
    row = await _row(w, tid)
    assert row.phase == "approval" and row.approval_id is not None
    return tid, int(row.approval_id)


async def _hook(w: ChangeWorld, approval_id: int) -> dict[str, Any]:
    """The ``core.approval_hook`` job the decision queued, run the way the worker does."""
    await w.session.commit()
    info = await approval_service.get_info(w.session, approval_id)
    job = (
        await w.session.execute(
            select(TiqoraJob).where(
                TiqoraJob.kind == "core.approval_hook",
                TiqoraJob.dedupe_key == f"approval:{approval_id}:{info.status}",
            )
        )
    ).scalar_one()
    assert job.status == "queued"
    return await run_hook_now(shared_session_factory(w.session), approval_id, info.status)


async def _hook_jobs(w: ChangeWorld, approval_id: int) -> list[str]:
    rows = await w.session.execute(
        select(TiqoraJob.dedupe_key).where(
            TiqoraJob.kind == "core.approval_hook",
            TiqoraJob.dedupe_key.like(f"approval:{approval_id}:%"),
        )
    )
    return [str(r[0]) for r in rows]


async def _events(w: ChangeWorld, tid: int) -> list[TiqoraItsmChangeEvent]:
    stmt = (
        select(TiqoraItsmChangeEvent)
        .where(TiqoraItsmChangeEvent.ticket_id == tid)
        .order_by(TiqoraItsmChangeEvent.id)
        .execution_options(populate_existing=True)
    )
    return list((await w.session.execute(stmt)).scalars())


async def _phases(w: ChangeWorld, tid: int) -> list[tuple[str | None, str | None]]:
    return [(e.from_phase, e.to_phase) for e in await _events(w, tid) if e.event == "phase"]


async def _approvers(w: ChangeWorld, approval_id: int) -> set[str]:
    rows = await w.session.execute(
        select(TiqoraApprovalApprover.principal).where(
            TiqoraApprovalApprover.approval_id == approval_id
        )
    )
    return {str(r[0]) for r in rows}


async def _vote(w: ChangeWorld, approval_id: int, user_id: int, decision: str) -> str:
    result = await approval_service.decide(
        w.session,
        approval_id,
        actor=Actor.agent(user_id),
        decision=decision,  # type: ignore[arg-type]
        channel="api",
    )
    return result.info.status


async def _standard_template(w: ChangeWorld) -> int:
    now = utc_now()
    tpl = TiqoraItsmChangeTemplate(
        name="chg94 Standard-Change 17",
        change_type="standard",
        is_standard=True,
        valid=True,
        defaults="{}",
        tasks="[]",
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    w.session.add(tpl)
    await w.session.flush()
    return tpl.id


async def _ticket_state(w: ChangeWorld, tid: int) -> str:
    return str(
        (
            await w.session.execute(
                text(
                    "SELECT ts.name FROM ticket t JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " WHERE t.id = :t"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )


async def _sweep(w: ChangeWorld, now: datetime) -> dict[str, int]:
    await w.session.commit()
    return await sweep.run_approvals_sweep(
        settings=APP_SETTINGS,
        session_factory=shared_session_factory(w.session),  # type: ignore[arg-type]
        now=now,
    )


# --- the adapter -----------------------------------------------------------------------------


async def test_real_adapter_passes_port_contract(w: ChangeWorld) -> None:
    assert isinstance(get_approval_port(), EngineApprovalPort)
    tid = await _create(w)
    definition = ApprovalDefinition(
        steps=[StepSpec(name="CAB", approvers=[ApproverEntry(kind="user", ref=str(V1))])]
    )
    await approval_port_contract.run(
        EngineApprovalPort, w.session, ticket_id=tid, definition=definition, user_id=U_FULL
    )
    # the round travels in subject_ref; the set belongs to the change subject type
    approvals = (
        (await w.session.execute(select(TiqoraApproval).where(TiqoraApproval.ticket_id == tid)))
        .scalars()
        .all()
    )
    assert {(a.subject_type, a.approval_key) for a in approvals} == {(SUBJECT_TYPE, APPROVAL_KEY)}
    assert sorted(a.subject_ref for a in approvals) == ["1", "2"]


async def test_engine_errors_become_change_errors(w: ChangeWorld) -> None:
    """Every engine error is a ChangeError with the engine code under ``engine_code``; a set
    of another subject type is no change approval (snapshot None)."""
    tid = await _create(w)
    port = EngineApprovalPort()
    bad = ApprovalDefinition(
        steps=[StepSpec(name="CAB", approvers=[ApproverEntry(kind="resolver", ref="chg94.none")])]
    )
    with pytest.raises(ChangeError) as exc:
        await port.request(
            w.session,
            ticket_id=tid,
            round=1,
            title="Change 1: Switch-Tausch",
            description="",
            definition=bad,
            actor=Actor.agent(U_FULL),
            requester=Principal.agent(U_FULL),
            allow_self_approval=False,
        )
    assert (exc.value.error_code, exc.value.http_status) == ("approval_request_failed", 409)
    assert exc.value.extra["engine_code"] == "unknown_resolver"
    assert exc.value.detail["code"] == "approval_request_failed"

    other = await approval_service.request_approval(
        w.session,
        ticket_id=tid,
        subject_type="ticket",
        title="Freigabe chg94",
        actor=Actor.agent(U_FULL),
        definition=ApprovalDefinition(
            steps=[StepSpec(name="S", approvers=[ApproverEntry(kind="user", ref=str(V1))])]
        ),
    )
    assert await port.snapshot(w.session, approval_id=other.approval_id) is None
    # cancelling a set that is gone or decided is no error
    await port.cancel(w.session, approval_id=2_000_000_000, actor=Actor.system(), reason="x")


# --- outcomes through the engine and its hook job ----------------------------------------------


async def test_cab_vote_majority_moves_change_to_scheduled(w: ChangeWorld) -> None:
    tid, aid = await _to_approval(w)
    # the owner/creator (U_FULL) is not on the CAB; V1-V3 vote individually
    assert await _approvers(w, aid) == {f"agent:{V1}", f"agent:{V2}", f"agent:{V3}"}
    assert await _vote(w, aid, V1, "approved") == "pending"
    await w.session.commit()
    assert await _hook_jobs(w, aid) == []
    assert (await _row(w, tid)).phase == "approval"

    assert await _vote(w, aid, V2, "approved") == "approved"
    result = await _hook(w, aid)
    assert result["hook"] == "called"
    row = await _row(w, tid)
    assert (row.phase, row.approval_via) == ("scheduled", "cab")
    assert row.approved_at is not None
    assert (await _phases(w, tid))[-2:] == [("assessment", "approval"), ("approval", "scheduled")]
    outcome = [e for e in await _events(w, tid) if e.event == "approval_outcome"]
    assert len(outcome) == 1 and '"status": "approved"' in (outcome[0].detail or "")


@pytest.mark.parametrize("section", [ITSM_CHANGE_SECTION, APPROVALS_SECTION])
async def test_decided_vote_finishes_its_hook_while_a_switch_is_off(
    w: ChangeWorld, section: Any
) -> None:
    """The outcome is not a person's step: ``on_cab_decided`` passes no module gate, so a decision
    made just before the switch still moves the change once its job runs."""
    tid, aid = await _to_approval(w)
    assert await _vote(w, aid, V1, "approved") == "pending"
    assert await _vote(w, aid, V2, "approved") == "approved"
    await save_section_values(
        w.session, section, {"enabled": False}, secret_key=APP_SETTINGS.secret_key
    )
    await w.session.commit()
    result = await _hook(w, aid)
    assert result["hook"] == "called"
    assert (await _row(w, tid)).phase == "scheduled"


async def test_rejection_moves_change_to_rejected(w: ChangeWorld) -> None:
    tid, aid = await _to_approval(w)
    before = await _ticket_state(w, tid)
    assert await _vote(w, aid, V1, "rejected") == "pending"
    assert await _vote(w, aid, V3, "rejected") == "rejected"
    await _hook(w, aid)
    row = await _row(w, tid)
    assert row.phase == "rejected"
    assert (await _phases(w, tid))[-1] == ("approval", "rejected")
    assert await _ticket_state(w, tid) == before  # F11: Abgelehnt leaves the ticket state


async def test_timeout_reject_when_configured(w: ChangeWorld) -> None:
    """F2/CX7: ``approval_timeout="reject"`` lets the engine decide at the deadline."""
    tid, aid = await _to_approval(w, approval_timeout="reject", approval_due_hours=1)
    counts = await _sweep(w, utc_now() + timedelta(hours=2))
    assert counts["timeout_decided"] == 1
    info = await approval_service.get_info(w.session, aid)
    assert (info.status, info.reason) == ("rejected", "timeout")
    await _hook(w, aid)
    row = await _row(w, tid)
    assert row.phase == "rejected"
    (outcome,) = [e for e in await _events(w, tid) if e.event == "approval_outcome"]
    assert '"reason": "timeout"' in (outcome.detail or "")


async def test_timeout_notify_keeps_change_in_approval(w: ChangeWorld) -> None:
    """P2: the default ``notify`` only reports the deadline; the CAB still decides."""
    tid, aid = await _to_approval(w, approval_due_hours=1)
    counts = await _sweep(w, utc_now() + timedelta(hours=2))
    assert counts["timeout_decided"] == 0
    assert counts["overdue"] == 1
    assert (await approval_service.get_info(w.session, aid)).status == "pending"
    assert await _hook_jobs(w, aid) == []
    assert (await _row(w, tid)).phase == "approval"


async def test_group_and_role_members_resolved_by_engine(w: ChangeWorld) -> None:
    tid, aid = await _to_approval(w, cab_id=CAB_GROUPS)
    # group: every agent with rw on it; role: its members - one voter each, no names stored
    assert await _approvers(w, aid) == {f"agent:{V1}", f"agent:{V2}", f"agent:{V3}"}
    shown = {m.member_kind: m for m in await resolved_members(w.session, CAB_GROUPS)}
    assert (shown["group"].count, shown["role"].count) == (2, 1)  # the CAB page counts the same
    info = await approve_all(w.session, aid)
    assert info.status == "approved"
    await _hook(w, aid)
    assert (await _row(w, tid)).phase == "scheduled"


async def test_service_owner_resolver(w: ChangeWorld) -> None:
    """F14: ``itsm.service_owner`` = owners of the ticket's service and the extra services."""
    await _cabs(w)
    tid = await _create(w, service_id=SERVICE, service_ids=[SERVICE_B])
    for service_id, owner in ((SERVICE, V1), (SERVICE_B, V2)):
        w.session.add(
            TiqoraItsmServiceMeta(
                service_id=service_id, owner_user_id=owner, change_time=_T, change_by=1
            )
        )
    await w.session.flush()
    extra = await w.session.execute(
        select(TiqoraItsmChangeService.service_id).where(TiqoraItsmChangeService.ticket_id == tid)
    )
    assert [int(r[0]) for r in extra] == [SERVICE_B]
    await _ready(w, tid, cab_id=CAB_OWNERS, phase="assessment")
    await _go(w, tid, "approval")
    aid = int((await _row(w, tid)).approval_id or 0)
    assert await _approvers(w, aid) == {f"agent:{V1}", f"agent:{V2}"}


async def test_no_approvers_rejects_through_hook(w: ChangeWorld) -> None:
    """N3: a CAB that resolves to nobody (only the requester) is rejected at once."""
    tid, aid = await _to_approval(w, cab_id=CAB_SELF)
    info = await approval_service.get_info(w.session, aid)
    assert (info.status, info.reason) == ("rejected", "no_approvers")
    await _hook(w, aid)
    assert (await _row(w, tid)).phase == "rejected"
    (outcome,) = [e for e in await _events(w, tid) if e.event == "approval_outcome"]
    assert '"reason": "no_approvers"' in (outcome.detail or "")


async def test_quorum_out_of_reach_is_a_clean_409(w: ChangeWorld) -> None:
    """Task 13 carry: a stored CAB whose quorum nobody can reach any more -> 409, nothing kept."""
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid, cab_id=CAB_QUORUM, phase="assessment")
    with pytest.raises(ChangeError) as exc:
        await _go(w, tid, "approval")
    assert (exc.value.error_code, exc.value.http_status) == ("approval_request_failed", 409)
    assert exc.value.extra["engine_code"] == "invalid_definition"
    row = await _row(w, tid)
    assert (row.phase, row.approval_id, row.approval_round) == ("assessment", None, 0)
    assert not [e for e in await _events(w, tid) if e.event == "approval_requested"]


ENGINE_PATHS = (
    "approved",
    "rejected",
    "timeout",
    "cancelled",
    "cancelled_emergency",
    "no_approvers",
    "post_rejected",
    "agent_cancel",
)


@pytest.mark.parametrize("path", ENGINE_PATHS)
async def test_every_engine_outcome_writes_one_phase_event(w: ChangeWorld, path: str) -> None:
    """X-1 (engine half of Task 12's test): every outcome delivered by the real ``on_decided``
    writes exactly one ``phase`` row (or none, when the change itself ended the round), and a
    second delivery of the same outcome writes nothing (the hook job is retried up to 5 times)."""
    await _cabs(w)
    await _settings(
        w,
        emergency_cab_id=ECAB,
        emergency_post_approval=True,
        approval_timeout="reject",
        approval_due_hours=1,
    )
    emergency = path in ("cancelled_emergency", "post_rejected")
    tid = await _create(w, "emergency" if emergency else "normal")
    if emergency:
        await _ready(w, tid, cab_id=None)
        await _go(w, tid, "implementing" if path == "post_rejected" else "approval")
    else:
        await _ready(w, tid, cab_id=CAB_SELF if path == "no_approvers" else CAB, phase="assessment")
        await _go(w, tid, "approval")
    await w.session.commit()
    aid = int((await _row(w, tid)).approval_id or 0)
    before = {e.id for e in await _events(w, tid)}

    expected: list[tuple[str, str]]
    if path == "approved":
        await approve_all(w.session, aid)
        expected = [("approval", "scheduled")]
    elif path in ("rejected", "post_rejected"):
        while (await approval_service.get_info(w.session, aid)).status == "pending":
            await reject_first(w.session, aid)
        expected = [("implementing", "review")] if emergency else [("approval", "rejected")]
    elif path == "timeout":
        await _sweep(w, utc_now() + timedelta(hours=2))
        expected = [("approval", "rejected")]
    elif path in ("cancelled", "cancelled_emergency"):
        # an admin withdraws the request on the approvals page
        await approval_service.cancel_approval(
            w.session, aid, actor=Actor.agent(1), check_permission=False
        )
        expected = [("approval", "draft" if emergency else "assessment")]
    elif path == "no_approvers":
        expected = [("approval", "rejected")]
    else:  # agent_cancel: the change cancels its own round; the outcome changes nothing more
        await _go(w, tid, "cancelled", U_MANAGER)
        expected = [("approval", "cancelled")]
    await _hook(w, aid)

    new = [e for e in await _events(w, tid) if e.id not in before]
    assert [(e.from_phase, e.to_phase) for e in new if e.event == "phase"] == expected
    outcomes = [e for e in new if e.event == "approval_outcome"]
    assert len(outcomes) == (0 if path == "agent_cancel" else 1)

    # second delivery: the job retried after its commit was lost, or a duplicate job
    info = await approval_service.get_info(w.session, aid)
    subject = assert_subject_registered(SUBJECT_TYPE)
    assert subject.on_decided is not None
    await subject.on_decided(
        w.session,
        ApprovalOutcome(
            approval_id=aid,
            ticket_id=tid,
            subject_type=SUBJECT_TYPE,
            subject_ref=str((await _row(w, tid)).approval_round),
            approval_key=APPROVAL_KEY,
            status=info.status,  # type: ignore[arg-type]
            reason=info.reason,
            decided_at=info.decided_at or utc_now(),
        ),
    )
    await w.session.flush()
    again = [e for e in await _events(w, tid) if e.id not in before]
    assert [e.id for e in again] == [e.id for e in new]


async def test_hook_after_auto_decision_is_ignored(w: ChangeWorld) -> None:
    """A-M9: the engine announces a pre-approval (``record_auto_decision``) through
    ``core.approval_hook`` like any decision; the change took ``draft -> scheduled`` itself and
    the real ``on_decided`` must change nothing (no phase, no second ``approval_outcome``)."""
    tid = await _create(w, "standard", template_id=await _standard_template(w))
    await _ready(w, tid, cab_id=None)
    await _go(w, tid, "scheduled")
    await w.session.commit()
    row = await _row(w, tid)
    assert (row.phase, row.approval_via) == ("scheduled", "template")
    aid = int(row.approval_id or 0)
    assert (await approval_service.get_info(w.session, aid)).status == "approved"
    before = [(e.id, e.event) for e in await _events(w, tid)]
    assert [e for _, e in before].count("approval_outcome") == 1

    assert await _hook_jobs(w, aid) == [f"approval:{aid}:approved"]
    await _hook(w, aid)
    assert [(e.id, e.event) for e in await _events(w, tid)] == before
    row = await _row(w, tid)
    assert (row.phase, row.approval_round, row.approval_via) == ("scheduled", 1, "template")


async def test_outcome_of_other_keys_is_ignored(w: ChangeWorld) -> None:
    """The listener acts on the CAB key only; anything else of the subject type is left alone."""
    tid, aid = await _to_approval(w)
    subject = assert_subject_registered(SUBJECT_TYPE)
    assert subject.on_decided is not None
    before = [e.id for e in await _events(w, tid)]
    for key, ref in (("other_key", "1"), (APPROVAL_KEY, "not-a-round"), (APPROVAL_KEY, None)):
        await subject.on_decided(
            w.session,
            ApprovalOutcome(
                approval_id=aid,
                ticket_id=tid,
                subject_type=SUBJECT_TYPE,
                subject_ref=ref,
                approval_key=key,
                status="approved",
                reason="votes",
                decided_at=utc_now(),
            ),
        )
    assert [e.id for e in await _events(w, tid)] == before
    assert (await _row(w, tid)).phase == "approval"


async def test_calendar_mirror_runs_with_the_real_listener(w: ChangeWorld) -> None:
    """Task 12/16 carry: approved through the engine -> Geplant -> the appointment exists."""
    cal = 947541
    await w.session.execute(
        text(
            "INSERT INTO calendar (id, group_id, name, salt_string, color, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, :g, 'chg94-cal-17', 'salt', '#3b82f6', 1, :t, 1, :t, 1)"
        ),
        {"id": cal, "g": G_A, "t": _T},
    )
    tid, aid = await _to_approval(w, calendar_id=cal)
    await approve_all(w.session, aid)
    await _hook(w, aid)
    row = await _row(w, tid)
    assert row.phase == "scheduled"
    assert row.calendar_appointment_id is not None
    found = (
        await w.session.execute(
            text("SELECT calendar_id FROM calendar_appointment WHERE id = :i"),
            {"i": row.calendar_appointment_id},
        )
    ).scalar_one()
    assert int(found) == cal


async def test_describe_lines_name_nobody(w: ChangeWorld) -> None:
    """N2: approvers without ticket access read these plain lines (no names, no logins)."""
    tid, aid = await _to_approval(w)
    row = await _row(w, tid)
    row.risk_level = "high"
    for kind, ref in (("service", str(SERVICE)), ("service", str(SERVICE_B)), ("ci", "17")):
        w.session.add(TiqoraItsmChangeImpact(ticket_id=tid, kind=kind, ref_key=ref, direct=True))
    await w.session.flush()
    assert await describe_change(w.session, aid) == [
        "Change-Art: Normal",
        "Risiko: hoch",
        "Geplant: 02.11.2026 08:00 – 02.11.2026 10:00 (UTC)",
        "Betroffene Services: 2",
        "Betroffene CIs: 1",
    ]
    assert await describe_change(w.session, 2_000_000_000) == []


# --- lock order: the approval port before the ticket row ---------------------------------------


@dataclass
class _OrderPort(EngineApprovalPort):
    """Records the ticket row's write fingerprint at the moment the port is called."""

    ticket_id: int = 0
    seen: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    async def _mark(self, session: AsyncSession, name: str) -> None:
        self.seen.append((name, await _fingerprint(session, self.ticket_id)))

    async def request(self, session: AsyncSession, **kw: Any) -> Any:  # type: ignore[override]
        await self._mark(session, "request")
        return await super().request(session, **kw)

    async def auto_decision(self, session: AsyncSession, **kw: Any) -> Any:  # type: ignore[override]
        await self._mark(session, "auto_decision")
        return await super().auto_decision(session, **kw)

    async def cancel(self, session: AsyncSession, **kw: Any) -> None:  # type: ignore[override]
        await self._mark(session, "cancel")
        await super().cancel(session, **kw)


async def _fingerprint(session: AsyncSession, tid: int) -> tuple[Any, ...]:
    """State, owner, lock and the number of history lines: every ticket write moves one."""
    ticket = (
        await session.execute(
            text("SELECT ticket_state_id, user_id, ticket_lock_id FROM ticket WHERE id = :t"),
            {"t": tid},
        )
    ).one()
    history = (
        await session.execute(
            text("SELECT count(*) FROM ticket_history WHERE ticket_id = :t"), {"t": tid}
        )
    ).scalar_one()
    return (*tuple(ticket), int(history))


@pytest.fixture
def order_port() -> Iterator[_OrderPort]:
    port = _OrderPort()
    set_approval_port_for_tests(port)
    try:
        yield port
    finally:
        set_approval_port_for_tests(None)


@pytest.mark.parametrize("path", ["emergency_request", "normal_request", "cancel", "standard"])
async def test_port_called_before_any_ticket_write(
    w: ChangeWorld, order_port: _OrderPort, path: str
) -> None:
    """Contract lock order (approval -> ticket): ``transition()`` calls the engine before it
    writes the Znuny ticket row (state, owner, lock, dynamic-field history)."""
    await _cabs(w)
    await _settings(w, emergency_cab_id=ECAB)
    if path == "standard":
        tid = await _create(w, "standard", template_id=await _standard_template(w))
        await _ready(w, tid, cab_id=None)
        to = "scheduled"
    elif path == "emergency_request":
        tid = await _create(w, "emergency")
        await _ready(w, tid, cab_id=None)
        to = "approval"
    else:
        tid = await _create(w)
        await _ready(w, tid, cab_id=CAB, phase="assessment")
        to = "approval"
        if path == "cancel":
            order_port.ticket_id = tid
            await _go(w, tid, "approval")
            to = "cancelled"
    order_port.ticket_id = tid
    order_port.seen.clear()
    before = await _fingerprint(w.session, tid)
    await _go(w, tid, to, U_MANAGER)
    after = await _fingerprint(w.session, tid)
    assert after != before  # the step did write the ticket (state or history)
    ((name, at_port),) = order_port.seen
    assert name == {"standard": "auto_decision", "cancel": "cancel"}.get(path, "request")
    assert at_port == before, f"{path}: the ticket was written before the approval call"


_RACE_TICKET = 947550
_RACE_VOTERS = (947551, 947552, 947553)
_RACE_MARKS = (
    "tiqora_approval_event",
    "tiqora_approval_token",
    "tiqora_approval_vote",
    "tiqora_approval_approver",
    "tiqora_approval_step",
    "tiqora_approval",
    "tiqora_job",
    "tiqora_event_outbox",
    "ticket_history",
)


async def _seed_race(url: str) -> None:
    engine, factory = make_factory(url)
    async with factory() as s:
        for uid in _RACE_VOTERS:
            await s.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:i, :l, 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
                ),
                {"i": uid, "l": f"chg94-race{uid}", "t": _T},
            )
        state = (
            await s.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :tn, 'Change', 1, 1, 1, 1, 1, 3, :st, 0, 0, 0, 0,"
                " 0, 0, 0, :now, 1, :now, 1)"
            ),
            {"id": _RACE_TICKET, "tn": f"2026100{_RACE_TICKET}", "st": state, "now": _T},
        )
        await s.commit()
    await engine.dispose()


@pytest.mark.parametrize("call", ["cancel", "next_round"])
async def test_engine_port_lock_order_never_deadlocks(
    request: pytest.FixtureRequest, call: str
) -> None:
    """MariaDB, committed data: the adapter's engine call in the consumer's transaction, a vote
    waiting on the request lock, then the consumer's ticket write and commit - no 1213, the
    vote finds the request closed (``approvals_consumer_kit.race_consumer_and_vote``)."""
    url = plain_url(request, "mariadb_znuny_url")
    marks = snapshot_marks(url, _RACE_MARKS)
    engine, factory = make_factory(url)
    port = EngineApprovalPort()
    definition = ApprovalDefinition(
        steps=[
            StepSpec(
                name="CAB",
                rule="all",
                approvers=[ApproverEntry(kind="user", ref=str(u)) for u in _RACE_VOTERS],
            )
        ]
    )

    async def ask(session: AsyncSession, round: int) -> int:
        snap = await port.request(
            session,
            ticket_id=_RACE_TICKET,
            round=round,
            title="Change 1: Switch-Tausch",
            description="",
            definition=definition,
            actor=Actor.agent(1 if round == 1 else _RACE_VOTERS[0]),
            requester=Principal.agent(_RACE_VOTERS[0]),
            allow_self_approval=True,
        )
        return snap.approval_id

    try:
        await _seed_race(url)
        async with factory() as setup:
            aid = await ask(setup, 1)
            await setup.commit()

        async def first(a: AsyncSession) -> None:
            if call == "cancel":
                await port.cancel(a, approval_id=aid, actor=Actor.system(), reason="obsolet")
            else:
                await ask(a, 2)  # supersedes round 1

        outcome = await race_consumer_and_vote(
            factory,
            aid,
            first=first,
            then=lambda a: touch_ticket(a, _RACE_TICKET),
            voter=Actor.agent(_RACE_VOTERS[1]),
        )
        assert outcome == ("ok", "approval_closed")
        async with factory() as check:
            info = await approval_service.get_info(check, aid)
            assert info.status == "cancelled"
            assert info.reason == ("cancelled" if call == "cancel" else "superseded")
    finally:
        await engine.dispose()
        delete_above_marks(
            url,
            marks,
            first=[
                ("DELETE FROM ticket_history WHERE ticket_id = :t", {"t": _RACE_TICKET}),
                ("DELETE FROM ticket WHERE id = :t", {"t": _RACE_TICKET}),
                *[("DELETE FROM users WHERE id = :u", {"u": u}) for u in _RACE_VOTERS],
            ],
        )


# --- CAB admin with the engine (CX21, N4) -----------------------------------------------------


def _members(*pairs: tuple[str, str]) -> list[CabMemberIn]:
    return [CabMemberIn(member_kind=k, member_ref=r) for k, r in pairs]  # type: ignore[arg-type]


async def test_cab_customer_members_need_the_engine_setting(w: ChangeWorld) -> None:
    """CX21: customer users and customer-yielding resolvers are refused (422) until customers may
    approve; then accepted."""
    await _cabs(w)
    customer = _members(("user", str(V1)), ("customer_user", "chg94-kunde@example.org"))
    with pytest.raises(ChangeError) as exc:
        await set_members(w.session, cab_id=CAB, members=customer, user_id=1)
    assert (exc.value.error_code, exc.value.http_status) == ("cab_customer_members_disabled", 422)
    assert "Kundenbenutzer als CAB-Mitglieder" in exc.value.extra["message"]
    assert {c.id: c.customer_members_allowed for c in await list_cabs(w.session)}[CAB] is False
    # a resolver that yields customers is refused the same way; one that yields agents is not
    customer_rule = _members(("user", str(V1)), ("resolver", "ticket.customer_user"))
    with pytest.raises(ChangeError) as by_rule:
        await set_members(w.session, cab_id=CAB, members=customer_rule, user_id=1)
    assert (by_rule.value.error_code, by_rule.value.http_status) == (
        "cab_customer_members_disabled",
        422,
    )
    agents = await set_members(
        w.session, cab_id=CAB, members=_members(("resolver", "ticket.owner")), user_id=1
    )
    assert [m.member_ref for m in agents.members] == ["ticket.owner"]

    await save_section_values(
        w.session,
        APPROVALS_SECTION,
        {"customer_approvers_enabled": True},
        secret_key=APP_SETTINGS.secret_key,
    )
    view = await set_members(w.session, cab_id=CAB, members=customer_rule, user_id=1)
    assert [m.member_ref for m in view.members] == [str(V1), "ticket.customer_user"]
    view = await set_members(w.session, cab_id=CAB, members=customer, user_id=1)
    assert view.customer_members_allowed is True
    shown = {m.member_kind: m for m in view.members}
    assert shown["customer_user"].missing is True  # no such customer user (a warning, not 422)


async def test_cab_members_checked_by_the_engine(w: ChangeWorld) -> None:
    """N4: Freigabegruppen show their name and size; unknown lists and resolvers are refused."""
    await _cabs(w)
    cab_list = await approval_lists.create_list(
        w.session, name="chg94 Freigabegruppe", member_ids=[V1, V2], actor_id=1
    )
    view = await set_members(
        w.session,
        cab_id=CAB,
        members=_members(("list", str(cab_list.id)), ("resolver", "itsm.service_owner")),
        user_id=1,
    )
    shown = {m.member_kind: m for m in view.members}
    assert (shown["list"].label, shown["list"].count, shown["list"].missing) == (
        "chg94 Freigabegruppe",
        2,
        False,
    )
    assert shown["resolver"].missing is False
    for pairs, code in (
        ((("list", "947599"),), "list_not_found"),
        ((("resolver", "chg94.none"),), "resolver_not_found"),
    ):
        with pytest.raises(ChangeError) as exc:
            await set_members(w.session, cab_id=CAB, members=_members(*pairs), user_id=1)
        assert (exc.value.error_code, exc.value.http_status) == (code, 422)


def test_listener_module_is_the_transitions_module() -> None:
    """The outcome listener ends in ``transitions.on_approval_outcome`` (the one system path)."""
    subject = assert_subject_registered(SUBJECT_TYPE)
    assert subject.on_decided is transitions_module.on_cab_decided
