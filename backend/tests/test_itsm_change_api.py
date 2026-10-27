"""Change API: ``/itsm/changes``, ``/tickets/{id}/change`` (both dialects).

Seed band 946300-946399 (see ``tests._change_fixtures``). The routes commit; the test owns the
transaction (``commit`` is ``flush``), so nothing leaks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests._change_fixtures import (
    Q_A,
    Q_B,
    SERVICE,
    U_CREATE,
    U_FULL,
    U_MANAGER,
    U_NONE,
    U_RO,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

T_PLAIN = 946340


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid,
        login=f"chg94.{uid}",
        first_name="Erika",
        last_name="Beispiel",
        auth_method="session",
    )


@dataclass
class Api:
    client: AsyncClient
    world: ChangeWorld
    as_user: dict[str, AuthenticatedUser]

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)

    async def create(self, **fields: Any) -> dict[str, Any]:
        body = {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
        got = await self.client.post("/api/v1/itsm/changes", json=body)
        assert got.status_code == 201, got.text
        return dict(got.json()["change"])


@pytest.fixture(params=URL_FIXTURES)
async def api(request: pytest.FixtureRequest) -> AsyncIterator[Api]:
    async with change_world(request, str(request.param)) as world:
        who = {"u": _user(U_FULL)}

        async def _db() -> Any:
            yield world.session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: world.factory
        app.dependency_overrides[get_current_user] = lambda: who["u"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Api(client, world, who)


async def test_create_get_patch_events(api: Api) -> None:
    created = await api.client.post(
        "/api/v1/itsm/changes",
        json={
            "title": "Switch-Tausch",
            "change_type": "normal",
            "queue_id": Q_A,
            "service_id": SERVICE,
            "body": "Details",
            "planned_start": "2026-11-01T08:00:00Z",
            "planned_end": "2026-11-01T10:00:00+00:00",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["tasks_job_id"] is None
    change = body["change"]
    tid = change["ticket_id"]
    assert (change["phase"], change["change_type"], change["ticket_state"]) == (
        "draft",
        "normal",
        "new",
    )
    assert change["planned_start"] == "2026-11-01T08:00:00+00:00"
    assert change["service_ids"] == [SERVICE] and change["drift"] == []
    assert change["can_write"] is True and change["can_manage"] is False
    assert "submitted" in change["allowed_targets"]

    got = await api.client.get(f"/api/v1/itsm/changes/{tid}")
    assert got.status_code == 200 and got.json() == change

    patched = await api.client.patch(
        f"/api/v1/itsm/changes/{tid}", json={"reason": "Grund", "implementation_plan": "Plan"}
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["reason"], patched.json()["implementation_plan"]) == ("Grund", "Plan")

    events = await api.client.get(f"/api/v1/itsm/changes/{tid}/events")
    assert events.status_code == 200
    rows = events.json()
    assert [e["event"] for e in rows] == ["updated", "phase", "created"]
    assert rows[0]["detail"] == {"fields": ["reason", "implementation_plan"]}
    assert (rows[1]["from_phase"], rows[1]["to_phase"]) == (None, "draft")
    older = await api.client.get(
        f"/api/v1/itsm/changes/{tid}/events", params={"limit": 1, "before_id": rows[0]["id"]}
    )
    assert [e["event"] for e in older.json()] == ["phase"]
    assert (
        await api.client.get(f"/api/v1/itsm/changes/{tid}/events", params={"limit": 201})
    ).status_code == 422


async def test_patch_cannot_set_phase(api: Api) -> None:
    tid = (await api.create())["ticket_id"]
    for body in (
        {"phase": "closed"},
        {"reason": "x", "phase": "approval"},
        {"outcome": "successful"},
    ):
        refused = await api.client.patch(f"/api/v1/itsm/changes/{tid}", json=body)
        assert refused.status_code == 422, refused.text
    assert (await api.client.get(f"/api/v1/itsm/changes/{tid}")).json()["phase"] == "draft"


async def test_access_codes(api: Api) -> None:
    tid = (await api.create())["ticket_id"]
    url = f"/api/v1/itsm/changes/{tid}"

    api.login(U_RO)
    read = await api.client.get(url)
    assert read.status_code == 200 and read.json()["can_write"] is False
    assert read.json()["allowed_targets"] == []
    forbidden = await api.client.patch(url, json={"reason": "x"})
    assert (forbidden.status_code, forbidden.json()["detail"]["code"]) == (403, "forbidden")
    assert (
        await api.client.post(
            "/api/v1/itsm/changes", json={"title": "x", "change_type": "normal", "queue_id": Q_A}
        )
    ).status_code == 403

    api.login(U_NONE)
    for call in (
        api.client.get(url),
        api.client.patch(url, json={"reason": "x"}),
        api.client.get(url + "/events"),
    ):
        hidden = await call
        assert (hidden.status_code, hidden.json()["detail"]["code"]) == (404, "ticket_not_found")

    api.login(U_CREATE)
    assert (await api.create())["can_write"] is False

    api.login(U_MANAGER)
    manager = await api.client.get(url)
    assert manager.json()["can_manage"] is True


async def test_create_validation_and_refusals(api: Api) -> None:
    async def post(**fields: Any) -> Any:
        return await api.client.post(
            "/api/v1/itsm/changes",
            json={"title": "x", "change_type": "normal", "queue_id": Q_A, **fields},
        )

    assert (await post(change_type="standard")).status_code == 422  # no template
    assert (await post(title="")).status_code == 422
    assert (await post(change_type="bogus")).status_code == 422
    assert (
        await post(planned_start="2026-11-01T10:00:00Z", planned_end="2026-11-01T09:00:00Z")
    ).status_code == 422
    missing = await api.client.post(
        "/api/v1/itsm/changes", json={"title": "x", "change_type": "normal"}
    )
    assert (missing.status_code, missing.json()["detail"]["code"]) == (422, "queue_required")
    other = await post(queue_id=Q_B)
    assert other.status_code == 403
    unknown_service = await post(service_id=946399)
    assert (unknown_service.status_code, unknown_service.json()["detail"]["code"]) == (
        422,
        "service_not_found",
    )


async def test_adopt_route(api: Api) -> None:
    w = api.world
    state = (
        await w.session.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
    ).scalar_one()
    type_id = (
        await w.session.execute(text("SELECT id FROM ticket_type WHERE name = 'Change'"))
    ).scalar_one()
    await w.session.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, '202610019463', 'Altes Ticket', :q, 1, :ty, 1, 1, 3, :st,"
            " 0, 0, 0, 0, 0, 0, 0, :now, 1, :now, 1)"
        ),
        {"id": T_PLAIN, "q": Q_A, "ty": type_id, "st": state, "now": datetime(2026, 10, 1)},
    )
    url = f"/api/v1/tickets/{T_PLAIN}/change"
    api.login(U_RO)
    assert (await api.client.post(url, json={"change_type": "normal"})).status_code == 403
    api.login(U_FULL)
    assert (await api.client.post(url, json={"change_type": "bogus"})).status_code == 422
    adopted = await api.client.post(url, json={"change_type": "emergency"})
    assert adopted.status_code == 200, adopted.text
    assert (adopted.json()["phase"], adopted.json()["change_type"]) == ("draft", "emergency")
    again = await api.client.post(url, json={"change_type": "normal"})
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "change_exists")
    assert (
        await api.client.post("/api/v1/tickets/946399/change", json={"change_type": "normal"})
    ).status_code == 404


async def test_status_route_is_not_shadowed_by_the_id_route(api: Api) -> None:
    got = await api.client.get("/api/v1/itsm/changes/status")
    assert got.status_code == 200 and got.json()["available"] is True


@pytest.mark.parametrize("mode", ["off", "znuny_readthrough"])
async def test_routes_refused_outside_mode_b(request: pytest.FixtureRequest, mode: str) -> None:
    async with change_world(request, URL_FIXTURES[0], mode=mode) as world:
        who = {"u": _user(U_FULL)}

        async def _db() -> Any:
            yield world.session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: world.factory
        app.dependency_overrides[get_current_user] = lambda: who["u"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/itsm/changes",
                json={"title": "x", "change_type": "normal", "queue_id": Q_A},
            )
            expected = (404, "change_unavailable") if mode == "off" else (409, "change_mode_a")
            assert (created.status_code, created.json()["detail"]["code"]) == expected
            assert (await client.get("/api/v1/itsm/changes/1")).status_code == expected[0]


def test_every_change_path_is_in_the_tickets_area() -> None:
    for path in (
        "/api/v1/itsm/changes",
        "/api/v1/itsm/changes/7",
        "/api/v1/itsm/changes/7/events",
        "/api/v1/tickets/7/change",
    ):
        assert path_to_area(path) == "tickets"
