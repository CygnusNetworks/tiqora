"""DB integration tests for TOTP 2FA enrollment + pending-2FA session isolation.

Mirrors the seeding pattern in test_compat_operations.py: real Znuny
``users`` row + ``TiqoraBase.metadata.create_all`` for ``tiqora_user_totp``,
against the mariadb/postgres testcontainer fixtures.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pyotp
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.domain.totp import TOTPService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    return sync_url


def _seed_user(sync_url: str) -> tuple[int, str]:
    ns = uuid.uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 500_000
    login = f"totp.agent.{ns}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Totp', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return user_id, login


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enroll_confirm_verify_disable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings)

        assert await totp.is_enabled(user_id) is False

        secret, uri = await totp.enroll(user_id, login)
        assert uri.startswith("otpauth://totp/")
        assert login in uri
        # Not enabled until confirmed
        assert await totp.is_enabled(user_id) is False

        wrong_code = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"
        assert await totp.confirm(user_id, wrong_code) is False

        code = pyotp.TOTP(secret).now()
        assert await totp.confirm(user_id, code) is True
        assert await totp.is_enabled(user_id) is True

        assert await totp.verify(user_id, code) is True
        assert await totp.verify(user_id, "654321" if code != "654321" else "123456") is False

        # Disable requires a valid code
        assert await totp.disable(user_id, "000000" if code != "000000" else "111111") is False
        assert await totp.disable(user_id, pyotp.TOTP(secret).now()) is True
        assert await totp.is_enabled(user_id) is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_pending_session_cannot_resolve_as_full_session(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """After password login with TOTP enabled, the pending session must be
    invisible to the normal resolve_session()/get_current_user path — only
    promote_pending_session() (gated by a valid TOTP code) may upgrade it."""
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key", totp_pending_ttl_seconds=300)
    sessions = SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        totp = TOTPService(session, settings)
        secret, _uri = await totp.enroll(user_id, login)
        await totp.confirm(user_id, pyotp.TOTP(secret).now())

        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        assert await totp.is_enabled(user.id) is True

        pending_token = await auth.create_pending_session(user)

        # Pending session must NOT resolve via the normal session path —
        # this is what makes it inaccessible to every other endpoint.
        assert await auth.resolve_session(pending_token) is None

        # The auth.py endpoint gates promotion on a valid TOTP code; a wrong
        # code must never even be checked against promotion.
        assert await totp.verify(user_id, "000000") is False

        # Correct code verifies; the endpoint would then call promote.
        assert await totp.verify(user_id, pyotp.TOTP(secret).now()) is True

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_promote_pending_session_issues_full_session(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key", totp_pending_ttl_seconds=300)
    sessions = SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        pending_token = await auth.create_pending_session(user)

        result = await auth.promote_pending_session(pending_token)
        assert result is not None
        full_token, promoted_user = result
        assert promoted_user.login == login

        # New full token resolves normally; old pending token is consumed.
        resolved = await auth.resolve_session(full_token)
        assert resolved is not None
        assert resolved.login == login
        assert await auth.promote_pending_session(pending_token) is None

    await engine.dispose()
