"""DB integration tests for API-key lifecycle (admin CRUD + resolve).

Covers create (one-time plaintext), revoke, hard expiry, last_used stamp,
invalid target user, and hard delete — against both MariaDB and Postgres
testcontainers with idempotent seed of a dedicated admin + service user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import api_keys as admin_api_keys
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import ApiKeyCreate, ApiKeyUpdate
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.domain.auth import AuthenticatedUser, AuthService, SessionStore, hash_api_key
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")


class _FakeRedis:
    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        pass

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        return None

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        pass

    async def delete(self, *keys: str) -> None:  # noqa: ARG002
        pass


class _FakeSettings:
    session_ttl_seconds = 86400


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_admin_and_target(sync_url: str) -> dict[str, int]:
    """Idempotent seed of a dedicated admin + target service user.

    Uses a uuid-derived id block so reshuffled serial order cannot collide
    with other tests; DELETE before INSERT on the shared session-scoped DB.
    """
    ns = uuid.uuid4().int % 1_000_000
    admin_id = 600_000 + ns
    target_id = 700_000 + ns
    admin_login = f"apikey.admin.{ns}"
    target_login = f"apikey.svc.{ns}"

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        for uid, login in ((admin_id, admin_login), (target_id, target_login)):
            conn.execute(text("DELETE FROM group_user WHERE user_id = :id"), {"id": uid})
            conn.execute(text("DELETE FROM role_user WHERE user_id = :id"), {"id": uid})
            conn.execute(
                text("DELETE FROM users WHERE id = :id OR login = :login"),
                {"id": uid, "login": login},
            )
        # Clear any leftover API keys for these user ids (idempotent re-runs).
        conn.execute(
            text("DELETE FROM tiqora_api_key WHERE user_id IN (:a, :t)"),
            {"a": admin_id, "t": target_id},
        )
        for uid, login, first in (
            (admin_id, admin_login, "ApiAdmin"),
            (target_id, target_login, "ApiSvc"),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, :pw, :first, 'Agent', 1, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "pw": PW_HASH, "first": first, "t": NOW},
            )
    engine.dispose()
    return {"admin_id": admin_id, "target_id": target_id}


def _admin_user(admin_id: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=admin_id,
        login="apikey.admin",
        first_name="ApiAdmin",
        last_name="Agent",
        auth_method="session",
    )


async def _make_session(sync_url: str) -> tuple[AsyncSession, object]:
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


def _auth(session: AsyncSession) -> AuthService:
    return AuthService(
        session,
        SessionStore(_FakeRedis(), _FakeSettings()),  # type: ignore[arg-type]
        _FakeSettings(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_create_returns_plaintext_once_and_resolves(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_target(sync_url)
    session, engine = await _make_session(sync_url)
    admin = _admin_user(ids["admin_id"])

    try:
        created = await admin_api_keys.create_api_key(
            ApiKeyCreate(name="ci-bot", user_id=ids["target_id"]),
            admin,
            session,
        )
        assert created.key.startswith("tiqora_")
        assert created.name == "ci-bot"
        assert created.user_id == ids["target_id"]
        assert created.valid is True
        assert created.created_by == ids["admin_id"]
        assert created.expires_at is None
        assert created.last_used_at is None

        # Raw key resolves to the target user.
        auth = _auth(session)
        resolved = await auth.resolve_api_key(created.key)
        assert resolved is not None
        assert resolved.id == ids["target_id"]
        assert resolved.auth_method == "api_key"

        # last_used_at stamped on successful resolve.
        row = await session.get(TiqoraApiKey, created.id)
        assert row is not None
        assert row.last_used_at is not None

        # GET never exposes plaintext or hash.
        out = await admin_api_keys.get_api_key(created.id, admin, session)
        assert not hasattr(out, "key") or getattr(out, "key", None) is None
        payload = out.model_dump()
        assert "key" not in payload
        assert "key_hash" not in payload
        assert payload["id"] == created.id

        listed = await admin_api_keys.list_api_keys(
            admin, session, ListParams(page=1, page_size=50, valid="all")
        )
        match = next(i for i in listed.items if i.id == created.id)
        assert "key" not in match.model_dump()
        assert "key_hash" not in match.model_dump()
        # Hash must not leak even if someone dumps the ORM row via schema.
        assert hash_api_key(created.key) not in str(match.model_dump())
    finally:
        await session.close()
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_revoke_and_expiry_block_resolve(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_target(sync_url)
    session, engine = await _make_session(sync_url)
    admin = _admin_user(ids["admin_id"])

    try:
        created = await admin_api_keys.create_api_key(
            ApiKeyCreate(name="to-revoke", user_id=ids["target_id"]),
            admin,
            session,
        )
        auth = _auth(session)
        assert await auth.resolve_api_key(created.key) is not None

        await admin_api_keys.update_api_key(created.id, ApiKeyUpdate(valid=False), admin, session)
        assert await auth.resolve_api_key(created.key) is None

        # Fresh key with past expires_at.
        expired = await admin_api_keys.create_api_key(
            ApiKeyCreate(
                name="expired",
                user_id=ids["target_id"],
                expires_at=datetime.utcnow() - timedelta(hours=1),  # noqa: DTZ003
            ),
            admin,
            session,
        )
        assert await auth.resolve_api_key(expired.key) is None
    finally:
        await session.close()
        await engine.dispose()  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_create_rejects_invalid_user_and_delete_removes(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_target(sync_url)
    session, engine = await _make_session(sync_url)
    admin = _admin_user(ids["admin_id"])

    try:
        with pytest.raises(HTTPException) as exc_info:
            await admin_api_keys.create_api_key(
                ApiKeyCreate(name="nope", user_id=9_999_999),
                admin,
                session,
            )
        assert exc_info.value.status_code == 422
        assert "target user" in str(exc_info.value.detail).lower()

        created = await admin_api_keys.create_api_key(
            ApiKeyCreate(name="to-delete", user_id=ids["target_id"]),
            admin,
            session,
        )
        await admin_api_keys.delete_api_key(created.id, admin, session)

        with pytest.raises(HTTPException) as get_exc:
            await admin_api_keys.get_api_key(created.id, admin, session)
        assert get_exc.value.status_code == 404

        auth = _auth(session)
        assert await auth.resolve_api_key(created.key) is None
    finally:
        await session.close()
        await engine.dispose()  # type: ignore[union-attr]
