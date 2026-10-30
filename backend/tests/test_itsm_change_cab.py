"""CAB boards, members, meetings, agenda and the approval request built from them.

Seed band 947100-947199: CABs 947101-947110, role 947120. Users, groups, queues and the change
manager come from ``tests._change_fixtures``; changes are created by the services inside the
rolled-back world, the approval engine is the in-memory fake port.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    Q_A,
    Q_B,
    U_FULL,
    U_MANAGER,
    URL_FIXTURES,
    ChangeWorld,
    FakeApprovalPort,
    approval_port_for,
    change_world,
    make_cab,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.approvals import lists as approval_lists
from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApproverEntry,
    Principal,
    StepSpec,
)
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.cab import (
    AgendaItemIn,
    CabIn,
    CabMemberIn,
    MeetingIn,
    MeetingUpdateIn,
    create_cab,
    create_meeting,
    delete_cab,
    get_meeting,
    list_cabs,
    list_meetings,
    resolved_members,
    set_agenda,
    set_members,
    suggest_agenda,
    update_cab,
    update_meeting,
)
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import TiqoraItsmCabAgendaItem, TiqoraItsmChange
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.transitions import TransitionIn, transition
from tiqora.permissions.engine import PermissionEngine
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

ROLE = 947120
#: chg94-ro; one test marks it invalid inside the rolled-back world
U_RO_INVALID = 946303
CAB_A, CAB_B, CAB_C = 947101, 947102, 947103
_T = datetime(2026, 10, 1)
SOON = datetime(2026, 11, 5, 9, 0, 0)
WINDOW = (datetime(2026, 11, 2, 8, 0, 0), datetime(2026, 11, 2, 10, 0, 0))
RANGE = (datetime(2026, 11, 1), datetime(2026, 12, 1))


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


def _members(*pairs: tuple[str, str]) -> list[CabMemberIn]:
    return [CabMemberIn(member_kind=k, member_ref=r) for k, r in pairs]  # type: ignore[arg-type]


async def _engine_members(w: ChangeWorld) -> int:
    """Customer approvers on (CX21) and one Freigabegruppe (N4); returns the list id."""
    await save_section_values(
        w.session,
        APPROVALS_SECTION,
        {"customer_approvers_enabled": True},
        secret_key=APP_SETTINGS.secret_key,
    )
    found = await approval_lists.create_list(
        w.session, name="chg94 Freigabegruppe", member_ids=[U_MANAGER], actor_id=1
    )
    return found.id


async def _role(w: ChangeWorld, *, user_id: int = U_FULL) -> None:
    await w.session.execute(
        text(
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'chg94-role', 1, :t, 1, :t, 1)"
        ),
        {"id": ROLE, "t": _T},
    )
    await w.session.execute(
        text(
            "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
            " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
        ),
        {"u": user_id, "r": ROLE, "t": _T},
    )


async def _create(
    w: ChangeWorld, *, queue_id: int = Q_A, user_id: int = U_FULL, **fields: Any
) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": "normal", "queue_id": queue_id, **fields}
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


async def _at(w: ChangeWorld, tid: int, phase: str, **fields: Any) -> TiqoraItsmChange:
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    await w.session.refresh(row)
    row.phase = phase
    for name, value in fields.items():
        setattr(row, name, value)
    await w.session.flush()
    return row


async def _move(w: ChangeWorld, tid: int, queue_id: int) -> None:
    """The ticket changes queue behind the change's back (``Q_B`` is out of reach of the agents)."""
    await w.session.execute(
        text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": queue_id, "t": tid}
    )


async def _meeting(w: ChangeWorld, cab_id: int = CAB_A, user_id: int = U_MANAGER) -> int:
    view = await create_meeting(
        w.session,
        APP_SETTINGS,
        data=MeetingIn(
            cab_id=cab_id, title="CAB Woche 45", starts_at=SOON, ends_at=SOON + timedelta(hours=1)
        ),
        user_id=user_id,
    )
    return view.id


def _agenda(*ids: int) -> list[AgendaItemIn]:
    return [AgendaItemIn(change_ticket_id=i) for i in ids]


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
async def _api(w: ChangeWorld, uid: int = 1) -> AsyncIterator[_Api]:
    who = {"u": _user(uid)}

    async def _db() -> Any:
        yield w.session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
    app.dependency_overrides[get_current_user] = lambda: who["u"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield _Api(client, who)


def _code(exc: pytest.ExceptionInfo[ChangeError]) -> str:
    return exc.value.error_code


# --- boards ----------------------------------------------------------------------------------


async def test_cab_crud_and_members(w: ChangeWorld) -> None:
    # the quorum belongs to the rule "quorum" and to nothing else
    with pytest.raises(ValidationError):
        CabIn(name="x", rule="quorum")
    with pytest.raises(ValidationError):
        CabIn(name="x", rule="majority", quorum=2)

    cab = await create_cab(
        w.session, data=CabIn(name="chg94 CAB Alpha", rule="quorum", quorum=2), user_id=1
    )
    assert (cab.rule, cab.quorum, cab.members, cab.valid) == ("quorum", 2, [], True)
    with pytest.raises(ChangeError) as dup:
        await create_cab(w.session, data=CabIn(name="chg94 CAB Alpha"), user_id=1)
    assert (_code(dup), dup.value.http_status) == ("cab_name_exists", 409)

    # unknown user / group / role -> 422
    for kind, code in (
        ("user", "user_not_found"),
        ("group", "group_not_found"),
        ("role", "role_not_found"),
    ):
        with pytest.raises(ChangeError) as unknown:
            await set_members(
                w.session, cab_id=cab.id, members=_members((kind, "947199")), user_id=1
            )
        assert (_code(unknown), unknown.value.http_status) == (code, 422)
    # an invalid user does not count either
    await w.session.execute(
        text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": U_RO_INVALID}
    )
    with pytest.raises(ChangeError) as invalid:
        await set_members(
            w.session, cab_id=cab.id, members=_members(("user", str(U_RO_INVALID))), user_id=1
        )
    assert _code(invalid) == "user_not_found"

    # shape errors
    with pytest.raises(ChangeError) as shape:
        await set_members(w.session, cab_id=cab.id, members=_members(("user", "abc")), user_id=1)
    assert _code(shape) == "cab_member_invalid"
    with pytest.raises(ChangeError) as twice:
        await set_members(
            w.session,
            cab_id=cab.id,
            members=_members(("user", str(U_FULL)), ("user", str(U_FULL))),
            user_id=1,
        )
    assert _code(twice) == "cab_member_duplicate"
    with pytest.raises(ChangeError) as many:
        await set_members(
            w.session,
            cab_id=cab.id,
            members=_members(*[("customer_user", f"chg94-c{i}@example.org") for i in range(51)]),
            user_id=1,
        )
    assert _code(many) == "cab_too_many_members"

    # quorum 2 cannot be reached by one individual (the engine's pure rule)
    with pytest.raises(ChangeError) as unreachable:
        await set_members(
            w.session, cab_id=cab.id, members=_members(("user", str(U_FULL))), user_id=1
        )
    assert (_code(unreachable), unreachable.value.http_status) == ("cab_invalid", 422)
    # A-M8: a stable reason next to the engine's English text
    assert unreachable.value.extra["reason"] == "quorum_exceeds_members"
    assert unreachable.value.detail["reason"] == "quorum_exceeds_members"

    await _role(w)
    list_id = await _engine_members(w)
    members = _members(
        ("user", str(U_FULL)),
        ("group", str(G_A)),
        ("role", str(ROLE)),
        ("list", str(list_id)),
        ("customer_user", "chg94-cust@example.org"),
        ("resolver", "ticket.owner"),
    )
    done = await set_members(w.session, cab_id=cab.id, members=members, user_id=1)
    assert [(m.member_kind, m.member_ref) for m in done.members] == [
        (m.member_kind, m.member_ref) for m in members
    ]  # order kept
    assert [c.id for c in await list_cabs(w.session) if c.id == cab.id] == [cab.id]

    # a replace is a replace
    done = await set_members(
        w.session,
        cab_id=cab.id,
        members=_members(("user", str(U_FULL)), ("user", str(U_MANAGER))),
        user_id=1,
    )
    assert len(done.members) == 2

    # the new rule must fit the members that stay
    with pytest.raises(ChangeError) as fit:
        await update_cab(
            w.session,
            cab_id=cab.id,
            data=CabIn(name="chg94 CAB Alpha", rule="quorum", quorum=3),
            user_id=1,
        )
    assert _code(fit) == "cab_invalid"
    assert fit.value.extra["reason"] == "quorum_exceeds_members"
    renamed = await update_cab(
        w.session,
        cab_id=cab.id,
        data=CabIn(name="chg94 CAB Beta", rule="all", is_emergency=True, valid=False),
        user_id=U_MANAGER,
    )
    assert (renamed.name, renamed.rule, renamed.quorum, renamed.is_emergency, renamed.valid) == (
        "chg94 CAB Beta",
        "all",
        None,
        True,
        False,
    )
    assert renamed.updated_by == U_MANAGER and len(renamed.members) == 2
    assert [c.id for c in await list_cabs(w.session, include_invalid=False) if c.id == cab.id] == []

    await create_cab(w.session, data=CabIn(name="chg94 CAB Gamma"), user_id=1)
    with pytest.raises(ChangeError) as clash:
        await update_cab(w.session, cab_id=cab.id, data=CabIn(name="chg94 CAB Gamma"), user_id=1)
    assert (_code(clash), clash.value.http_status) == ("cab_name_exists", 409)

    with pytest.raises(ChangeError) as missing:
        await update_cab(w.session, cab_id=947198, data=CabIn(name="n"), user_id=1)
    assert (_code(missing), missing.value.http_status) == ("cab_not_found", 404)

    await delete_cab(w.session, cab_id=cab.id)
    with pytest.raises(ChangeError):
        await update_cab(w.session, cab_id=cab.id, data=CabIn(name="n"), user_id=1)


async def test_cab_admin_api(w: ChangeWorld) -> None:
    async with _api(w) as api:
        created = await api.client.post(
            "/api/v1/admin/itsm/change/cabs", json={"name": "chg94 API CAB", "rule": "any"}
        )
        assert created.status_code == 201, created.text
        cab_id = created.json()["id"]
        put = await api.client.put(
            f"/api/v1/admin/itsm/change/cabs/{cab_id}/members",
            json=[{"member_kind": "user", "member_ref": str(U_FULL)}],
        )
        assert put.status_code == 200, put.text
        assert put.json()["members"][0]["label"] == "Erika Beispiel"
        bad = await api.client.put(
            f"/api/v1/admin/itsm/change/cabs/{cab_id}/members",
            json=[{"member_kind": "user", "member_ref": "947199"}],
        )
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "user_not_found"
        body = await api.client.post(
            "/api/v1/admin/itsm/change/cabs", json={"name": "x", "rule": "quorum"}
        )
        assert body.status_code == 422
        listed = await api.client.get("/api/v1/admin/itsm/change/cabs")
        assert [c["id"] for c in listed.json()] == [cab_id]

        api.login(U_FULL)  # not an admin
        assert (await api.client.get("/api/v1/admin/itsm/change/cabs")).status_code == 403
        # agents read the valid CABs
        agent = await api.client.get("/api/v1/itsm/cabs")
        assert agent.status_code == 200 and [c["id"] for c in agent.json()] == [cab_id]

        api.login(1)
        assert (
            await api.client.delete(f"/api/v1/admin/itsm/change/cabs/{cab_id}")
        ).status_code == 204


async def test_cab_delete_refused_with_meetings(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    await make_cab(w.session, cab_id=CAB_B)
    await _meeting(w, CAB_A)
    with pytest.raises(ChangeError) as refused:
        await delete_cab(w.session, cab_id=CAB_A)
    assert (_code(refused), refused.value.http_status) == ("cab_has_meetings", 409)
    await delete_cab(w.session, cab_id=CAB_B)  # without meetings it goes

    async with _api(w) as api:
        answer = await api.client.delete(f"/api/v1/admin/itsm/change/cabs/{CAB_A}")
        assert answer.status_code == 409
        assert answer.json()["detail"]["code"] == "cab_has_meetings"
    kept = await list_cabs(w.session)
    assert [c.id for c in kept if c.id in (CAB_A, CAB_B)] == [CAB_A]


async def test_resolved_members_display(w: ChangeWorld) -> None:
    await _role(w)
    await make_cab(
        w.session,
        cab_id=CAB_A,
        members=[
            ("user", str(U_FULL)),
            ("group", str(G_A)),
            ("role", str(ROLE)),
            ("list", "42"),
            ("customer_user", "chg94-cust@example.org"),
            ("resolver", "ticket_owner"),
            ("user", "947198"),
        ],
    )
    shown = {(m.member_kind, m.member_ref): m for m in await resolved_members(w.session, CAB_A)}
    assert shown[("user", str(U_FULL))].label == "Erika Beispiel"
    assert shown[("user", str(U_FULL))].count is None

    voters = await PermissionEngine(w.session).users_for_group_permission(G_A, "rw")
    group = shown[("group", str(G_A))]
    assert (group.label, group.count, group.missing) == ("chg94-a", len(voters), False)
    assert group.count == 2  # U_FULL and the manager hold rw on the group

    role = shown[("role", str(ROLE))]
    assert (role.label, role.count) == ("chg94-role", 1)
    gone_list = shown[("list", "42")]  # no such Freigabegruppe (any more)
    assert (gone_list.label, gone_list.count, gone_list.missing) == ("Freigabegruppe #42", 0, True)
    customer = shown[("customer_user", "chg94-cust@example.org")]
    assert (customer.label, customer.missing) == ("chg94-cust@example.org", True)
    assert shown[("resolver", "ticket_owner")].label == "ticket_owner"
    gone = shown[("user", "947198")]
    assert (gone.label, gone.missing) == ("#947198", True)

    # a retired group stays visible and counts nobody
    await w.session.execute(
        text("UPDATE permission_groups SET valid_id = 2 WHERE id = :g"), {"g": G_A}
    )
    retired = {(m.member_kind, m.member_ref): m for m in await resolved_members(w.session, CAB_A)}
    assert (retired[("group", str(G_A))].missing, retired[("group", str(G_A))].count) == (True, 0)


# --- meetings --------------------------------------------------------------------------------


async def test_meetings_manager_write_agent_read(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    data = MeetingIn(
        cab_id=CAB_A, title="CAB Woche 45", starts_at=SOON, ends_at=SOON + timedelta(hours=1)
    )
    with pytest.raises(ChangeError) as no:
        await create_meeting(w.session, APP_SETTINGS, data=data, user_id=U_FULL)
    assert (_code(no), no.value.http_status) == ("forbidden", 403)

    with pytest.raises(ValidationError):  # the end follows the start
        MeetingIn(cab_id=CAB_A, title="x", starts_at=SOON, ends_at=SOON)
    await make_cab(w.session, cab_id=CAB_B, valid=False)
    with pytest.raises(ChangeError) as invalid:
        await create_meeting(
            w.session,
            APP_SETTINGS,
            data=data.model_copy(update={"cab_id": CAB_B}),
            user_id=U_MANAGER,
        )
    assert (_code(invalid), invalid.value.http_status) == ("cab_not_found", 422)

    created = await create_meeting(w.session, APP_SETTINGS, data=data, user_id=U_MANAGER)
    assert (created.status, created.cab_name, created.agenda_count, created.created_by) == (
        "planned",
        "chg94 CAB 947101",
        0,
        U_MANAGER,
    )
    # admins manage as well
    assert (await create_meeting(w.session, APP_SETTINGS, data=data, user_id=1)).id != created.id

    # agents read
    seen = await list_meetings(w.session, start=RANGE[0], end=RANGE[1], user_id=U_FULL)
    assert created.id in [m.id for m in seen]
    assert (
        await list_meetings(
            w.session, start=datetime(2027, 1, 1), end=datetime(2027, 2, 1), user_id=U_FULL
        )
        == []
    )
    assert (
        await get_meeting(w.session, meeting_id=created.id, user_id=U_FULL)
    ).title == "CAB Woche 45"
    with pytest.raises(ChangeError) as absent:
        await get_meeting(w.session, meeting_id=947199, user_id=U_FULL)
    assert _code(absent) == "meeting_not_found"

    # ... but do not write
    with pytest.raises(ChangeError) as agent_edit:
        await update_meeting(
            w.session,
            APP_SETTINGS,
            meeting_id=created.id,
            data=MeetingUpdateIn(status="held"),
            user_id=U_FULL,
        )
    assert agent_edit.value.http_status == 403
    with pytest.raises(ChangeError) as agent_agenda:
        await set_agenda(w.session, APP_SETTINGS, meeting_id=created.id, items=[], user_id=U_FULL)
    assert agent_agenda.value.http_status == 403

    held = await update_meeting(
        w.session,
        APP_SETTINGS,
        meeting_id=created.id,
        data=MeetingUpdateIn(status="held", minutes="Beschluss: Freigabe", location="Raum 1"),
        user_id=U_MANAGER,
    )
    assert (held.status, held.minutes, held.location) == ("held", "Beschluss: Freigabe", "Raum 1")
    cleared = await update_meeting(
        w.session,
        APP_SETTINGS,
        meeting_id=created.id,
        data=MeetingUpdateIn(location=""),
        user_id=U_MANAGER,
    )
    assert (cleared.location, cleared.minutes, cleared.status) == (
        None,
        "Beschluss: Freigabe",
        "held",
    )
    with pytest.raises(ChangeError) as flipped:
        await update_meeting(
            w.session,
            APP_SETTINGS,
            meeting_id=created.id,
            data=MeetingUpdateIn(ends_at=SOON - timedelta(hours=1)),
            user_id=U_MANAGER,
        )
    assert _code(flipped) == "range_invalid"
    # a held meeting no longer takes an agenda
    with pytest.raises(ChangeError) as closed:
        await set_agenda(
            w.session, APP_SETTINGS, meeting_id=created.id, items=[], user_id=U_MANAGER
        )
    assert (_code(closed), closed.value.http_status) == ("meeting_not_planned", 409)


async def test_meetings_api(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    async with _api(w, U_MANAGER) as api:
        made = await api.client.post(
            "/api/v1/itsm/cab-meetings",
            json={
                "cab_id": CAB_A,
                "title": "CAB Woche 45",
                "starts_at": "2026-11-05T09:00:00Z",
                "ends_at": "2026-11-05T10:00:00Z",
            },
        )
        assert made.status_code == 201, made.text
        mid = made.json()["id"]
        noted = await api.client.patch(
            f"/api/v1/itsm/cab-meetings/{mid}", json={"minutes": "Beschluss: Freigabe"}
        )
        assert noted.status_code == 200, noted.text
        api.login(U_FULL)
        listed = await api.client.get(
            "/api/v1/itsm/cab-meetings",
            params={"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
        )
        assert [m["id"] for m in listed.json()] == [mid]
        # B-M1: the list carries no minutes and no agenda; the meeting itself has them
        assert not {"minutes", "agenda"} & listed.json()[0].keys()
        assert listed.json()[0]["agenda_count"] == 0
        one = await api.client.get(f"/api/v1/itsm/cab-meetings/{mid}")
        assert one.status_code == 200 and one.json()["starts_at"].startswith("2026-11-05T09:00:00")
        assert one.json()["minutes"] == "Beschluss: Freigabe"
        denied = await api.client.patch(f"/api/v1/itsm/cab-meetings/{mid}", json={"status": "held"})
        assert denied.status_code == 403
        assert (
            await api.client.put(f"/api/v1/itsm/cab-meetings/{mid}/agenda", json={"items": []})
        ).status_code == 403
        wide = await api.client.get(
            "/api/v1/itsm/cab-meetings",
            params={"start": "2026-01-01T00:00:00Z", "end": "2026-12-01T00:00:00Z"},
        )
        assert wide.status_code == 422 and wide.json()["detail"]["code"] == "range_too_large"
        sugg = await api.client.get(f"/api/v1/itsm/cab-meetings/{mid}/suggestions")
        assert sugg.status_code == 200 and sugg.json() == []


# --- agenda ----------------------------------------------------------------------------------


async def test_agenda_only_open_changes_and_readable(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    mid = await _meeting(w)
    draft = await _create(w)
    assessing = await _at(w, await _create(w), "assessment")
    approving = await _at(w, await _create(w), "approval")
    scheduled = await _at(w, await _create(w), "scheduled")
    implementing = await _at(w, await _create(w), "implementing")
    closed = await _at(w, await _create(w), "closed")
    elsewhere = await _create(w)
    await _at(w, elsewhere, "approval")
    await _move(w, elsewhere, Q_B)  # the manager cannot read queue B

    for tid, code in (
        (draft, "change_not_open"),
        (implementing.ticket_id, "change_not_open"),
        (closed.ticket_id, "change_not_open"),
        (elsewhere, "change_not_found"),
        (947199, "change_not_found"),  # missing and unreadable answer alike
    ):
        with pytest.raises(ChangeError) as refused:
            await set_agenda(
                w.session, APP_SETTINGS, meeting_id=mid, items=_agenda(tid), user_id=U_MANAGER
            )
        assert (_code(refused), refused.value.http_status) == (code, 422), tid
    with pytest.raises(ChangeError) as twice:
        await set_agenda(
            w.session,
            APP_SETTINGS,
            meeting_id=mid,
            items=_agenda(assessing.ticket_id, assessing.ticket_id),
            user_id=U_MANAGER,
        )
    assert _code(twice) == "agenda_duplicate"

    order = [scheduled.ticket_id, approving.ticket_id, assessing.ticket_id]
    items = [AgendaItemIn(change_ticket_id=t, note=f"Punkt {i}") for i, t in enumerate(order)]
    view = await set_agenda(w.session, APP_SETTINGS, meeting_id=mid, items=items, user_id=U_MANAGER)
    assert [(e.position, e.change_ticket_id, e.phase, e.note) for e in view.agenda] == [
        (1, order[0], "scheduled", "Punkt 0"),
        (2, order[1], "approval", "Punkt 1"),
        (3, order[2], "assessment", "Punkt 2"),
    ]
    assert view.agenda_count == 3 and not any(e.hidden for e in view.agenda)
    assert view.agenda[0].title == "Switch-Tausch" and view.agenda[0].tn

    # a change that left the open phases stays on the agenda it was already on; others cannot join
    await _at(w, assessing.ticket_id, "implementing")
    kept = await set_agenda(
        w.session,
        APP_SETTINGS,
        meeting_id=mid,
        items=_agenda(assessing.ticket_id, approving.ticket_id),
        user_id=U_MANAGER,
    )
    assert [e.change_ticket_id for e in kept.agenda] == [assessing.ticket_id, approving.ticket_id]
    emptied = await set_agenda(w.session, APP_SETTINGS, meeting_id=mid, items=[], user_id=U_MANAGER)
    assert emptied.agenda == [] and emptied.agenda_count == 0


async def test_agenda_hides_unreadable_change(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    mid = await _meeting(w)
    open_change = await _at(w, await _create(w), "approval", approval_id=None)
    secret = await _at(w, await _create(w), "approval")
    view = await set_agenda(
        w.session,
        APP_SETTINGS,
        meeting_id=mid,
        items=_agenda(secret.ticket_id, open_change.ticket_id),
        user_id=U_MANAGER,
    )
    assert [e.hidden for e in view.agenda] == [False, False]
    secret_id = secret.ticket_id
    await _move(w, secret_id, Q_B)  # later the ticket moves to a queue nobody here reads

    for who in (U_MANAGER, U_FULL):
        seen = await get_meeting(w.session, meeting_id=mid, user_id=who)
        assert seen.agenda_count == 2
        hidden, shown = seen.agenda
        assert hidden.model_dump(exclude_none=True) == {"position": 1, "hidden": True}
        assert (shown.hidden, shown.change_ticket_id) == (False, open_change.ticket_id)

    # the manager reorders what they read; the line they cannot read stays
    again = await set_agenda(
        w.session,
        APP_SETTINGS,
        meeting_id=mid,
        items=_agenda(open_change.ticket_id),
        user_id=U_MANAGER,
    )
    assert [(e.hidden, e.change_ticket_id) for e in again.agenda] == [
        (False, open_change.ticket_id),
        (True, None),
    ]
    stored = (
        await w.session.execute(
            select(TiqoraItsmCabAgendaItem.change_ticket_id).where(
                TiqoraItsmCabAgendaItem.meeting_id == mid
            )
        )
    ).scalars()
    assert secret_id in set(stored)

    # through the API the hidden line carries nothing else
    async with _api(w, U_FULL) as api:
        body = (await api.client.get(f"/api/v1/itsm/cab-meetings/{mid}")).json()
        assert body["agenda"][1] == {
            "position": 2,
            "hidden": True,
            "change_ticket_id": None,
            "tn": None,
            "title": None,
            "change_type": None,
            "phase": None,
            "planned_start": None,
            "planned_end": None,
            "note": None,
            "approval": None,
        }


async def test_agenda_shows_approval_snapshot(w: ChangeWorld, port: FakeApprovalPort) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    mid = await _meeting(w)
    tid = await _create(w)
    snap = await port.request(  # the engine (fake or real) knows the set the change points at
        w.session,
        ticket_id=tid,
        round=1,
        title="t",
        description="",
        definition=ApprovalDefinition(
            steps=[StepSpec(name="CAB", approvers=[ApproverEntry(kind="user", ref=str(U_MANAGER))])]
        ),
        actor=Actor.agent(U_FULL),
        requester=Principal.agent(U_FULL),
        allow_self_approval=False,
    )
    await _at(w, tid, "approval", approval_id=snap.approval_id, approval_round=1)
    view = await set_agenda(
        w.session, APP_SETTINGS, meeting_id=mid, items=_agenda(tid), user_id=U_MANAGER
    )
    (line,) = view.agenda
    assert line.approval is not None
    assert (line.approval.status, line.approval.round) == ("pending", 1)


async def test_suggest_agenda(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    await make_cab(w.session, cab_id=CAB_B, members=[("user", str(U_FULL))])
    await save_section_values(
        w.session,
        ITSM_CHANGE_SECTION,
        {"default_cab_id": CAB_B},
        secret_key=APP_SETTINGS.secret_key,
    )
    own = await _at(w, await _create(w, cab_id=CAB_A), "approval", planned_start=WINDOW[1])
    sooner = await _at(w, await _create(w, cab_id=CAB_A), "approval", planned_start=WINDOW[0])
    undated = await _at(w, await _create(w, cab_id=CAB_A), "approval")
    other = await _at(w, await _create(w, cab_id=CAB_B), "approval")
    by_default = await _at(w, await _create(w), "approval")  # no CAB of its own -> default CAB
    waiting = await _at(w, await _create(w, cab_id=CAB_A), "assessment")
    secret = await _create(w, cab_id=CAB_A)
    await _at(w, secret, "approval")
    await _move(w, secret, Q_B)

    got = await suggest_agenda(w.session, APP_SETTINGS, cab_id=CAB_A, user_id=U_MANAGER)
    assert [i.ticket_id for i in got] == [sooner.ticket_id, own.ticket_id, undated.ticket_id]
    assert got[0].phase == "approval" and got[0].title == "Switch-Tausch"
    assert {waiting.ticket_id, other.ticket_id, secret}.isdisjoint(i.ticket_id for i in got)

    default = await suggest_agenda(w.session, APP_SETTINGS, cab_id=CAB_B, user_id=U_MANAGER)
    assert {i.ticket_id for i in default} == {other.ticket_id, by_default.ticket_id}

    with pytest.raises(ChangeError) as absent:
        await suggest_agenda(w.session, APP_SETTINGS, cab_id=947199, user_id=1)
    assert _code(absent) == "cab_not_found"


# --- the approval request --------------------------------------------------------------------


async def test_approval_request_uses_cab_rule_and_members(
    w: ChangeWorld, port: FakeApprovalPort
) -> None:
    """The CAB as an admin edits it is what the transition hands to the approval engine."""
    cab = await create_cab(
        w.session, data=CabIn(name="chg94 CAB Freigabe", rule="quorum", quorum=2), user_id=1
    )
    list_id = await _engine_members(w)
    await set_members(
        w.session,
        cab_id=cab.id,
        members=_members(
            ("user", str(U_MANAGER)),
            ("group", str(G_A)),
            ("user", str(U_FULL)),
            ("list", str(list_id)),
            ("customer_user", "chg94-cust@example.org"),
        ),
        user_id=1,
    )
    tid = await _create(w, cab_id=cab.id)
    row = await _at(
        w,
        tid,
        "assessment",
        implementation_plan="Switch tauschen",
        backout_plan="Alten Switch einbauen",
        planned_start=WINDOW[0],
        planned_end=WINDOW[1],
    )
    row.impact_computed_at = datetime(2099, 1, 1)
    await w.session.flush()

    view = await transition(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        data=TransitionIn.model_validate({"to": "approval"}),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    assert (view.record.phase, view.record.approval_via) == ("approval", "cab")
    (req,) = port.requests
    (step,) = req.definition.steps
    assert (step.name, step.rule, step.quorum) == ("chg94 CAB Freigabe", "quorum", 2)
    assert [(a.kind, a.ref) for a in step.approvers] == [
        ("user", str(U_MANAGER)),
        ("group", str(G_A)),
        ("user", str(U_FULL)),
        ("list", str(list_id)),
        ("customer_user", "chg94-cust@example.org"),
    ]

    # the board a change names also decides while it stays in the suggestions of its CAB
    suggested = await suggest_agenda(w.session, APP_SETTINGS, cab_id=cab.id, user_id=U_MANAGER)
    assert [i.ticket_id for i in suggested] == [tid]


async def test_suggest_agenda_emergency_cab_meeting_and_invalid(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    await make_cab(w.session, cab_id=CAB_B, members=[("user", str(U_FULL))])
    await make_cab(w.session, cab_id=CAB_C, members=[("user", str(U_FULL))], valid=False)
    await save_section_values(
        w.session,
        ITSM_CHANGE_SECTION,
        {"default_cab_id": CAB_A, "emergency_cab_id": CAB_B},
        secret_key=APP_SETTINGS.secret_key,
    )
    emergency = await _at(w, await _create(w, change_type="emergency"), "approval")
    normal = await _at(w, await _create(w), "approval")

    ecab = await suggest_agenda(w.session, APP_SETTINGS, cab_id=CAB_B, user_id=U_MANAGER)
    assert [i.ticket_id for i in ecab] == [emergency.ticket_id]
    default = await suggest_agenda(w.session, APP_SETTINGS, cab_id=CAB_A, user_id=U_MANAGER)
    assert [i.ticket_id for i in default] == [normal.ticket_id]  # emergency has its own CAB

    mid = await _meeting(w, CAB_A)
    await set_agenda(
        w.session, APP_SETTINGS, meeting_id=mid, items=_agenda(normal.ticket_id), user_id=U_MANAGER
    )
    left = await suggest_agenda(
        w.session, APP_SETTINGS, cab_id=CAB_A, user_id=U_MANAGER, meeting_id=mid
    )
    assert left == []  # already on this agenda

    with pytest.raises(ChangeError) as invalid:
        await suggest_agenda(w.session, APP_SETTINGS, cab_id=CAB_C, user_id=U_MANAGER)
    assert (_code(invalid), invalid.value.http_status) == ("cab_not_found", 422)


async def test_delete_cab_referenced_by_change_clears_reference(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A, members=[("user", str(U_FULL))])
    tid = await _create(w, cab_id=CAB_A)
    await delete_cab(w.session, cab_id=CAB_A)
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    await w.session.refresh(row)
    assert row.cab_id is None  # ON DELETE SET NULL


async def test_update_meeting_invalid_window_changes_nothing(w: ChangeWorld) -> None:
    await make_cab(w.session, cab_id=CAB_A)
    mid = await _meeting(w)
    with pytest.raises(ChangeError):
        await update_meeting(
            w.session,
            APP_SETTINGS,
            meeting_id=mid,
            data=MeetingUpdateIn(title="neu", ends_at=SOON - timedelta(hours=1)),
            user_id=U_MANAGER,
        )
    await w.session.flush()  # a caller that goes on with the session flushes nothing wrong
    kept = await get_meeting(w.session, meeting_id=mid, user_id=U_MANAGER)
    assert (kept.title, kept.ends_at) == ("CAB Woche 45", SOON + timedelta(hours=1))
