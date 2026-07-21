"""Per-action Znuny ACL enforcement on ticket PATCH + TicketDetail.permissions.

Covers the locked key→action mapping:
  priority → priority, owner/responsible → owner, title/state/lock → rw,
  move → move_into (source), note/reply → note, create → create.
``rw`` implies every key. Personal watch/unwatch stays ungated.

Also regression for the broken patch_ticket priority path that passed a
ticket_id into ``_assert_rw`` (which expects a queue_id).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketService

pytestmark = pytest.mark.db

NOW = datetime(2024, 7, 1, 12, 0, 0)

# Dedicated id block — avoid collisions with zoom (73xx/84xx), read_api (20x), etc.
_UID_PRIO = 9201
_UID_OWNER = 9202
_UID_RW = 9203
_UID_RO = 9204
_GROUP = 9230
_QUEUE = 9200
_TICKET = 9270
_LOGIN_PRIO = "acl.agent.priority"
_LOGIN_OWNER = "acl.agent.owner"
_LOGIN_RW = "acl.agent.rw"
_LOGIN_RO = "acl.agent.ro"
# Second agent used as an assignable owner target (must differ from ticket owner).
_UID_TARGET = 9205
_LOGIN_TARGET = "acl.agent.target"


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Children before parents (mutations write ticket_history / outbox / cache).
        conn.execute(text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": _TICKET})
        conn.execute(text("DELETE FROM ticket_watcher WHERE ticket_id = :t"), {"t": _TICKET})
        conn.execute(text("DELETE FROM tiqora_event_outbox WHERE ticket_id = :t"), {"t": _TICKET})
        conn.execute(
            text("DELETE FROM tiqora_cache_invalidation WHERE ticket_id = :t"),
            {"t": _TICKET},
        )
        conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": _TICKET})
        conn.execute(text("DELETE FROM queue WHERE id = :q"), {"q": _QUEUE})
        conn.execute(
            text(
                "DELETE FROM group_user WHERE user_id IN (:u1, :u2, :u3, :u4, :u5) OR group_id = :g"
            ),
            {
                "u1": _UID_PRIO,
                "u2": _UID_OWNER,
                "u3": _UID_RW,
                "u4": _UID_RO,
                "u5": _UID_TARGET,
                "g": _GROUP,
            },
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :g"), {"g": _GROUP})
        conn.execute(
            text("DELETE FROM users WHERE id IN (:u1, :u2, :u3, :u4, :u5)"),
            {
                "u1": _UID_PRIO,
                "u2": _UID_OWNER,
                "u3": _UID_RW,
                "u4": _UID_RO,
                "u5": _UID_TARGET,
            },
        )

        for uid, login in (
            (_UID_PRIO, _LOGIN_PRIO),
            (_UID_OWNER, _LOGIN_OWNER),
            (_UID_RW, _LOGIN_RW),
            (_UID_RO, _LOGIN_RO),
            (_UID_TARGET, _LOGIN_TARGET),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, 'x', 'Acl', :login, 1, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "t": NOW},
            )

        conn.execute(
            text(
                """
                INSERT INTO permission_groups (id, name, valid_id,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, 'acl-grp', 1, :t, 1, :t, 1)
                """
            ),
            {"id": _GROUP, "t": NOW},
        )

        # priority-only: ro (read ticket) + priority
        for key in ("ro", "priority"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user (user_id, group_id, permission_key,
                        create_time, create_by, change_time, change_by)
                    VALUES (:uid, :gid, :k, :t, 1, :t, 1)
                    """
                ),
                {"uid": _UID_PRIO, "gid": _GROUP, "k": key, "t": NOW},
            )
        # owner-only: ro + owner
        for key in ("ro", "owner"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user (user_id, group_id, permission_key,
                        create_time, create_by, change_time, change_by)
                    VALUES (:uid, :gid, :k, :t, 1, :t, 1)
                    """
                ),
                {"uid": _UID_OWNER, "gid": _GROUP, "k": key, "t": NOW},
            )
        # rw agent (full write)
        for key in ("ro", "rw"):
            conn.execute(
                text(
                    """
                    INSERT INTO group_user (user_id, group_id, permission_key,
                        create_time, create_by, change_time, change_by)
                    VALUES (:uid, :gid, :k, :t, 1, :t, 1)
                    """
                ),
                {"uid": _UID_RW, "gid": _GROUP, "k": key, "t": NOW},
            )
        # ro-only
        conn.execute(
            text(
                """
                INSERT INTO group_user (user_id, group_id, permission_key,
                    create_time, create_by, change_time, change_by)
                VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)
                """
            ),
            {"uid": _UID_RO, "gid": _GROUP, "t": NOW},
        )

        conn.execute(
            text(
                """
                INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,
                    signature_id, follow_up_id, follow_up_lock, valid_id,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, 'AclQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)
                """
            ),
            {"id": _QUEUE, "gid": _GROUP, "t": NOW},
        )

        # priority_id 3 = "3 normal" (Znuny default); state 4 = open
        conn.execute(
            text(
                """
                INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id, timeout, until_time, escalation_time,
                    escalation_update_time, escalation_response_time,
                    escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by)
                VALUES (:id, '20240701920001', 'ACL Test Ticket', :qid, 1, 1,
                    :owner, 1, 3, 4, 'ACLCUST', 'acl.cust@example.com',
                    0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)
                """
            ),
            {"id": _TICKET, "qid": _QUEUE, "owner": _UID_RW, "t": NOW},
        )
    engine.dispose()
    return {
        "ticket": _TICKET,
        "queue": _QUEUE,
        "uid_prio": _UID_PRIO,
        "uid_owner": _UID_OWNER,
        "uid_rw": _UID_RW,
        "uid_ro": _UID_RO,
        "uid_target": _UID_TARGET,
        "login_prio": _LOGIN_PRIO,
        "login_owner": _LOGIN_OWNER,
        "login_rw": _LOGIN_RW,
        "login_ro": _LOGIN_RO,
    }


async def _client_for(sync_url: str, user_id: int, login: str) -> tuple[AsyncClient, Any]:
    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=login,
        first_name="Acl",
        last_name=login,
        auth_method="session",
        email=f"{login}@example.com",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: fake_user
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, engine


async def _ticket_priority(sync_url: str, ticket_id: int) -> int:
    engine = create_async_engine(_to_async_url(sync_url))
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT ticket_priority_id FROM ticket WHERE id = :id"),
                {"id": ticket_id},
            )
        ).first()
    await engine.dispose()
    assert row is not None
    return int(row[0])


async def _ticket_owner(sync_url: str, ticket_id: int) -> int:
    engine = create_async_engine(_to_async_url(sync_url))
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT user_id FROM ticket WHERE id = :id"),
                {"id": ticket_id},
            )
        ).first()
    await engine.dispose()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_priority_only_agent_can_change_priority_not_owner_or_title(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    client, engine = await _client_for(sync_url, ids["uid_prio"], ids["login_prio"])
    try:
        # priority_id 5 = "5 very high" from Znuny initial_insert
        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"priority_id": 5},
        )
        assert r.status_code == 204, r.text
        assert await _ticket_priority(sync_url, ids["ticket"]) == 5

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"owner_id": ids["uid_target"]},
        )
        assert r.status_code == 403, r.text

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"title": "Nope"},
        )
        assert r.status_code == 403, r.text
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_owner_only_agent_can_change_owner_not_priority(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    client, engine = await _client_for(sync_url, ids["uid_owner"], ids["login_owner"])
    try:
        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"owner_id": ids["uid_target"]},
        )
        assert r.status_code == 204, r.text
        assert await _ticket_owner(sync_url, ids["ticket"]) == ids["uid_target"]

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"responsible_id": ids["uid_target"]},
        )
        assert r.status_code == 204, r.text

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"priority_id": 5},
        )
        assert r.status_code == 403, r.text
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_rw_agent_all_mutations_and_permissions_object(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    client, engine = await _client_for(sync_url, ids["uid_rw"], ids["login_rw"])
    try:
        # Regression: priority change must succeed for rw agents (was always 403).
        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"priority_id": 4},
        )
        assert r.status_code == 204, r.text
        assert await _ticket_priority(sync_url, ids["ticket"]) == 4

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"title": "RW retitled"},
        )
        assert r.status_code == 204, r.text

        r = await client.patch(
            f"/api/v1/tickets/{ids['ticket']}",
            json={"owner_id": ids["uid_target"]},
        )
        assert r.status_code == 204, r.text

        detail = await client.get(f"/api/v1/tickets/{ids['ticket']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["can_write"] is True
        perms = body["permissions"]
        for key in ("ro", "move_into", "create", "note", "owner", "priority", "rw"):
            assert perms[key] is True, key
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_ro_agent_writes_denied_read_and_permissions(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    client, engine = await _client_for(sync_url, ids["uid_ro"], ids["login_ro"])
    try:
        for payload in (
            {"priority_id": 5},
            {"owner_id": ids["uid_target"]},
            {"title": "nope"},
            {"lock": "lock"},
            {"state_id": 2},
        ):
            r = await client.patch(f"/api/v1/tickets/{ids['ticket']}", json=payload)
            assert r.status_code == 403, (payload, r.text)

        detail = await client.get(f"/api/v1/tickets/{ids['ticket']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["can_write"] is False
        perms = body["permissions"]
        assert perms["ro"] is True
        assert perms["rw"] is False
        assert perms["priority"] is False
        assert perms["owner"] is False
        assert perms["note"] is False
        assert perms["move_into"] is False
        assert perms["create"] is False
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_priority_only_permissions_object_via_service(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Service-level check: priority-only agent gets priority=True, rw=False."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            ts = TicketService(session)
            detail = await ts.get_ticket(ids["uid_prio"], ids["ticket"])
            assert detail.can_write is False
            assert detail.permissions.priority is True
            assert detail.permissions.ro is True
            assert detail.permissions.rw is False
            assert detail.permissions.owner is False
            assert detail.permissions.note is False
    finally:
        await engine.dispose()
