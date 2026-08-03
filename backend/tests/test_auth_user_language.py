"""UserLanguage preference on /auth/me and PUT /auth/me/language."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
USER_ID = 920_042
LOGIN = "lang.agent"


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


def _cleanup(sync_url: str) -> None:
    """Remove rows this module commits so the DB leak detector stays green."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM user_preferences WHERE user_id = :id"),
            {"id": USER_ID},
        )
        conn.execute(
            text("DELETE FROM users WHERE id = :id OR login = :login"),
            {"id": USER_ID, "login": LOGIN},
        )
    engine.dispose()


def _seed(sync_url: str, *, language: str | None = None) -> None:
    pw = hash_password("secret")
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM user_preferences WHERE user_id = :id"),
            {"id": USER_ID},
        )
        conn.execute(
            text("DELETE FROM users WHERE id = :id OR login = :login"),
            {"id": USER_ID, "login": LOGIN},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, :pw, 'Lang', 'Agent', 1, :t, 1, :t, 1)
                """
            ),
            {"id": USER_ID, "login": LOGIN, "pw": pw, "t": NOW},
        )
        if language is not None:
            # Store as text (not bytes) so MySQL LONGBLOB and PG TEXT both keep
            # a readable language code — matches Znuny and set_user_language.
            conn.execute(
                text(
                    """
                    INSERT INTO user_preferences (user_id, preferences_key, preferences_value)
                    VALUES (:id, 'UserLanguage', :v)
                    """
                ),
                {"id": USER_ID, "v": language},
            )
    engine.dispose()


async def _client(sync_url: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db, get_redis
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=USER_ID,
        login=LOGIN,
        first_name="Lang",
        last_name="Agent",
        auth_method="session",
    )
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.delete = AsyncMock(return_value=0)

    settings = Settings(environment="test")
    app = create_app(settings)
    app.state.session_factory = factory
    app.state.redis = fake_redis
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: fake_redis

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_me_returns_user_language(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url, language="de")
    client, engine = await _client(sync_url)
    try:
        async with client:
            resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["language"] == "de"
    finally:
        await engine.dispose()
        _cleanup(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_me_language_null_when_unset(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url, language=None)
    client, engine = await _client(sync_url)
    try:
        async with client:
            resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json().get("language") in (None, "")
    finally:
        await engine.dispose()
        _cleanup(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_put_me_language_persists(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url, language=None)
    client, engine = await _client(sync_url)
    try:
        async with client:
            bad = await client.put("/api/v1/auth/me/language", json={"language": "xx_YY"})
            assert bad.status_code == 422

            ok = await client.put("/api/v1/auth/me/language", json={"language": "pt-BR"})
            assert ok.status_code == 200, ok.text
            assert ok.json()["language"] == "pt_BR"

            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["language"] == "pt_BR"
    finally:
        await engine.dispose()
        _cleanup(sync_url)


def test_decode_preference_value_handles_pg_hex() -> None:
    """Regression: PG TEXT + LargeBinary can surface language as \\x hex."""
    from tiqora.domain.auth import decode_preference_value

    assert decode_preference_value(b"de") == "de"
    assert decode_preference_value("de") == "de"
    assert decode_preference_value("\\x6465") == "de"
    assert decode_preference_value("\\x70745f4252") == "pt_BR"
    assert decode_preference_value(None) is None
    assert decode_preference_value("") is None
    assert decode_preference_value(memoryview(b"en")) == "en"
