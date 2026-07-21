"""DB integration tests for agent ticket-zoom APIs.

Covers:
  * GET  /api/v1/tickets/search          — ro-scoped tn/title search
  * POST /api/v1/customers               — agent create customer_user
  * GET  /api/v1/reference/queues?movable=true — rw + valid queues only

Uses a dedicated id/login block (8400+) so it coexists with the other
session-scoped MariaDB fixture consumers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Unique seed block — avoid collisions with test_ticket_zoom_db (73xx),
# test_reference_api (7xx), test_read_api_db, etc.
_UID_RW = 8401
_UID_RO = 8402
_UID_NONE = 8403
_GROUP_RW = 8430
_GROUP_OTHER = 8431
_QUEUE_RW = 8400
_QUEUE_OTHER = 8401
_QUEUE_INVALID = 8402
_TICKET_VISIBLE = 8700
_TICKET_OTHER = 8701
_TICKET_MERGED = 8702
_LOGIN_RW = "zoom.api.agent.rw"
_LOGIN_RO = "zoom.api.agent.ro"
_LOGIN_NONE = "zoom.api.agent.none"


def _mysql_async(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # Agent create-customer invalidates Znuny cache types into
        # tiqora_cache_invalidation — ensure the owned tables exist.
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our block (shared session-scoped DB).
        conn.execute(
            text("DELETE FROM ticket WHERE id IN (:t1, :t2, :t3)"),
            {"t1": _TICKET_VISIBLE, "t2": _TICKET_OTHER, "t3": _TICKET_MERGED},
        )
        conn.execute(
            text("DELETE FROM queue WHERE id IN (:q1, :q2, :q3)"),
            {"q1": _QUEUE_RW, "q2": _QUEUE_OTHER, "q3": _QUEUE_INVALID},
        )
        conn.execute(
            text(
                "DELETE FROM group_user WHERE user_id IN (:u1, :u2, :u3) OR group_id IN (:g1, :g2)"
            ),
            {
                "u1": _UID_RW,
                "u2": _UID_RO,
                "u3": _UID_NONE,
                "g1": _GROUP_RW,
                "g2": _GROUP_OTHER,
            },
        )
        conn.execute(
            text("DELETE FROM permission_groups WHERE id IN (:g1, :g2)"),
            {"g1": _GROUP_RW, "g2": _GROUP_OTHER},
        )
        # customer_user.create_by/change_by → users; drop our rows first
        conn.execute(
            text(
                "DELETE FROM customer_user WHERE login LIKE 'zoom.api.cust.%' "
                "OR create_by IN (:u1, :u2, :u3) OR change_by IN (:u1, :u2, :u3)"
            ),
            {"u1": _UID_RW, "u2": _UID_RO, "u3": _UID_NONE},
        )
        conn.execute(
            text("DELETE FROM users WHERE id IN (:u1, :u2, :u3)"),
            {"u1": _UID_RW, "u2": _UID_RO, "u3": _UID_NONE},
        )

        for uid, login in (
            (_UID_RW, _LOGIN_RW),
            (_UID_RO, _LOGIN_RO),
            (_UID_NONE, _LOGIN_NONE),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, 'x', 'Zoom', :login, 1, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "t": NOW},
            )

        for gid, name in ((_GROUP_RW, "zoom-api-rw"), (_GROUP_OTHER, "zoom-api-other")):
            conn.execute(
                text(
                    """
                    INSERT INTO permission_groups (id, name, valid_id,
                        create_time, create_by, change_time, change_by)
                    VALUES (:id, :name, 1, :t, 1, :t, 1)
                    """
                ),
                {"id": gid, "name": name, "t": NOW},
            )

        # agent.rw: full rw (+ ro) on GROUP_RW
        for key in ("ro", "rw", "create", "move_into"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user (user_id, group_id, permission_key,
                        create_time, create_by, change_time, change_by)
                    VALUES (:uid, :gid, :k, :t, 1, :t, 1)
                    """
                ),
                {"uid": _UID_RW, "gid": _GROUP_RW, "k": key, "t": NOW},
            )
        # agent.ro: only ro on GROUP_RW (no rw → excluded from movable queues)
        conn.execute(
            text(
                """
                INSERT INTO group_user (user_id, group_id, permission_key,
                    create_time, create_by, change_time, change_by)
                VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)
                """
            ),
            {"uid": _UID_RO, "gid": _GROUP_RW, "t": NOW},
        )
        # agent.none: no group_user rows

        # Valid queue in RW group, valid queue in OTHER group, invalid queue in RW group
        for qid, name, gid, valid in (
            (_QUEUE_RW, "ZoomApiQueue", _GROUP_RW, 1),
            (_QUEUE_OTHER, "ZoomApiOtherQ", _GROUP_OTHER, 1),
            (_QUEUE_INVALID, "ZoomApiInvalidQ", _GROUP_RW, 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,
                        signature_id, follow_up_id, follow_up_lock, valid_id,
                        create_time, create_by, change_time, change_by)
                    VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, :valid, :t, 1, :t, 1)
                    """
                ),
                {"id": qid, "name": name, "gid": gid, "valid": valid, "t": NOW},
            )

        # Tickets: visible (open), other-queue (open), merged-on-visible-queue
        # state_id 4 = open, 9 = merged (from Znuny initial insert)
        for tid, tn, title, qid, state_id in (
            (
                _TICKET_VISIBLE,
                "20240601840001",
                "Visible Zoom Search Target",
                _QUEUE_RW,
                4,
            ),
            (
                _TICKET_OTHER,
                "20240601840002",
                "Hidden Other Queue Ticket",
                _QUEUE_OTHER,
                4,
            ),
            (
                _TICKET_MERGED,
                "20240601840003",
                "Merged Zoom Search Target",
                _QUEUE_RW,
                9,
            ),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,
                        user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                        customer_id, customer_user_id, timeout, until_time, escalation_time,
                        escalation_update_time, escalation_response_time,
                        escalation_solution_time, archive_flag,
                        create_time, create_by, change_time, change_by)
                    VALUES (:id, :tn, :title, :qid, 1, 1,
                        :uid, 1, 3, :sid, 'ZOOMAPI', 'zoom.api@example.com',
                        0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)
                    """
                ),
                {
                    "id": tid,
                    "tn": tn,
                    "title": title,
                    "qid": qid,
                    "sid": state_id,
                    "uid": _UID_RW,
                    "t": NOW,
                },
            )
    engine.dispose()
    return {
        "user_rw": _UID_RW,
        "user_ro": _UID_RO,
        "user_none": _UID_NONE,
        "login_rw": _LOGIN_RW,
        "login_ro": _LOGIN_RO,
        "login_none": _LOGIN_NONE,
        "queue_rw": _QUEUE_RW,
        "queue_other": _QUEUE_OTHER,
        "queue_invalid": _QUEUE_INVALID,
        "ticket_visible": _TICKET_VISIBLE,
        "ticket_other": _TICKET_OTHER,
        "ticket_merged": _TICKET_MERGED,
    }


async def _client_for(mariadb_znuny_url: str, user_id: int, login: str) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=login,
        first_name="Zoom",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


# ── Ticket search ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ticket_search_matches_tn_and_title_scoped(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids["user_rw"], ids["login_rw"])
    async with client:
        by_title = await client.get("/api/v1/tickets/search", params={"q": "Visible Zoom Search"})
        by_tn = await client.get("/api/v1/tickets/search", params={"q": "20240601840001"})
        by_partial = await client.get("/api/v1/tickets/search", params={"q": "zoom search target"})
    await engine.dispose()

    assert by_title.status_code == 200
    title_ids = {h["ticket_id"] for h in by_title.json()}
    assert ids["ticket_visible"] in title_ids
    assert ids["ticket_other"] not in title_ids
    assert ids["ticket_merged"] not in title_ids

    hit = next(h for h in by_title.json() if h["ticket_id"] == ids["ticket_visible"])
    assert hit["tn"] == "20240601840001"
    assert hit["title"] == "Visible Zoom Search Target"
    assert hit["queue"] == "ZoomApiQueue"
    assert hit["state"] is not None
    assert hit["state_type"] is not None

    assert by_tn.status_code == 200
    assert {h["ticket_id"] for h in by_tn.json()} == {ids["ticket_visible"]}

    # Case-insensitive title match
    assert by_partial.status_code == 200
    partial_ids = {h["ticket_id"] for h in by_partial.json()}
    assert ids["ticket_visible"] in partial_ids
    # Merged ticket title also matches the substring but must be excluded
    assert ids["ticket_merged"] not in partial_ids


@pytest.mark.asyncio
async def test_ticket_search_excludes_inaccessible(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids["user_none"], ids["login_none"])
    async with client:
        resp = await client.get("/api/v1/tickets/search", params={"q": "Zoom Search"})
    await engine.dispose()
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_ticket_search_empty_q_returns_empty(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids["user_rw"], ids["login_rw"])
    async with client:
        resp = await client.get("/api/v1/tickets/search", params={"q": "  "})
    await engine.dispose()
    assert resp.status_code == 200
    assert resp.json() == []


# ── Agent create customer ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_create_customer_user(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids["user_rw"], ids["login_rw"])
    login = "zoom.api.cust.created"
    body = {
        "login": login,
        "email": "created@zoom.api.example",
        "first_name": "Created",
        "last_name": "Customer",
        "customer_id": "ZOOMAPI",
        "phone": "+49 30 123",
    }
    async with client:
        create_resp = await client.post("/api/v1/customers", json=body)
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        assert created == {
            "login": login,
            "email": body["email"],
            "customer_id": "ZOOMAPI",
            "first_name": "Created",
            "last_name": "Customer",
        }
        # Findable via the reference customer search used by the Kunde dialog
        search_resp = await client.get(
            "/api/v1/reference/customers", params={"q": "zoom.api.cust.created"}
        )
        # Duplicate login → 409
        dup_resp = await client.post("/api/v1/customers", json=body)
    await engine.dispose()

    assert search_resp.status_code == 200
    logins = {c["login"] for c in search_resp.json()}
    assert login in logins
    assert dup_resp.status_code == 409


# ── Movable queues ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_movable_queues_rw_and_valid_only(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)

    # agent.rw: sees ZoomApiQueue (rw + valid), not invalid, not other-group
    client, engine = await _client_for(mariadb_znuny_url, ids["user_rw"], ids["login_rw"])
    async with client:
        resp = await client.get("/api/v1/reference/queues", params={"movable": "true"})
    await engine.dispose()
    assert resp.status_code == 200
    by_id = {q["id"]: q["name"] for q in resp.json()}
    assert ids["queue_rw"] in by_id
    assert by_id[ids["queue_rw"]] == "ZoomApiQueue"
    assert ids["queue_invalid"] not in by_id
    assert ids["queue_other"] not in by_id

    # agent.ro: has ro but not rw → empty movable list
    client, engine = await _client_for(mariadb_znuny_url, ids["user_ro"], ids["login_ro"])
    async with client:
        resp_ro = await client.get("/api/v1/reference/queues", params={"movable": "true"})
        # Without movable, ro is enough for ZoomApiQueue
        resp_ro_list = await client.get("/api/v1/reference/queues")
    await engine.dispose()
    assert resp_ro.status_code == 200
    assert resp_ro.json() == []
    assert resp_ro_list.status_code == 200
    ro_ids = {q["id"] for q in resp_ro_list.json()}
    assert ids["queue_rw"] in ro_ids
    assert ids["queue_invalid"] not in ro_ids
