"""Agent ↔ phone extension (``TiqoraPhoneExtension`` preference) for the CTI
popup: lookup by extension, self-service ``/auth/me/phone`` and the admin
user field."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.schemas import UserUpdate
from tiqora.channels.phone.cti import PHONE_EXTENSION_PREF, users_for_extension
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
ANNA, BERT, GONE = 920_051, 920_052, 920_053


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # Admin writes queue a Znuny cache invalidation row (tiqora table).
        TiqoraBase.metadata.create_all(conn)
        ids = {"a": ANNA, "b": BERT, "c": GONE}
        conn.execute(text("DELETE FROM user_preferences WHERE user_id IN (:a, :b, :c)"), ids)
        conn.execute(text("DELETE FROM tiqora_cache_invalidation"))
        conn.execute(text("DELETE FROM users WHERE id IN (:a, :b, :c)"), ids)
    engine.dispose()


def _seed(sync_url: str) -> None:
    _cleanup(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, login, valid, ext in (
            (ANNA, "cti.anna", 1, "100, PJSIP/anna"),
            (BERT, "cti.bert", 1, "101,100"),
            (GONE, "cti.gone", 2, "100"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Cti', 'Agent', :v, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": login, "v": valid, "t": NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (:id, :k, :v)"
                ),
                {"id": uid, "k": PHONE_EXTENSION_PREF, "v": ext},
            )
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Any:
    _seed(mariadb_znuny_url)
    yield mariadb_znuny_url
    _cleanup(mariadb_znuny_url)


async def test_users_for_extension_matches_lists_and_skips_invalid(seeded: str) -> None:
    engine = create_async_engine(_mysql_async(seeded))
    try:
        async with async_sessionmaker(engine)() as session:
            assert await users_for_extension(session, "100") == [ANNA, BERT]
            assert await users_for_extension(session, "pjsip/anna") == [ANNA]
            assert await users_for_extension(session, "101") == [BERT]
            assert await users_for_extension(session, "10") == []
            assert await users_for_extension(session, " ") == []
    finally:
        await engine.dispose()


async def _client(sync_url: str, user_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db, get_redis
    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    app = create_app(Settings(environment="test"))
    app.state.session_factory = factory
    app.state.redis = fake_redis
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id, login="cti.anna", first_name="Cti", last_name="Agent", auth_method="session"
    )
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: fake_redis
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


async def test_agents_cannot_set_their_own_extension(seeded: str) -> None:
    """The extension is admin-managed (user admin); there is no self-service API."""
    client, engine = await _client(seeded, ANNA)
    try:
        async with client:
            assert (await client.get("/api/v1/auth/me/phone")).status_code in (404, 405)
            put = await client.put("/api/v1/auth/me/phone", json={"extension": "200"})
            assert put.status_code in (404, 405)
    finally:
        await engine.dispose()


async def test_admin_user_phone_extension(seeded: str) -> None:
    from types import SimpleNamespace

    from tiqora.config import Settings

    root = AuthenticatedUser(
        id=1, login="root@localhost", first_name="A", last_name="Z", auth_method="session"
    )
    with pytest.raises(ValidationError):
        UserUpdate(phone_extension="no way!")

    engine = create_async_engine(_mysql_async(seeded))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            fetched = await admin_users.get_user(BERT, root, session)
            assert fetched.phone_extension == "101,100"

            request: Any = SimpleNamespace()
            updated = await admin_users.update_user(
                BERT, UserUpdate(phone_extension="300"), request, root, session, Settings()
            )
            assert updated.phone_extension == "300"
            assert await users_for_extension(session, "300") == [BERT]

            untouched = await admin_users.update_user(
                BERT, UserUpdate(first_name="Bertram"), request, root, session, Settings()
            )
            assert untouched.phone_extension == "300"

            cleared = await admin_users.update_user(
                BERT, UserUpdate(phone_extension=""), request, root, session, Settings()
            )
            assert cleared.phone_extension is None
    finally:
        await engine.dispose()
