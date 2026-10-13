"""``/api/v1/admin/itsm/link-types``, ``/api/v1/itsm/link-types`` and the ticket link guard.

Both dialects. Seed band 940600-940699 (shared with ``test_itsm_link_types.py``): sysconfig rows
940600-940609, user 940620, group 940621, queue 940622, tickets 940630/940631.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from tests._itsm_mode_helpers import itsm_url, make_factory, plain_url, store
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._sysconfig_rows import set_sysconfig_rows
from tests.test_itsm_link_types import (
    ITSM_TYPES,
    PLAIN_CONFIG,
    SYSCONFIG_BASE,
    wipe_link_types,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.mode import SETTING_MODE

pytestmark = pytest.mark.db

ADMIN_BASE = "/api/v1/admin/itsm/link-types"
NOW = datetime(2026, 10, 1, 8, 0, 0)
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT_ID, GROUP, QUEUE, T1, T2 = 940620, 940621, 940622, 940630, 940631
AGENT = AuthenticatedUser(
    id=AGENT_ID, login="lt94.agent", first_name="Lia", last_name="Agent", auth_method="session"
)
DIALECTS = ["mysql", "postgresql"]
_SC_HI = SYSCONFIG_BASE + 9
_MARK_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_history")


def _clients(
    url: str, *, admin: bool, agent: bool = True
) -> Callable[[], AsyncIterator[AsyncClient]]:
    async def make() -> AsyncIterator[AsyncClient]:
        engine, factory = make_factory(url)

        async def _db() -> Any:
            async with factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: AGENT
        if admin:
            app.dependency_overrides[get_admin_user] = lambda: ADMIN
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
        await engine.dispose()

    return make


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def admin(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[AsyncClient]:
    url = itsm_url(request, dialect, "itsm-6")
    wipe_link_types(url)
    request.addfinalizer(lambda: wipe_link_types(url))
    async for client in _clients(url, admin=True)():
        yield client


async def _synced(client: AsyncClient) -> dict[str, dict[str, Any]]:
    assert (await client.post(f"{ADMIN_BASE}/sync")).status_code == 200
    return {i["name"]: i for i in (await client.get(ADMIN_BASE)).json()["items"]}


async def test_get_is_read_only_and_lists_unsynced_types(admin: AsyncClient) -> None:
    body = (await admin.get(ADMIN_BASE)).json()
    assert body["items"] == [] and body["missing_from_table"] == sorted(ITSM_TYPES)
    assert (await admin.get(ADMIN_BASE)).json()["items"] == []  # GET did not write (F31)
    synced = await admin.post(f"{ADMIN_BASE}/sync")
    assert synced.status_code == 200 and synced.json() == {"changed": 7}
    body = (await admin.get(ADMIN_BASE)).json()
    assert body["missing_from_table"] == [] and len(body["items"]) == 7
    depends = next(i for i in body["items"] if i["name"] == "DependsOn")
    assert depends["origin"] == "znuny" and depends["propagation"] == "both"
    assert (depends["znuny_source_name"], depends["znuny_target_name"]) == (
        "Depends on",
        "Required for",
    )
    assert (await admin.post(f"{ADMIN_BASE}/sync")).json() == {"changed": 0}


async def test_admin_cannot_rename_znuny_type(admin: AsyncClient) -> None:
    rows = await _synced(admin)
    normal = rows["Normal"]["id"]
    renamed = await admin.patch(f"{ADMIN_BASE}/{normal}", json={"name": "Standard"})
    assert renamed.status_code == 422
    assert renamed.json()["detail"]["code"] == "link_type_name_immutable"
    for field, value in (("pointed", True), ("propagation", "source")):
        locked = await admin.patch(f"{ADMIN_BASE}/{normal}", json={field: value})
        assert locked.status_code == 422, field
        assert locked.json()["detail"]["code"] == "znuny_link_type_read_only"
    assert (await admin.delete(f"{ADMIN_BASE}/{normal}")).status_code == 422
    ok = await admin.patch(
        f"{ADMIN_BASE}/{normal}",
        json={"name": "Normal", "source_label": "Standard", "valid": False, "offer_ci_ci": True},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["source_label"] == "Standard" and ok.json()["valid"] is False
    assert (await admin.post(f"{ADMIN_BASE}/sync")).json() == {"changed": 0}  # override stays
    assert (await _synced(admin))["Normal"]["source_label"] == "Standard"


async def test_admin_crud_tiqora_type(admin: AsyncClient) -> None:
    created = await admin.post(
        ADMIN_BASE,
        json={
            "name": "BackedUpBy",
            "source_label": "Backed up by",
            "target_label": "Backs up",
            "pointed": True,
            "propagation": "target",
            "offer_ci_ci": True,
            "offer_ticket_ci": True,
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["origin"] == "tiqora" and row["valid"] is True and row["propagation"] == "target"
    assert row["offer_ticket_ci"] and row["offer_ci_ci"] and not row["offer_ticket_ticket"]
    assert row["znuny_source_name"] is None
    one = f"{ADMIN_BASE}/{row['id']}"

    dup = await admin.post(ADMIN_BASE, json={"name": "BackedUpBy"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "link_type_exists"
    znuny_name = await admin.post(ADMIN_BASE, json={"name": "DependsOn"})  # Znuny's, not synced yet
    assert znuny_name.status_code == 409
    ticket_ticket = await admin.post(ADMIN_BASE, json={"name": "TT", "offer_ticket_ticket": True})
    assert ticket_ticket.status_code == 422
    assert ticket_ticket.json()["detail"]["code"] == "offer_ticket_ticket_not_allowed"
    assert (await admin.post(ADMIN_BASE, json={"name": "9bad name"})).status_code == 422
    assert (
        await admin.post(ADMIN_BASE, json={"name": "X", "propagation": "up"})
    ).status_code == 422

    changed = await admin.patch(one, json={"propagation": "both", "pointed": False, "valid": False})
    assert changed.status_code == 200
    assert (changed.json()["propagation"], changed.json()["pointed"]) == ("both", False)
    assert changed.json()["valid"] is False
    rename = await admin.patch(one, json={"name": "Other"})
    assert rename.status_code == 422
    assert rename.json()["detail"]["code"] == "link_type_name_immutable"
    assert (await admin.patch(one, json={"offer_ticket_ticket": True})).status_code == 422

    assert (await admin.delete(one)).status_code == 204
    assert (await admin.delete(one)).status_code == 404
    assert (await admin.patch(one, json={"valid": True})).status_code == 404
    assert (await admin.get(ADMIN_BASE)).json()["items"] == []


async def test_non_admin_forbidden(request: pytest.FixtureRequest, dialect: str) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    wipe_link_types(url)
    request.addfinalizer(lambda: wipe_link_types(url))
    async for client in _clients(url, admin=False)():
        assert (await client.get(ADMIN_BASE)).status_code == 403
        assert (await client.post(f"{ADMIN_BASE}/sync")).status_code == 403
        assert (await client.post(ADMIN_BASE, json={"name": "Nope"})).status_code == 403
        assert (await client.patch(f"{ADMIN_BASE}/1", json={"valid": False})).status_code == 403
        assert (await client.delete(f"{ADMIN_BASE}/1")).status_code == 403


async def test_agent_route_offers_types_per_mode(
    request: pytest.FixtureRequest, dialect: str
) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    wipe_link_types(url)
    request.addfinalizer(lambda: wipe_link_types(url))
    route = "/api/v1/itsm/link-types"
    async for client in _clients(url, admin=False)():
        got = await client.get(route)
        assert [o["name"] for o in got.json()] == ["Normal", "ParentChild"]
        assert got.json()[1]["pointed"] is True and got.json()[1]["source_name"] == "Parent"
        ci = await client.get(route, params={"pair": "ticket_ci"})
        assert [o["name"] for o in ci.json()] == ["AlternativeTo", "DependsOn", "RelevantTo"]
        assert (await client.get(route, params={"pair": "bogus"})).status_code == 422
        store(url, SETTING_MODE, "off")
        off = await client.get(route, params={"pair": "ci_ci"})
        assert off.status_code == 409 and off.json()["detail"] == {"code": "itsm_off"}
        assert (await client.get(route)).status_code == 200  # ticket links work without ITSM


# --- POST /tickets/{id}/links validates the type (reads inside the route's transaction) ---


def _clean_tickets(conn: Any) -> None:
    ids = f"{T1}, {T2}"
    keys = f"'{T1}', '{T2}'"
    for sql in (
        f"DELETE FROM link_relation WHERE source_key IN ({keys}) OR target_key IN ({keys})",
        "DELETE FROM link_object WHERE name = 'Ticket' AND id NOT IN"
        " (SELECT source_object_id FROM link_relation)"
        " AND id NOT IN (SELECT target_object_id FROM link_relation)",
        f"DELETE FROM sysconfig_modified WHERE id BETWEEN {SYSCONFIG_BASE} AND {_SC_HI}",
        f"DELETE FROM sysconfig_default WHERE id BETWEEN {SYSCONFIG_BASE} AND {_SC_HI}",
        f"DELETE FROM ticket_history WHERE ticket_id IN ({ids})",
        f"DELETE FROM ticket WHERE id IN ({ids})",
        f"DELETE FROM queue WHERE id = {QUEUE}",
        f"DELETE FROM group_user WHERE group_id = {GROUP}",
        f"DELETE FROM permission_groups WHERE id = {GROUP}",
        f"DELETE FROM users WHERE id = {AGENT_ID}",
    ):
        conn.execute(text(sql))


def _seed_tickets(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _clean_tickets(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'lt94.agent', 'x', 'Lia', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'lt94-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": NOW},
        )
        for key in ("ro", "rw"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": AGENT_ID, "g": GROUP, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'LinkTypeQueue', :g, 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": NOW},
        )
        for tid in (T1, T2):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'link type', :q, 1, 1, :u, 1, 3, 4, 0, 0, 0, 0, 0, 0,"
                    " 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20261001{tid}", "q": QUEUE, "u": AGENT_ID, "t": NOW},
            )
        set_sysconfig_rows(conn, SYSCONFIG_BASE, PLAIN_CONFIG)
    engine.dispose()


@pytest.fixture
async def tickets(
    request: pytest.FixtureRequest, dialect: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, str]]:
    url = plain_url(request, "mariadb_znuny_url" if dialect == "mysql" else "postgres_znuny_url")
    wipe_link_types(url)
    _seed_tickets(url)
    marks = snapshot_marks(url, _MARK_TABLES)
    engine, factory = make_factory(url)
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    try:
        async for client in _clients(url, admin=False)():
            yield client, url
    finally:
        await engine.dispose()
        delete_above_marks(url, marks)
        cleanup = create_engine(url)
        with cleanup.begin() as conn:
            _clean_tickets(conn)
        cleanup.dispose()
        wipe_link_types(url)


async def _post_link(client: AsyncClient, link_type: str) -> Any:
    return await client.post(
        f"/api/v1/tickets/{T1}/links", json={"target_ticket_id": T2, "link_type": link_type}
    )


async def test_link_create_without_sync_still_201(tickets: tuple[AsyncClient, str]) -> None:
    client, url = tickets
    wipe_link_types(url)  # no row at all: the SysConfig catalogue alone offers "Normal"
    resp = await _post_link(client, "Normal")
    assert resp.status_code == 201, resp.text
    listed = (await client.get(f"/api/v1/tickets/{T1}/links")).json()
    assert [(r["other_ticket_id"], r["link_type"]) for r in listed] == [(T2, "Normal")]


async def test_link_create_rejects_type_not_offered(tickets: tuple[AsyncClient, str]) -> None:
    client, url = tickets
    engine, factory = make_factory(url)
    try:
        from tiqora.itsm.link_types import sync_link_type_rows

        async with factory() as session:
            await sync_link_type_rows(session, user_id=1)
            await session.commit()
    finally:
        await engine.dispose()
    update = create_engine(url)
    try:
        with update.begin() as conn:
            conn.execute(
                text("UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'Normal'"),
                {"v": False},
            )
            conn.execute(
                # a tiqora default: Znuny cannot store it as a ticket link
                text(
                    "UPDATE tiqora_itsm_link_type SET offer_ticket_ticket = :v"
                    " WHERE name = 'DependsOn'"
                ),
                {"v": True},
            )
    finally:
        update.dispose()
    for hidden in ("Normal", "DependsOn", "Banana"):
        resp = await _post_link(client, hidden)
        assert resp.status_code == 422, hidden
        assert resp.json()["detail"] == {"code": "link_type_not_offered", "link_type": hidden}
    assert (await client.get(f"/api/v1/tickets/{T1}/links")).json() == []
    assert (await _post_link(client, "ParentChild")).status_code == 201
