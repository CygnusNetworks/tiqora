"""Change transitions: guards, ticket state mapping, CAB approvals through the port, API.

Seed band 947000-947099: CABs 947001-947003, task tickets 947020-947029, the committed world of
the concurrency test 947050-947059. Everything else (change tickets, templates, freeze windows)
is created by the services inside the rolled-back world of ``tests._change_fixtures``; the
approval engine is the in-memory fake port (outcomes are delivered by calling
``on_approval_outcome`` the way the engine's hook job will).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    Q_A,
    SERVICE,
    U_FULL,
    U_MANAGER,
    U_NONE,
    U_RO,
    URL_FIXTURES,
    ChangeWorld,
    FakeApprovalPort,
    approval_port_for,
    change_world,
    make_cab,
    make_change,
    require_fake,
    settle,
)
from tests._itsm_mode_helpers import make_factory, plain_url, store, stored
from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.approvals.contract import Actor, Principal
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change import risk as risk_module
from tiqora.itsm.change import transitions as transitions_module
from tiqora.itsm.change.approval_port import get_approval_port
from tiqora.itsm.change.conflicts import Conflict
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import FreezeIn, create_freeze
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeTask,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.risk import CHECK_FAILED_KEY, RiskFinding, evaluate_risk
from tiqora.itsm.change.service import (
    ChangeCreateIn,
    ChangeUpdateIn,
    ChangeView,
    create_change,
    get_change,
    update_change,
)
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.transitions import TransitionIn, on_approval_outcome, transition
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import apply_setup
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.jobs.service import utc_now
from tiqora.plugins.sync_hooks import SyncBreaker
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

CAB, ECAB, CAB_EMPTY = 947001, 947002, 947003
T_TASK = 947020
WINDOW = (datetime(2026, 11, 2, 8, 0, 0), datetime(2026, 11, 2, 10, 0, 0))
_T = datetime(2026, 10, 1)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        yield world


@pytest.fixture
def port() -> Iterator[FakeApprovalPort]:
    """The fake, or the real engine under ``TIQORA_TEST_REAL_APPROVALS=1`` (_change_fixtures)."""
    with approval_port_for() as test_port:
        yield test_port


# --- helpers ---------------------------------------------------------------------------------


async def _settings(w: ChangeWorld, **values: Any) -> None:
    await save_section_values(
        w.session, ITSM_CHANGE_SECTION, values, secret_key=APP_SETTINGS.secret_key
    )


async def _cabs(w: ChangeWorld) -> None:
    await make_cab(
        w.session,
        cab_id=CAB,
        rule="majority",
        members=[("user", str(U_MANAGER)), ("group", str(G_A))],
    )
    await make_cab(w.session, cab_id=ECAB, rule="any", members=[("user", str(U_MANAGER))])
    await make_cab(w.session, cab_id=CAB_EMPTY, rule="any")


async def _create(
    w: ChangeWorld, change_type: str = "normal", user_id: int = U_FULL, **fields: Any
) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": change_type, "queue_id": Q_A, **fields}
    )
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=user_id,
        session_factory=w.factory,
    )
    return view.record.ticket_id


async def _row(w: ChangeWorld, tid: int) -> TiqoraItsmChange:
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    return row


async def _ready(w: ChangeWorld, tid: int, *, cab_id: int | None = CAB) -> TiqoraItsmChange:
    """Plans, window, CAB and a current impact set: everything the guards ask for."""
    row = await _row(w, tid)
    row.implementation_plan = "Switch tauschen"
    row.backout_plan = "Alten Switch wieder einbauen"
    row.planned_start, row.planned_end = WINDOW
    row.cab_id = cab_id
    row.impact_computed_at = max(row.impact_requested_at or utc_now(), utc_now()) + timedelta(
        seconds=5
    )
    await w.session.flush()
    return row


async def _at(w: ChangeWorld, tid: int, phase: str, **fields: Any) -> TiqoraItsmChange:
    """Put a change into ``phase`` directly (tests of later phases skip the walk)."""
    row = await _row(w, tid)
    row.phase = phase
    for name, value in fields.items():
        setattr(row, name, value)
    await w.session.flush()
    return row


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


async def _refused(
    w: ChangeWorld, tid: int, to: str, user_id: int = U_FULL, **fields: Any
) -> dict[str, bool]:
    """``{code: overridable}`` of a refused transition."""
    with pytest.raises(ChangeError) as exc:
        await _go(w, tid, to, user_id, **fields)
    assert exc.value.error_code == "transition_refused", exc.value.detail
    return {f["code"]: f["overridable"] for f in exc.value.extra["failures"]}


async def _outcome(
    w: ChangeWorld,
    tid: int,
    status: str,
    reason: str | None = None,
    *,
    approval_id: int | None = None,
    round: int | None = None,
) -> bool:
    """The engine decides (the current set, while still pending: the fake's ``decide`` or, under
    the real-engine switch, votes / a cancel), then its hook delivers the outcome."""
    row = await _row(w, tid)
    if approval_id is None and row.approval_id is not None:
        await settle(w.session, get_approval_port(), int(row.approval_id), status, reason)
    return await on_approval_outcome(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        approval_id=row.approval_id if approval_id is None else approval_id,
        round=row.approval_round if round is None else round,
        status=status,  # type: ignore[arg-type]
        reason=reason,
    )


async def _events(
    w: ChangeWorld, tid: int, event: str | None = None
) -> list[TiqoraItsmChangeEvent]:
    stmt = select(TiqoraItsmChangeEvent).where(TiqoraItsmChangeEvent.ticket_id == tid)
    if event is not None:
        stmt = stmt.where(TiqoraItsmChangeEvent.event == event)
    return list((await w.session.execute(stmt.order_by(TiqoraItsmChangeEvent.id))).scalars())


async def _phases(w: ChangeWorld, tid: int) -> list[tuple[str | None, str | None]]:
    return [(e.from_phase, e.to_phase) for e in await _events(w, tid, "phase")]


def _detail(event: TiqoraItsmChangeEvent) -> dict[str, Any]:
    return dict(json.loads(event.detail or "{}"))


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


async def _state_updates(w: ChangeWorld, tid: int) -> int:
    return int(
        (
            await w.session.execute(
                text(
                    "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                    " ON ht.id = h.history_type_id WHERE ht.name = 'StateUpdate'"
                    " AND h.ticket_id = :t"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )


async def _open_task(w: ChangeWorld, change_tid: int, task_tid: int = T_TASK) -> None:
    state_id = (
        await w.session.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
    ).scalar_one()
    await w.session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :tn, 'Aufgabe', :q, 1, 1, 1, 1, 3, :st, 0, 0, 0, 0,"
            " 0, 0, 0, :now, 1, :now, 1)"
        ),
        {"id": task_tid, "tn": f"20261001{task_tid}", "q": Q_A, "st": state_id, "now": _T},
    )
    w.session.add(
        TiqoraItsmChangeTask(
            ticket_id=task_tid,
            change_ticket_id=change_tid,
            position=1,
            task_kind="workorder",
            created_at=_T,
            created_by=1,
        )
    )
    await w.session.flush()


async def _standard_template(w: ChangeWorld) -> int:
    now = utc_now()
    tpl = TiqoraItsmChangeTemplate(
        name="chg94 Standard-Change",
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


async def _blocking_freeze(w: ChangeWorld) -> None:
    await create_freeze(
        w.session,
        data=FreezeIn(
            name="chg94 Jahresabschluss",
            enforcement="block",
            allow_emergency=False,
            starts_at=WINDOW[0] - timedelta(hours=1),
            ends_at=WINDOW[1] + timedelta(hours=1),
        ),
        user_id=1,
    )


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid,
        login=f"chg94.{uid}",
        first_name="Erika",
        last_name="Beispiel",
        auth_method="session",
    )


@dataclass
class _Api:
    client: AsyncClient
    who: dict[str, AuthenticatedUser]

    def login(self, uid: int) -> None:
        self.who["u"] = _user(uid)


@asynccontextmanager
async def _api(w: ChangeWorld) -> AsyncIterator[_Api]:
    who = {"u": _user(U_FULL)}

    async def _db() -> Any:
        yield w.session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
    app.dependency_overrides[get_current_user] = lambda: who["u"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield _Api(client, who)


# --- the paths -------------------------------------------------------------------------------


async def test_normal_happy_path(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    tid = await _create(w, reason="Austausch\nam Samstag")
    await _ready(w, tid)
    assert await _ticket_state(w, tid) == "new"

    assert (await _go(w, tid, "submitted")).record.phase == "submitted"
    assert await _ticket_state(w, tid) == "open"  # leaving Entwurf opens the ticket
    await _go(w, tid, "assessment")
    view = await _go(w, tid, "approval")
    rec = view.record
    assert (rec.phase, rec.approval_round, rec.approval_via) == ("approval", 1, "cab")
    assert rec.risk_evaluated_at is not None  # F16: evaluated right before the request

    (req,) = port.requests
    assert (req.ticket_id, req.round) == (tid, 1)
    assert req.title == f"Change {view.ticket.tn}: Switch-Tausch"
    assert req.description == "Austausch\nam Samstag"
    (step,) = req.definition.steps
    assert (step.name, step.rule, step.quorum) == ("chg94 CAB 947001", "majority", None)
    assert [(a.kind, a.ref) for a in step.approvers] == [
        ("user", str(U_MANAGER)),
        ("group", str(G_A)),
    ]
    assert (step.due_hours, step.on_timeout, step.on_no_approver) == (72, "notify", "reject")
    assert step.allow_delegation and step.allow_email
    assert req.actor == Actor.agent(U_FULL)
    assert req.requester == Principal.agent(U_FULL)  # the change ticket's owner
    assert req.allow_self_approval is False
    assert rec.approval_id == require_fake(port).snapshots[rec.approval_id].approval_id
    (requested,) = await _events(w, tid, "approval_requested")
    assert _detail(requested) == {
        "approval_id": rec.approval_id,
        "cab_entries": 2,
        "cab_id": CAB,
        "round": 1,
    }

    # the outcome is the engine's: an agent cannot take it
    assert "system_only" in await _refused(w, tid, "scheduled")
    assert await _outcome(w, tid, "approved") is True
    row = await _row(w, tid)
    assert row.phase == "scheduled" and row.approved_at is not None

    view = await _go(w, tid, "implementing")
    assert view.record.actual_start is not None and view.record.actual_end is None
    view = await _go(w, tid, "review")
    assert view.record.actual_end is not None
    assert "outcome_missing" in await _refused(w, tid, "closed", pir_notes="Lief gut")
    assert "pir_missing" in await _refused(w, tid, "closed", outcome="successful")
    view = await _go(w, tid, "closed", outcome="successful", pir_notes="Lief gut")
    assert (view.record.outcome, view.record.pir_notes) == ("successful", "Lief gut")
    assert view.allowed_targets == ()
    assert await _ticket_state(w, tid) == "closed successful"

    assert await _phases(w, tid) == [
        (None, "draft"),
        ("draft", "submitted"),
        ("submitted", "assessment"),
        ("assessment", "approval"),
        ("approval", "scheduled"),
        ("scheduled", "implementing"),
        ("implementing", "review"),
        ("review", "closed"),
    ]
    with pytest.raises(ChangeError) as final:
        await _go(w, tid, "cancelled")
    assert (final.value.error_code, final.value.http_status) == ("change_final", 409)


async def test_standard_path_pre_approved(w: ChangeWorld, port: FakeApprovalPort) -> None:
    tpl = await _standard_template(w)
    tid = await _create(w, "standard", template_id=tpl)
    assert "window_missing" in await _refused(w, tid, "scheduled")
    await _ready(w, tid, cab_id=None)

    view = await _go(w, tid, "scheduled")
    rec = view.record
    assert port.requests == []  # pre-approved: nobody votes
    assert port.auto_decisions == [(tid, 1)]
    assert (rec.approval_via, rec.approval_round) == ("template", 1)
    assert rec.approved_at is not None and rec.approval_id is not None
    assert require_fake(port).snapshots[rec.approval_id].reason == "auto"
    (trail,) = await _events(w, tid, "approval_outcome")
    assert _detail(trail)["status"] == "approved" and _detail(trail)["via"] == "template"

    # the engine reports the auto decision too; the change ignores it
    assert await _outcome(w, tid, "approved", "auto") is False
    await _go(w, tid, "implementing")
    view = await _go(w, tid, "closed", outcome="successful")  # no review for standard
    assert await _ticket_state(w, tid) == "closed successful"
    assert [p for _, p in await _phases(w, tid)] == ["draft", "scheduled", "implementing", "closed"]


async def test_emergency_path_and_post_approval(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    await _settings(w, emergency_cab_id=ECAB, default_cab_id=CAB)

    # off (default): an emergency change asks the emergency CAB before anything happens
    first = await _create(w, "emergency")
    await _ready(w, first, cab_id=None)
    assert "transition_not_allowed" in await _refused(w, first, "implementing")
    await _go(w, first, "approval")
    assert port.requests[-1].definition.steps[0].name == "chg94 CAB 947002"
    await _outcome(w, first, "approved")
    assert (await _row(w, first)).phase == "scheduled"

    # on: implementation starts first, the decision follows
    await _settings(w, emergency_post_approval=True)
    rejected = await _create(w, "emergency")
    await _ready(w, rejected, cab_id=None)
    view = await _go(w, rejected, "implementing")
    rec = view.record
    assert (rec.approval_via, rec.approval_round, rec.actual_start is not None) == ("post", 1, True)
    assert port.requests[-1].ticket_id == rejected
    assert await _ticket_state(w, rejected) == "open"
    # F19: a rejection while implementing forces Nachbetrachtung
    assert await _outcome(w, rejected, "rejected") is True
    row = await _row(w, rejected)
    assert row.phase == "review" and row.actual_end is not None
    (forced,) = await _events(w, rejected, "forced")
    assert _detail(forced)["reason"] == "approval_rejected" and forced.created_by == 1
    assert await _phases(w, rejected) == [
        (None, "draft"),
        ("draft", "implementing"),
        ("implementing", "review"),
    ]
    # a rejected change cannot be closed as successful
    assert "transition_not_allowed" in await _refused(
        w, rejected, "closed", outcome="successful", pir_notes="Abgebrochen"
    )
    await _go(w, rejected, "closed", outcome="failed", pir_notes="Abgebrochen")
    assert await _ticket_state(w, rejected) == "closed unsuccessful"

    # a decision still missing blocks the close, for everybody
    pending = await _create(w, "emergency")
    await _ready(w, pending, cab_id=None)
    await _go(w, pending, "implementing")
    await _go(w, pending, "review")
    failures = await _refused(
        w,
        pending,
        "closed",
        U_MANAGER,
        outcome="successful",
        pir_notes="ok",
        force=True,
        reason="x",
    )
    assert failures == {"approval_pending": False}
    assert await _outcome(w, pending, "approved") is True
    row = await _row(w, pending)
    assert row.phase == "review" and row.approved_at is not None  # bookkeeping only
    await _go(w, pending, "closed", outcome="successful", pir_notes="ok")


async def test_low_risk_auto_approval_only_when_enabled(
    w: ChangeWorld, port: FakeApprovalPort, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    await _at(w, tid, "assessment")
    view = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert "scheduled" not in view.allowed_targets
    assert await _refused(w, tid, "scheduled") == {"auto_approval_not_eligible": False}

    await _settings(w, auto_approve_low_risk=True)
    await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    view = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert view.record.risk_level == "low" and "scheduled" in view.allowed_targets

    # an override above low, a failed check or a block finding rule the route out
    await _at(w, tid, "assessment", risk_override_level="high", risk_override_reason="x")
    assert "auto_approval_not_eligible" in await _refused(w, tid, "scheduled")
    await _at(w, tid, "assessment", risk_override_level=None, risk_override_reason=None)

    real_run = risk_module._run_checks

    async def failing(session: Any, inp: Any) -> Any:
        found = await real_run(session, inp)
        return [*found, RiskFinding("chg94.check", "warn", 0, CHECK_FAILED_KEY, {}, ())]

    monkeypatch.setattr(risk_module, "_run_checks", failing)
    assert "auto_approval_not_eligible" in await _refused(w, tid, "scheduled")
    monkeypatch.setattr(risk_module, "_run_checks", real_run)

    await _blocking_freeze(w)
    failures = await _refused(w, tid, "scheduled", U_MANAGER, override_reason="Notwendig")
    assert failures == {"block_conflict": False, "auto_approval_not_eligible": False}

    # the route itself
    other = await _create(w)
    await _ready(w, other)
    other_row = await _at(w, other, "assessment")
    other_row.planned_start = WINDOW[1] + timedelta(days=1)
    other_row.planned_end = WINDOW[1] + timedelta(days=1, hours=2)
    await w.session.flush()
    view = await _go(w, other, "scheduled")
    rec = view.record
    assert (rec.phase, rec.approval_via, rec.approval_round) == ("scheduled", "risk", 1)
    assert port.requests == [] and port.auto_decisions == [(other, 1)]


async def test_guard_failures_returned_as_409(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    async with _api(w) as api:
        created = await api.client.post(
            "/api/v1/itsm/changes",
            json={"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A},
        )
        tid = created.json()["change"]["ticket_id"]
        url = f"/api/v1/itsm/changes/{tid}/transition"
        for to in ("submitted", "assessment"):
            got = await api.client.post(url, json={"to": to})
            assert got.status_code == 200, got.text
            assert got.json()["phase"] == to

        got = await api.client.post(url, json={"to": "approval"})
        assert got.status_code == 409
        detail = got.json()["detail"]
        assert detail["code"] == "transition_refused"
        assert {f["code"] for f in detail["failures"]} == {
            "window_missing",
            "plans_missing",
            "impact_pending",
            "cab_missing",
        }
        assert await _phases(w, tid) == [
            (None, "draft"),
            ("draft", "submitted"),
            ("submitted", "assessment"),
        ]

        await _ready(w, tid, cab_id=CAB_EMPTY)
        got = await api.client.post(url, json={"to": "approval"})
        assert [f["code"] for f in got.json()["detail"]["failures"]] == ["cab_empty"]
        got = await api.client.post(url, json={"to": "closed"})
        assert got.json()["detail"]["failures"] == [
            {"code": "transition_not_allowed", "overridable": False}
        ]
        assert (await api.client.post(url, json={"to": "nonsense"})).status_code == 422

        api.login(U_RO)
        got = await api.client.post(url, json={"to": "cancelled"})
        assert (got.status_code, got.json()["detail"]["code"]) == (403, "forbidden")
        api.login(U_NONE)
        assert (await api.client.post(url, json={"to": "cancelled"})).status_code == 404
        assert (await api.client.get(f"/api/v1/itsm/changes/{tid}/approval")).status_code == 404

        api.login(U_FULL)
        await _ready(w, tid)
        got = await api.client.post(url, json={"to": "approval", "reason": "Bitte freigeben"})
        assert got.status_code == 200, got.text
        assert got.json()["phase"] == "approval"
        last = (await _events(w, tid, "phase"))[-1]
        assert _detail(last) == {"reason": "Bitte freigeben"}


async def test_patch_cannot_set_phase(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    tid = await _create(w)
    async with _api(w) as api:
        url = f"/api/v1/itsm/changes/{tid}"
        got = await api.client.patch(url, json={"phase": "closed"})
        assert got.status_code == 422
        assert (await api.client.get(url)).json()["phase"] == "draft"

        await _ready(w, tid)
        await _at(w, tid, "assessment")
        await _go(w, tid, "approval")
        # while the CAB decides, what it decides on is frozen
        for body in (
            {"planned_start": "2026-11-02T07:00:00Z"},
            {"implementation_plan": "Anders"},
            {"backout_plan": "Anders"},
            {"test_plan": "Anders"},
            {"cab_id": ECAB},
            {"change_type": "emergency"},
            {"clear_window": True},
        ):
            got = await api.client.patch(url, json=body)
            assert got.status_code == 409, (body, got.text)
            assert got.json()["detail"]["code"] == "change_locked_in_approval"
        assert (await api.client.patch(url, json={"reason": "Mehr Details"})).status_code == 200

        # later phases stay editable, and every edit is on the timeline
        await _outcome(w, tid, "approved")
        got = await api.client.patch(url, json={"planned_end": "2026-11-02T11:00:00Z"})
        assert got.status_code == 200, got.text
        assert got.json()["phase"] == "scheduled"
        assert _detail((await _events(w, tid, "updated"))[-1]) == {"fields": ["window"]}


async def test_override_requires_manager_and_reason(
    w: ChangeWorld, port: FakeApprovalPort, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    await _at(w, tid, "assessment")
    await _blocking_freeze(w)

    assert await _refused(w, tid, "approval", override_reason="Muss sein") == {
        "block_conflict": True
    }
    assert await _refused(w, tid, "approval", U_MANAGER) == {"block_conflict": True}
    assert await _refused(w, tid, "approval", U_MANAGER, override_reason="  ") == {
        "block_conflict": True
    }
    await _go(w, tid, "approval", U_MANAGER, override_reason="Sicherheitsupdate")
    (override,) = await _events(w, tid, "conflict_override")
    assert override.created_by == U_MANAGER
    assert _detail(override) == {"block_conflicts": 1, "reason": "Sicherheitsupdate"}

    # freeze occurrences that were cut off count as a block (fail closed)
    other = await _create(w)
    await _ready(w, other)
    await _at(w, other, "assessment")
    truncated = Conflict("freeze_truncated", "warn", None, *WINDOW)

    async def only_truncated(session: Any, *, ticket_id: int, user_id: int | None = None) -> Any:
        assert user_id is None  # guards read the system view
        return [truncated]

    monkeypatch.setattr(transitions_module, "load_conflicts", only_truncated)
    assert await _refused(w, other, "approval") == {"block_conflict": True}


async def _service_freeze(w: ChangeWorld) -> None:
    """A blocking Sperrzeitraum on ``SERVICE`` only, no exception for emergencies."""
    await create_freeze(
        w.session,
        data=FreezeIn(
            name="chg94 Mail-Wochenende",
            enforcement="block",
            allow_emergency=False,
            scope="services",
            service_ids=[SERVICE],
            starts_at=WINDOW[0] - timedelta(hours=1),
            ends_at=WINDOW[1] + timedelta(hours=1),
        ),
        user_id=1,
    )


async def _impact_pending(w: ChangeWorld, tid: int) -> None:
    """The impact job has not run yet (``_ready`` marks it current)."""
    row = await _row(w, tid)
    row.impact_computed_at = None
    await w.session.flush()


async def test_service_freeze_blocks_before_the_impact_job_ran(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """A-I1: the direct services count at once, the stored impact set is not needed."""
    await _cabs(w)
    await _settings(w, emergency_post_approval=True, emergency_cab_id=ECAB)
    await _service_freeze(w)

    # emergency "Sofort umsetzen" right after creating it on the frozen service
    emergency = await _create(w, "emergency", service_ids=[SERVICE])
    await _ready(w, emergency, cab_id=None)
    await _impact_pending(w, emergency)
    assert await _refused(w, emergency, "implementing") == {"block_conflict": True}
    assert (await _row(w, emergency)).phase == "draft"

    # a standard change from a template, planned at once
    standard = await _create(
        w, "standard", template_id=await _standard_template(w), service_id=SERVICE
    )
    await _ready(w, standard, cab_id=None)
    await _impact_pending(w, standard)
    assert await _refused(w, standard, "scheduled") == {"block_conflict": True}
    await _go(w, standard, "scheduled", U_MANAGER, override_reason="Abgestimmt")
    (override,) = await _events(w, standard, "conflict_override")
    assert _detail(override) == {"block_conflicts": 1, "reason": "Abgestimmt"}

    # a scheduled change gets the frozen service and is started at once
    scheduled = await _create(w)
    await _ready(w, scheduled)
    await _at(w, scheduled, "scheduled")
    await update_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=scheduled,
        data=ChangeUpdateIn(service_ids=[SERVICE]),
        user_id=U_FULL,
    )
    assert await _refused(w, scheduled, "implementing") == {"block_conflict": True}


async def test_service_freeze_unverified_while_impact_is_pending(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """A-I1 (b): a change with a CI may reach the frozen service through it; until the impact
    job ran that cannot be ruled out, so it blocks (overridable), like cut-off freezes."""
    await _service_freeze(w)
    tpl = await _standard_template(w)
    tid = await _create(w, "standard", template_id=tpl)
    await _ready(w, tid, cab_id=None)
    w.session.add(TiqoraItsmChangeImpact(ticket_id=tid, kind="ci", ref_key="947090", direct=True))
    await _impact_pending(w, tid)
    assert await _refused(w, tid, "scheduled") == {"block_conflict": True}

    # computed: the CI reaches no frozen service, the step is free
    await _ready(w, tid, cab_id=None)
    assert (await _go(w, tid, "scheduled")).record.phase == "scheduled"

    # without any CI the live direct services are the whole set: nothing to verify
    bare = await _create(w, "standard", template_id=tpl)
    await _ready(w, bare, cab_id=None)
    await _impact_pending(w, bare)
    assert (await _go(w, bare, "scheduled")).record.phase == "scheduled"


async def test_service_freeze_unverified_on_a_truncated_impact_set(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """A computed set cut off at the limit may miss the frozen service just the same."""
    await _service_freeze(w)
    tpl = await _standard_template(w)
    tid = await _create(w, "standard", template_id=tpl)
    await _ready(w, tid, cab_id=None)
    w.session.add(TiqoraItsmChangeImpact(ticket_id=tid, kind="ci", ref_key="947090", direct=True))
    (await _row(w, tid)).impact_truncated = True
    await w.session.flush()
    assert await _refused(w, tid, "scheduled") == {"block_conflict": True}

    (await _row(w, tid)).impact_truncated = False
    await w.session.flush()
    assert (await _go(w, tid, "scheduled")).record.phase == "scheduled"


async def test_standard_needs_a_valid_standard_template(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """A-M2: a retired, edited or deleted template no longer pre-approves its drafts."""
    tpl_id = await _standard_template(w)
    tids = [await _create(w, "standard", template_id=tpl_id) for _ in range(3)]
    for tid in tids:
        await _ready(w, tid, cab_id=None)
    tpl = await w.session.get(TiqoraItsmChangeTemplate, tpl_id)
    assert tpl is not None

    tpl.valid = False
    await w.session.flush()
    with pytest.raises(ChangeError) as exc:
        await _go(w, tids[0], "scheduled")
    assert (exc.value.error_code, exc.value.http_status) == ("standard_template_invalid", 422)

    tpl.valid, tpl.is_standard = True, False
    await w.session.flush()
    with pytest.raises(ChangeError) as exc:
        await _go(w, tids[1], "scheduled")
    assert exc.value.error_code == "standard_template_invalid"

    row = await _row(w, tids[2])
    row.template_id = None  # the template was deleted (ON DELETE SET NULL)
    await w.session.flush()
    with pytest.raises(ChangeError) as exc:
        await _go(w, tids[2], "scheduled")
    assert exc.value.error_code == "standard_template_invalid"
    assert port.auto_decisions == []

    tpl.is_standard = True
    await w.session.flush()
    assert (await _go(w, tids[1], "scheduled")).record.phase == "scheduled"


async def test_force_close_with_open_tasks_manager_only(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    tid = await _create(w)
    # created before any task ticket: an explicit ticket id moves MariaDB's auto increment
    standard = await _create(w, "standard", template_id=await _standard_template(w))
    await _ready(w, tid, cab_id=None)
    await _at(w, tid, "implementing")
    await _open_task(w, tid)

    assert await _refused(w, tid, "review") == {"tasks_open": True}
    assert await _refused(w, tid, "review", force=True, reason="Erledigt") == {"tasks_open": True}
    assert await _refused(w, tid, "review", U_MANAGER, force=True) == {"reason_missing": False}
    await _go(w, tid, "review", U_MANAGER, force=True, reason="Aufgabe obsolet")
    (forced,) = await _events(w, tid, "forced")
    assert forced.created_by == U_MANAGER
    assert _detail(forced) == {"open_tasks": 1, "reason": "Aufgabe obsolet"}
    assert (await _phases(w, tid))[-1] == ("implementing", "review")

    # a standard change closes straight from implementing; the missing reason is listed together
    # with the step's other failures
    await _ready(w, standard, cab_id=None)
    await _at(w, standard, "implementing")
    await _open_task(w, standard, T_TASK + 1)
    assert await _refused(w, standard, "closed", U_MANAGER, force=True) == {
        "outcome_missing": False,
        "reason_missing": False,
    }
    await _go(w, standard, "closed", U_MANAGER, force=True, reason="ok", outcome="successful")
    (forced,) = await _events(w, standard, "forced")
    assert _detail(forced) == {"open_tasks": 1, "reason": "ok"}


async def test_post_approval_outcome_delivered_twice(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """The hook job is retried: after the phase stays put, the event trail decides."""
    await _cabs(w)
    await _settings(w, emergency_post_approval=True, emergency_cab_id=ECAB)

    approved = await _create(w, "emergency")
    await _ready(w, approved, cab_id=None)
    await _go(w, approved, "implementing")
    await _go(w, approved, "review")
    assert await _outcome(w, approved, "approved") is True
    first = (await _row(w, approved)).approved_at
    assert first is not None
    assert await _outcome(w, approved, "approved") is False
    assert (await _row(w, approved)).approved_at == first
    assert len(await _events(w, approved, "approval_outcome")) == 1

    rejected = await _create(w, "emergency")
    await _ready(w, rejected, cab_id=None)
    await _go(w, rejected, "implementing")
    assert await _outcome(w, rejected, "rejected") is True
    assert await _outcome(w, rejected, "rejected") is False
    assert await _outcome(w, rejected, "cancelled", "admin") is False
    assert len(await _events(w, rejected, "forced")) == 1
    assert len(await _events(w, rejected, "approval_outcome")) == 1
    assert [p for p in await _phases(w, rejected) if p[0] == "implementing"] == [
        ("implementing", "review")
    ]

    cancelled = await _create(w, "emergency")
    await _ready(w, cancelled, cab_id=None)
    await _go(w, cancelled, "implementing")
    assert await _outcome(w, cancelled, "cancelled", "admin") is True
    assert await _outcome(w, cancelled, "cancelled", "admin") is False
    assert len(await _events(w, cancelled, "approval_outcome")) == 1

    # before the decision (phase approval): the row alone answers the second delivery
    pre = await _create(w)
    await _ready(w, pre)
    await _at(w, pre, "assessment")
    await _go(w, pre, "approval")
    assert await _outcome(w, pre, "rejected") is True
    assert await _outcome(w, pre, "rejected") is False
    assert len(await _events(w, pre, "approval_outcome")) == 1


async def test_erased_post_approval_counts_by_approved_at(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    await _settings(w, emergency_post_approval=True, emergency_cab_id=ECAB)
    close = {"outcome": "successful", "pir_notes": "ok"}

    # approved before the set was erased: closes like any approved change
    approved = await _create(w, "emergency")
    await _ready(w, approved, cab_id=None)
    await _go(w, approved, "implementing")
    await _go(w, approved, "review")
    assert await _outcome(w, approved, "approved") is True
    erased = (await _row(w, approved)).approval_id
    require_fake(port).snapshots.pop(int(erased or 0))
    await _go(w, approved, "closed", **close)

    # never decided: counts as cancelled (fail closed), a manager forces it with a reason
    undecided = await _create(w, "emergency")
    await _ready(w, undecided, cab_id=None)
    await _go(w, undecided, "implementing")
    await _go(w, undecided, "review")
    require_fake(port).snapshots.pop(int((await _row(w, undecided)).approval_id or 0))
    assert await _refused(w, undecided, "closed", **close) == {"approval_pending": True}
    await _go(w, undecided, "closed", U_MANAGER, **close, force=True, reason="Gelöscht")
    (forced,) = await _events(w, undecided, "forced")
    assert _detail(forced) == {"approval": "cancelled", "reason": "Gelöscht"}


async def test_pir_notes_only_where_recorded(w: ChangeWorld, port: FakeApprovalPort) -> None:
    with pytest.raises(ValidationError):
        TransitionIn.model_validate({"to": "submitted", "pir_notes": "zu früh"})
    assert TransitionIn(to="review", pir_notes="ok").pir_notes == "ok"
    tid = await _create(w)
    async with _api(w) as api:
        got = await api.client.post(
            f"/api/v1/itsm/changes/{tid}/transition", json={"to": "submitted", "pir_notes": "x"}
        )
        assert got.status_code == 422
    assert (await _row(w, tid)).pir_notes is None


async def test_cancel_cancels_pending_approval(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    await _at(w, tid, "assessment")
    rec = (await _go(w, tid, "approval")).record
    assert rec.approval_id is not None

    view = await _go(w, tid, "cancelled", reason="Nicht mehr nötig")
    assert view.record.phase == "cancelled"
    assert port.cancels == [(rec.approval_id, "Nicht mehr nötig")]
    assert require_fake(port).snapshots[rec.approval_id].status == "cancelled"
    assert await _ticket_state(w, tid) == "closed unsuccessful"
    # the engine reports the cancel this change asked for: ignored
    events = len(await _events(w, tid))
    assert await _outcome(w, tid, "cancelled", "Nicht mehr nötig") is False
    assert len(await _events(w, tid)) == events

    # without a reason the engine gets a code, never UI text
    other = await _create(w)
    await _ready(w, other)
    await _at(w, other, "assessment")
    await _go(w, other, "approval")
    await _go(w, other, "cancelled")
    assert port.cancels[-1][1] == "change_cancelled"


async def test_rejected_then_rework_starts_new_round(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    for to in ("submitted", "assessment", "approval"):
        await _go(w, tid, to)
    first = (await _row(w, tid)).approval_id
    assert await _outcome(w, tid, "rejected") is True
    assert (await _row(w, tid)).phase == "rejected"
    assert await _ticket_state(w, tid) == "open"  # F11: Abgelehnt leaves the ticket alone

    view = await _go(w, tid, "draft")  # "Überarbeiten"
    assert view.record.approval_via is None
    for to in ("submitted", "assessment", "approval"):
        await _go(w, tid, to)
    row = await _row(w, tid)
    assert row.approval_round == 2 and row.approval_id != first
    assert [r.round for r in port.requests] == [1, 2]
    assert await _ticket_state(w, tid) == "open"  # rejected -> draft -> submitted: still open
    assert await _state_updates(w, tid) == 1  # only the first new -> open
    assert await _outcome(w, tid, "approved", approval_id=first, round=1) is False
    assert (await _row(w, tid)).phase == "approval"


async def test_rejected_change_can_be_given_up(w: ChangeWorld, port: FakeApprovalPort) -> None:
    """A-M7: Abgelehnt -> Abgebrochen directly (no fake rework step); the ticket closes, the
    decided approval is left alone and the impact refresh no longer picks the change."""
    from tiqora.itsm.change.impact import refresh_stale_impacts
    from tiqora.itsm.change.settings import ItsmChangeSettings

    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    for to in ("submitted", "assessment", "approval"):
        await _go(w, tid, to)
    assert await _outcome(w, tid, "rejected") is True
    view = await get_change(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert "cancelled" in view.allowed_targets
    # an agent action that needs rw
    assert await _code_of(_go(w, tid, "cancelled", U_RO)) == "forbidden"

    await _go(w, tid, "cancelled", reason="Nicht mehr nötig")
    row = await _row(w, tid)
    assert row.phase == "cancelled"
    assert (await _phases(w, tid))[-1] == ("rejected", "cancelled")
    assert port.cancels == []  # the rejection is final: nothing to cancel in the engine
    assert await _ticket_state(w, tid) == "closed unsuccessful"
    row.impact_computed_at = None  # stale: a reworkable change would be re-queued
    await w.session.flush()
    assert await refresh_stale_impacts(w.session, ItsmChangeSettings()) == 0


async def _code_of(call: Any) -> str:
    with pytest.raises(ChangeError) as exc:
        await call
    return exc.value.error_code


async def test_stale_round_outcome_ignored(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    await _at(w, tid, "assessment", approval_round=1, approval_id=999)
    rec = (await _go(w, tid, "approval")).record
    assert rec.approval_round == 2
    events = len(await _events(w, tid))

    assert await _outcome(w, tid, "approved", approval_id=999, round=1) is False
    assert await _outcome(w, tid, "approved", round=1) is False  # current id, old round
    assert await _outcome(w, tid, "approved", approval_id=999) is False  # current round, old id
    assert (
        await on_approval_outcome(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=947099,
            approval_id=1,
            round=1,
            status="approved",
            reason=None,
        )
        is False
    )  # no change row
    assert (await _row(w, tid)).phase == "approval"
    assert len(await _events(w, tid)) == events


async def test_outcome_delivered_twice_transitions_once(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    ids = []
    for _ in range(3):
        tid = await _create(w)
        await _ready(w, tid)
        await _at(w, tid, "assessment")
        await _go(w, tid, "approval")
        ids.append(tid)
    twice, rejected_first, cancelled_first = ids

    assert await _outcome(w, twice, "approved") is True
    assert await _outcome(w, twice, "approved") is False
    assert (await _phases(w, twice))[-1] == ("approval", "scheduled")
    assert len(await _events(w, twice, "approval_outcome")) == 1

    # one round, rejected and cancelled, in both orders (hook lag): the first one counts
    assert await _outcome(w, rejected_first, "rejected") is True
    assert await _outcome(w, rejected_first, "cancelled", "admin") is False
    assert (await _row(w, rejected_first)).phase == "rejected"
    assert await _outcome(w, cancelled_first, "cancelled", "admin") is True
    assert await _outcome(w, cancelled_first, "rejected") is False
    assert (await _row(w, cancelled_first)).phase == "assessment"
    for tid in ids:
        assert len([p for p in await _phases(w, tid) if p[0] == "approval"]) == 1


async def test_plugin_veto_writes_nothing(
    w: ChangeWorld, port: FakeApprovalPort, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await _create(w)
    events = len(await _events(w, tid))
    history = await _state_updates(w, tid)
    boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.state_change"] = "veto"
    try:
        with pytest.raises(ChangeError) as exc:
            await _go(w, tid, "submitted")  # leaving Entwurf: new -> open
    finally:
        SyncBreaker.reset_for_tests()
    assert (exc.value.error_code, exc.value.http_status) == ("vetoed", 409)
    assert exc.value.extra["message"] == "Vertragsnummer fehlt"
    row = await _row(w, tid)
    await w.session.refresh(row)
    assert row.phase == "draft"
    assert len(await _events(w, tid)) == events
    assert await _ticket_state(w, tid) == "new"
    assert await _state_updates(w, tid) == history


async def test_ticket_state_mapping_only_at_named_phases(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    tid = await _create(w)
    await _ready(w, tid)
    seen = [await _ticket_state(w, tid)]
    for to in ("submitted", "assessment", "approval"):
        await _go(w, tid, to)
        seen.append(await _ticket_state(w, tid))
    await _outcome(w, tid, "approved")
    seen.append(await _ticket_state(w, tid))
    for to in ("implementing", "review"):
        await _go(w, tid, to)
        seen.append(await _ticket_state(w, tid))
    assert seen == ["new", *["open"] * 6]
    assert await _state_updates(w, tid) == 1

    # Abgebrochen closes with its own state; an "open" ticket is not re-opened on leaving draft
    await _settings(w, state_closed_cancelled="closed successful")
    other = await _create(w)
    await _go(w, other, "cancelled")
    assert await _ticket_state(w, other) == "closed successful"
    assert await _state_updates(w, other) == 1


async def test_missing_state_name_409(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _settings(w, state_open="chg94 gibt es nicht")
    tid = await _create(w)
    with pytest.raises(ChangeError) as exc:
        await _go(w, tid, "submitted")
    assert (exc.value.error_code, exc.value.http_status) == ("change_state_not_found", 409)
    assert exc.value.extra == {"setting": "state_open", "state": "chg94 gibt es nicht"}
    assert (await _row(w, tid)).phase == "draft"
    assert await _phases(w, tid) == [(None, "draft")]


async def test_cancelled_approval_returns_to_assessment(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    await _settings(w, emergency_cab_id=ECAB)
    normal = await _create(w)
    await _ready(w, normal)
    await _at(w, normal, "assessment")
    await _go(w, normal, "approval")
    emergency = await _create(w, "emergency")
    await _ready(w, emergency, cab_id=None)
    await _go(w, emergency, "approval")

    # the engine supersedes a round itself: no phase change
    assert await _outcome(w, normal, "cancelled", "superseded") is False
    assert await _outcome(w, normal, "cancelled", "admin") is True
    assert await _outcome(w, emergency, "cancelled", "admin") is True
    assert (await _row(w, normal)).phase == "assessment"
    assert (await _row(w, emergency)).phase == "draft"
    (outcome,) = await _events(w, normal, "approval_outcome")
    assert _detail(outcome)["status"] == "cancelled" and outcome.created_by == 1

    await _go(w, normal, "approval")  # resubmission: round + 1
    assert (await _row(w, normal)).approval_round == 2


async def test_post_approval_cancelled_needs_forced_close(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    await _cabs(w)
    await _settings(w, emergency_post_approval=True, emergency_cab_id=ECAB)
    tid = await _create(w, "emergency")
    await _ready(w, tid, cab_id=None)
    await _go(w, tid, "implementing")
    await _go(w, tid, "review")
    assert await _outcome(w, tid, "cancelled", "admin") is True
    assert (await _row(w, tid)).phase == "review"

    close = {"outcome": "successful", "pir_notes": "ok"}
    assert await _refused(w, tid, "closed", **close) == {"approval_pending": True}
    assert await _refused(w, tid, "closed", **close, force=True, reason="x") == {
        "approval_pending": True
    }
    assert await _refused(w, tid, "closed", U_MANAGER, **close, force=True) == {
        "reason_missing": False
    }
    await _go(w, tid, "closed", U_MANAGER, **close, force=True, reason="ECAB aufgelöst")
    (forced,) = await _events(w, tid, "forced")
    assert _detail(forced) == {"approval": "cancelled", "reason": "ECAB aufgelöst"}


async def test_close_owner_follows_required_lock(w: ChangeWorld, port: FakeApprovalPort) -> None:
    tid = await _create(w)  # owner U_FULL, unlocked
    await _ready(w, tid, cab_id=None)
    await _at(w, tid, "review")
    await _go(w, tid, "closed", U_MANAGER, outcome="successful", pir_notes="ok")
    owner, lock = (
        await w.session.execute(
            text("SELECT user_id, ticket_lock_id FROM ticket WHERE id = :t"), {"t": tid}
        )
    ).one()
    # like closing in Znuny (RequiredLock): the closing agent becomes owner, unlocked afterwards
    assert (owner, lock) == (U_MANAGER, 1)


_X1 = [
    "agent_submit",
    "agent_return",
    "agent_approval",
    "agent_override",
    "agent_replan",
    "agent_rework",
    "agent_cancel",
    "agent_cancel_in_approval",
    "agent_close",
    "auto_template",
    "auto_risk",
    "manager_force",
    "approved",
    "rejected",
    "no_approvers",
    "cancelled_normal",
    "cancelled_emergency",
    "post_rejected",
]


@pytest.mark.parametrize("path", _X1)
async def test_every_phase_change_writes_one_phase_event(
    w: ChangeWorld, port: FakeApprovalPort, path: str
) -> None:
    """X-1: one ``phase`` row per phase change on every non-engine path; extra rows only in
    addition (Task 17 adds the engine paths)."""
    await _cabs(w)
    await _settings(
        w, emergency_cab_id=ECAB, emergency_post_approval=True, auto_approve_low_risk=True
    )
    emergency = path in ("cancelled_emergency", "post_rejected")
    if path == "auto_template":
        tid = await _create(w, "standard", template_id=await _standard_template(w))
    else:
        tid = await _create(w, "emergency" if emergency else "normal")
    await _ready(w, tid, cab_id=None if emergency or path == "auto_template" else CAB)
    if path == "no_approvers":
        require_fake(port).reject_at_once = "no_approvers"
    if path in (
        "approved",
        "rejected",
        "no_approvers",
        "cancelled_normal",
        "agent_cancel_in_approval",
    ):
        await _at(w, tid, "assessment")
        await _go(w, tid, "approval")
    elif path in ("agent_approval", "agent_override", "auto_risk"):
        await _at(w, tid, "assessment")
        if path == "agent_override":
            await _blocking_freeze(w)
    elif path == "cancelled_emergency":
        await _go(w, tid, "approval")
    elif path == "post_rejected":
        await _go(w, tid, "implementing")
    elif path == "manager_force":
        await _at(w, tid, "implementing")
        await _open_task(w, tid)
    elif path == "agent_return":
        await _at(w, tid, "submitted")
    elif path == "agent_replan":
        await _at(w, tid, "scheduled", approval_via="cab", approved_at=_T)
    elif path == "agent_rework":
        await _at(w, tid, "rejected", approval_via="cab")
    elif path == "agent_close":
        await _at(w, tid, "review")
    before = {e.id for e in await _events(w, tid)}

    agent: dict[str, tuple[str, dict[str, Any], tuple[str, str], list[str]]] = {
        "agent_submit": ("submitted", {}, ("draft", "submitted"), []),
        "agent_return": ("draft", {}, ("submitted", "draft"), []),
        "agent_approval": ("approval", {}, ("assessment", "approval"), ["approval_requested"]),
        "agent_override": (
            "approval",
            {"override_reason": "Sicherheitsupdate"},
            ("assessment", "approval"),
            ["conflict_override", "approval_requested"],
        ),
        "agent_replan": ("draft", {}, ("scheduled", "draft"), []),
        "agent_rework": ("draft", {}, ("rejected", "draft"), []),
        "agent_cancel": ("cancelled", {}, ("draft", "cancelled"), []),
        "agent_cancel_in_approval": ("cancelled", {}, ("approval", "cancelled"), []),
        "agent_close": (
            "closed",
            {"outcome": "successful", "pir_notes": "ok"},
            ("review", "closed"),
            [],
        ),
        "auto_template": ("scheduled", {}, ("draft", "scheduled"), ["approval_outcome"]),
        "auto_risk": ("scheduled", {}, ("assessment", "scheduled"), ["approval_outcome"]),
        "manager_force": (
            "review",
            {"force": True, "reason": "obsolet"},
            ("implementing", "review"),
            ["forced"],
        ),
    }
    system: dict[str, tuple[str, str | None, tuple[str, str], list[str]]] = {
        "approved": ("approved", None, ("approval", "scheduled"), ["approval_outcome"]),
        "rejected": ("rejected", None, ("approval", "rejected"), ["approval_outcome"]),
        "no_approvers": (
            "rejected",
            "no_approvers",
            ("approval", "rejected"),
            ["approval_outcome"],
        ),
        "cancelled_normal": (
            "cancelled",
            "admin",
            ("approval", "assessment"),
            ["approval_outcome"],
        ),
        "cancelled_emergency": ("cancelled", "admin", ("approval", "draft"), ["approval_outcome"]),
        "post_rejected": (
            "rejected",
            None,
            ("implementing", "review"),
            ["approval_outcome", "forced"],
        ),
    }
    if path in agent:
        to, fields, expected, extra = agent[path]
        await _go(w, tid, to, U_MANAGER, **fields)
    else:
        status, reason, expected, extra = system[path]
        assert await _outcome(w, tid, status, reason) is True

    new = [e for e in await _events(w, tid) if e.id not in before]
    phase_rows = [(e.from_phase, e.to_phase) for e in new if e.event == "phase"]
    assert phase_rows == [expected]
    assert sorted(e.event for e in new if e.event != "phase") == sorted(extra)
    if path == "no_approvers":
        (outcome,) = [e for e in new if e.event == "approval_outcome"]
        assert _detail(outcome)["reason"] == "no_approvers"


async def test_approval_read_and_erased_snapshot(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await _cabs(w)
    tid = await _create(w)
    async with _api(w) as api:
        url = f"/api/v1/itsm/changes/{tid}/approval"
        got = await api.client.get(url)
        assert got.status_code == 200, got.text
        assert got.json() == {
            "approval_id": None,
            "approval_round": 0,
            "approval_via": None,
            "approved_at": None,
            "history_missing": False,
            "snapshot": None,
        }

        await _ready(w, tid)
        await _at(w, tid, "assessment")
        rec = (await _go(w, tid, "approval")).record
        got = (await api.client.get(url)).json()
        assert (got["approval_id"], got["approval_round"], got["history_missing"]) == (
            rec.approval_id,
            1,
            False,
        )
        snap = got["snapshot"]
        assert (snap["status"], snap["round"], snap["approval_key"]) == ("pending", 1, "change_cab")

        # CX14/N6: the approval set was erased (GDPR); the read still answers
        require_fake(port).snapshots.clear()
        got = await api.client.get(url)
        assert got.status_code == 200
        assert got.json()["snapshot"] is None and got.json()["history_missing"] is True


# --- concurrency (committed rows, own cleanup) -----------------------------------------------

U_C, G_C, Q_C, T_C = 947050, 947051, 947052, 947053


async def _seed_committed(url: str) -> None:
    engine, factory = make_factory(url)
    try:
        async with factory() as s:
            await apply_setup(s, ItsmSettings(), groups={"change"}, user_id=1)
            await s.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:i, 'chg94-conc', 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
                ),
                {"i": U_C, "t": _T},
            )
            await s.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, 'chg94-conc', 1, :t, 1, :t, 1)"
                ),
                {"id": G_C, "t": _T},
            )
            await s.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": U_C, "g": G_C, "t": _T},
            )
            await s.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, 'chg94-conc', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": Q_C, "g": G_C, "t": _T},
            )
            type_id = (
                await s.execute(text("SELECT id FROM ticket_type WHERE name = 'Change'"))
            ).scalar_one()
            state_id = (
                await s.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by) VALUES (:id, :tn,"
                    " 'Parallel', :q, 1, :ty, :u, 1, 3, :st, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": T_C,
                    "tn": f"20261001{T_C}",
                    "q": Q_C,
                    "ty": type_id,
                    "u": U_C,
                    "st": state_id,
                    "t": _T,
                },
            )
            await make_change(s, T_C)
            await s.commit()
    finally:
        await engine.dispose()


async def _submit(session: Any) -> ChangeView:
    return await transition(
        session,
        APP_SETTINGS,
        SysConfig(session),
        ticket_id=T_C,
        data=TransitionIn(to="submitted"),
        user_id=U_C,
    )


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_concurrent_transitions_serialised(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = plain_url(request, url_fixture)
    tables = (
        "tiqora_itsm_change_event",
        "tiqora_cache_invalidation",
        "tiqora_event_outbox",
        "dynamic_field_value",
        "ticket_history",
        "dynamic_field",
        "ticket_type",
        "ticket_state",
    )
    marks = snapshot_marks(url, tables)
    mode = stored(url, "itsm.mode")
    store(url, "itsm.mode", "tiqora")
    clear_detect_cache()
    engine, factory = make_factory(url)
    try:
        await _seed_committed(url)
        async with factory() as s1, factory() as s2:
            assert (await _submit(s1)).record.phase == "submitted"  # holds the row lock
            second = asyncio.create_task(_submit(s2))
            await asyncio.sleep(1.0)
            assert not second.done()  # waits on FOR UPDATE
            await s1.commit()
            with pytest.raises(ChangeError) as exc:
                await asyncio.wait_for(second, timeout=30)
            await s2.rollback()
        assert exc.value.error_code == "transition_refused"
        assert exc.value.extra["failures"] == [
            {"code": "transition_not_allowed", "overridable": False}
        ]
        async with factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT from_phase, to_phase FROM tiqora_itsm_change_event"
                        " WHERE ticket_id = :t AND event = 'phase'"
                    ),
                    {"t": T_C},
                )
            ).all()
        assert [tuple(r) for r in rows] == [("draft", "submitted")]
    finally:
        await engine.dispose()
        delete_above_marks(
            url,
            marks,
            first=[
                ("DELETE FROM tiqora_itsm_change_event WHERE ticket_id = :t", {"t": T_C}),
                ("DELETE FROM tiqora_itsm_change WHERE ticket_id = :t", {"t": T_C}),
                ("DELETE FROM dynamic_field_value WHERE object_id = :t", {"t": T_C}),
                ("DELETE FROM ticket_history WHERE ticket_id = :t", {"t": T_C}),
                ("DELETE FROM ticket WHERE id = :t", {"t": T_C}),
                ("DELETE FROM queue WHERE id = :q", {"q": Q_C}),
                ("DELETE FROM group_user WHERE user_id = :u", {"u": U_C}),
                ("DELETE FROM permission_groups WHERE id = :g", {"g": G_C}),
                ("DELETE FROM users WHERE id = :u", {"u": U_C}),
            ],
        )
        store(url, "itsm.mode", mode)
        clear_detect_cache()
