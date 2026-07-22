"""DB integration tests for ``/api/v1/reference/*`` agent picker endpoints.

Uses a fresh id block (700+) and unique logins so it can run alongside the
other DB tests that share the session-scoped MariaDB fixture without PK or
login collisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {
        "user_id": 700,
        "login": "ref.agent.alpha",
        "queue_id": 750,
        "group_id": 750,
        "signature_id": 750,
        "system_address_id": 750,
    }
    with engine.begin() as conn:
        # Idempotent: the MariaDB fixture is session-scoped and shared, so
        # each test re-seeds the same rows — clear our id/login block first.
        conn.execute(text("DELETE FROM users WHERE id IN (700, 701, 702)"))
        conn.execute(
            text("DELETE FROM customer_user WHERE login LIKE 'ref.cust.%'"),
        )
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": ids["queue_id"]})
        conn.execute(text("DELETE FROM signature WHERE id = :id"), {"id": ids["signature_id"]})
        conn.execute(
            text("DELETE FROM system_address WHERE id = :id"),
            {"id": ids["system_address_id"]},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": ids["group_id"]})
        for uid, login, valid in (
            (700, "ref.agent.alpha", 1),
            (701, "ref.agent.bravo", 1),
            (702, "ref.agent.invalid", 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, 'x', 'Ref', :login, :valid, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "valid": valid, "t": NOW},
            )
        for login, email, cust, valid in (
            ("ref.cust.match", "match@ref.example", "REFCUST", 1),
            ("ref.cust.other", "nomatch@ref.example", "REFCUST", 1),
            ("ref.cust.invalid", "inv@ref.example", "REFCUST", 2),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO customer_user (login, email, customer_id, first_name,
                                               last_name, valid_id,
                                               create_time, create_by, change_time, change_by)
                    VALUES (:login, :email, :cust, 'Cust', :login, :valid, :t, 1, :t, 1)
                    """
                ),
                {"login": login, "email": email, "cust": cust, "valid": valid, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'ref-compose-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": ids["group_id"], "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO signature (id, name, text, content_type, comments, valid_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, 'ref-compose-sig', :txt, 'text/plain; charset=utf-8',"
                " 'test', 1, 1, :t, 1, :t)"
            ),
            {
                "id": ids["signature_id"],
                "txt": "Kind regards,\n<OTRS_FIRST_NAME> <OTRS_LAST_NAME> - Ref Support",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id,"
                " queue_id, create_by, create_time, change_by, change_time)"
                " VALUES (:id, 'compose@ref.example', 'Ref Support', 'test', 1, 1,"
                " 1, :t, 1, :t)"
            ),
            {"id": ids["system_address_id"], "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'RefComposeQueue', :gid, :sa, 1, :sig, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": ids["queue_id"],
                "gid": ids["group_id"],
                "sa": ids["system_address_id"],
                "sig": ids["signature_id"],
                "t": NOW,
            },
        )
    engine.dispose()
    return ids


async def _client_for(mariadb_znuny_url: str, ids: dict[str, Any]) -> Any:
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
        id=ids["user_id"],
        login=ids["login"],
        first_name="Ref",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.asyncio
async def test_reference_priorities(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/reference/priorities")
    await engine.dispose()
    assert resp.status_code == 200
    body = resp.json()
    # Znuny seeds 5 default priorities (3 normal).
    assert any(p["name"] == "3 normal" for p in body)
    assert all("id" in p and "name" in p for p in body)


@pytest.mark.asyncio
async def test_reference_states(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/reference/states")
    await engine.dispose()
    assert resp.status_code == 200
    body = resp.json()
    by_name = {s["name"]: s for s in body}
    assert "open" in by_name
    # 'open' belongs to the 'open' state type; pending states carry a
    # 'pending ...' type so the UI can group wait states.
    assert by_name["open"]["type_name"] == "open"
    assert any(s["type_name"].startswith("pending") for s in body)


@pytest.mark.asyncio
async def test_reference_agents_excludes_invalid(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/reference/agents")
    await engine.dispose()
    assert resp.status_code == 200
    logins = {a["login"] for a in resp.json()}
    assert "ref.agent.alpha" in logins
    assert "ref.agent.bravo" in logins
    assert "ref.agent.invalid" not in logins


@pytest.mark.asyncio
async def test_reference_customers_search(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/reference/customers", params={"q": "cust.match"})
    await engine.dispose()
    assert resp.status_code == 200
    logins = {c["login"] for c in resp.json()}
    assert "ref.cust.match" in logins
    assert "ref.cust.other" not in logins
    assert "ref.cust.invalid" not in logins


@pytest.mark.asyncio
async def test_reference_compose_context(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get(
            "/api/v1/reference/compose-context", params={"queue_id": ids["queue_id"]}
        )
    await engine.dispose()
    assert resp.status_code == 200
    body = resp.json()
    assert body["from_address"] == "Ref Support <compose@ref.example>"
    # Placeholders must be expanded against the *current agent* (ids["user_id"],
    # login "ref.agent.alpha"), same as the reply-draft signature preview —
    # not left raw like "<OTRS_FIRST_NAME> <OTRS_LAST_NAME>".
    assert "Ref ref.agent.alpha - Ref Support" in body["signature"]
    assert "<OTRS_" not in body["signature"]
    assert body["signature_is_html"] is False
    # Frontend::RichText has no seeded sysconfig row for this DB — falls back
    # to the endpoint's own default (true), same as ZNUNY_SETTING_DEFAULTS
    # fallback semantics for unknown keys.
    assert body["rich_text"] is True


@pytest.mark.asyncio
async def test_reference_compose_context_unknown_queue(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/reference/compose-context", params={"queue_id": 999_999})
    await engine.dispose()
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reference_requires_auth() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.config import Settings

    app = create_app(Settings(environment="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/reference/priorities")
    assert resp.status_code == 401
