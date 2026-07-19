"""DB integration tests for customer portal authentication (Phase 3a subtask 1).

Covers: login success, login failure (wrong password / invalid customer),
session resolve, and logout — mirrors the agent-auth test style used for
``tiqora.domain.auth.SessionStore`` in test_compat_operations.py, but against
``CustomerAuthService`` / ``CustomerSessionStore`` and the real ``customer_user``
table (seeded in a Docker testcontainer).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.customer_auth import CustomerAuthService, CustomerSessionStore
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")
# mariadb_znuny_url / postgres_znuny_url are session-scoped testcontainer
# fixtures shared by every test function *and test module* in the whole
# pytest run (e.g. also test_portal_tickets.py), so seed data is namespaced
# with a random UUID fragment per call to avoid primary-key/unique collisions.


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return _mysql_async(sync_url)
    return sync_url


class _FakeRedis:
    """Minimal fake Redis for CustomerSessionStore testing (no real Redis needed)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeSettings:
    session_ttl_seconds = 86400
    customer_session_cookie_name = "tiqora_customer_session"


def _make_session_store() -> CustomerSessionStore:
    return CustomerSessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]


def _seed_customers(sync_url: str) -> dict[str, str]:
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000
    cid = f"PORTAL1-{ns}"
    login_ok = f"alice.portal.{ns}@example.com"
    login_invalid = f"bob.invalid.{ns}@example.com"

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO customer_company (
                    customer_id, name, valid_id, create_time, create_by, change_time, change_by
                ) VALUES (:cid, :cname, 1, :t, 1, :t, 1)
                """
            ),
            {"cid": cid, "cname": f"Portal Corp {ns}", "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO customer_user (
                    id, login, email, customer_id, pw, first_name, last_name, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    :id, :login, :login, :cid, :pw,
                    'Alice', 'Portal', 1, :t, 1, :t, 1
                )
                """
            ),
            {"id": base + 1, "login": login_ok, "cid": cid, "pw": PW_HASH, "t": NOW},
        )
        # Invalid customer (valid_id=2) must never authenticate
        conn.execute(
            text(
                """
                INSERT INTO customer_user (
                    id, login, email, customer_id, pw, first_name, last_name, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    :id, :login, :login, :cid, :pw,
                    'Bob', 'Invalid', 2, :t, 1, :t, 1
                )
                """
            ),
            {"id": base + 2, "login": login_invalid, "cid": cid, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return {"login_ok": login_ok, "login_invalid": login_invalid, "customer_id": cid}


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_login_success_and_session_lifecycle(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    seeded = _seed_customers(sync_url)
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sessions = _make_session_store()
    async with factory() as session:
        auth = CustomerAuthService(session, sessions, _FakeSettings())  # type: ignore[arg-type]

        customer = await auth.authenticate_password(seeded["login_ok"], "secret123")
        assert customer is not None
        assert customer.login == seeded["login_ok"]
        assert customer.customer_id == seeded["customer_id"]
        assert customer.email == seeded["login_ok"]

        token = await auth.create_session(customer)
        resolved = await auth.resolve_session(token)
        assert resolved is not None
        assert resolved.login == seeded["login_ok"]

        await auth.logout(token)
        assert await auth.resolve_session(token) is None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_login_failure_wrong_password_and_invalid_customer(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    seeded = _seed_customers(sync_url)
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    sessions = _make_session_store()
    async with factory() as session:
        auth = CustomerAuthService(session, sessions, _FakeSettings())  # type: ignore[arg-type]

        # Wrong password
        assert await auth.authenticate_password(seeded["login_ok"], "wrong-password") is None

        # Unknown login
        assert await auth.authenticate_password("nobody.portal@example.com", "secret123") is None

        # valid_id != 1 must never authenticate, even with correct password
        assert await auth.authenticate_password(seeded["login_invalid"], "secret123") is None

    await engine.dispose()
