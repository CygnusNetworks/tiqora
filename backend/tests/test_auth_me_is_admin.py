"""``GET /api/v1/auth/me`` exposes ``is_admin`` via ``PermissionEngine.is_admin``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if sync_url.startswith("mysql://"):
        return sync_url.replace("mysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_plain_agent(sync_url: str) -> dict[str, Any]:
    """Seed a non-admin agent. Root (id=1) already has admin-group rw from Znuny seed."""
    plain_id = 920_001
    login = "me.plain.agent"
    pw = hash_password("secret")
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM group_user WHERE user_id = :id"), {"id": plain_id})
        conn.execute(
            text("DELETE FROM users WHERE id = :id OR login = :login"),
            {"id": plain_id, "login": login},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, :pw, 'Plain', 'Agent', 1, :t, 1, :t, 1)
                """
            ),
            {"id": plain_id, "login": login, "pw": pw, "t": NOW},
        )
    engine.dispose()
    return {"admin_id": 1, "plain_id": plain_id, "plain_login": login}


async def _client_for(sync_url: str, user_id: int, login: str) -> Any:
    from httpx import ASGITransport, AsyncClient

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
        first_name="Test",
        last_name="User",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_me_is_admin_true_for_admin_group_rw(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_plain_agent(sync_url)
    client, engine = await _client_for(sync_url, ids["admin_id"], "root@localhost")
    try:
        async with client:
            resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ids["admin_id"]
        assert body["is_admin"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_me_is_admin_false_for_plain_agent(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_plain_agent(sync_url)
    client, engine = await _client_for(sync_url, ids["plain_id"], ids["plain_login"])
    try:
        async with client:
            resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ids["plain_id"]
        assert body["is_admin"] is False
    finally:
        await engine.dispose()
