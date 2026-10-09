"""Disabling an account or an admin password reset ends the person's sessions.

Agents (disable via PATCH ``valid_id`` and via DELETE) and customer users (admin password
reset, disable via PATCH and via DELETE): the Znuny ``sessions`` rows are deleted first, then
the Redis sessions (agent tokens; portal sessions and GenericInterface customer tokens).

DB tests on MariaDB, direct router calls. Seed ids: agents 8930-8931, customer logins
``gi8930.*@example.org``, ``sessions`` ids ``GI8930*`` (GI sub-band 8930-8939; 8920-8929 is
``test_compat_webservice_api``). Every row is deleted afterwards.
"""

from __future__ import annotations

import fnmatch
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import customers as admin_customers
from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.schemas import (
    CustomerUserAdminUpdate,
    CustomerUserBulkUpdate,
    UserUpdate,
)
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser, SessionStore
from tiqora.domain.customer_auth import CustomerSessionStore

pytestmark = pytest.mark.db

NOW = datetime(2024, 1, 1, 12, 0, 0)
AGENT, OTHER_AGENT = 8930, 8931
CUST, OTHER_CUST = "gi8930.kim@example.org", "gi8930.lee@example.org"


class _FakeRedis:
    """Strings, sets and SCAN - enough for both session stores."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        if key in self.sets:
            raise TypeError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self.store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)
            self.sets.pop(key, None)

    async def sadd(self, key: str, *members: str) -> int:
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def scan_iter(self, match: str = "*", count: int | None = None) -> AsyncIterator[str]:
        for key in list(self.store) + list(self.sets):
            if fnmatch.fnmatchcase(key, match):
                yield key


def _settings() -> Settings:
    return Settings(secret_key="unit-test-secret-key")


def _request(redis: _FakeRedis) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=redis, settings=_settings())),
        state=SimpleNamespace(),
        cookies={},
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


_CLEANUP = (
    "DELETE FROM sessions WHERE session_id LIKE 'GI8930%'",
    "DELETE FROM tiqora_cache_invalidation",
    f"DELETE FROM customer_user WHERE login IN ('{CUST}', '{OTHER_CUST}')",
    f"DELETE FROM users WHERE id IN ({AGENT}, {OTHER_AGENT})",
)


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> Iterator[str]:
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        for uid in (AGENT, OTHER_AGENT):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": f"gi{uid}.agent", "t": NOW},
            )
        for login in (CUST, OTHER_CUST):
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, pw, first_name,"
                    " last_name, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:l, :l, 'GI8930', 'x', 'Kim', 'Kunde', 1, :t, 1, :t, 1)"
                ),
                {"l": login, "t": NOW},
            )
        # Znuny session rows: two of the target, one bystander each
        for sid, uid, utype in (
            ("GI8930A1", str(AGENT), "User"),
            ("GI8930A2", str(OTHER_AGENT), "User"),
            ("GI8930C1", CUST, "Customer"),
            ("GI8930C2", OTHER_CUST, "Customer"),
        ):
            for key, val in (("UserID", uid), ("UserType", utype)):
                conn.execute(
                    text(
                        "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                        " VALUES (:s, :k, :v, 0)"
                    ),
                    {"s": sid, "k": key, "v": val},
                )
    engine.dispose()
    yield mariadb_znuny_url
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
    engine.dispose()


def _session_ids(sync_url: str) -> list[str]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT session_id FROM sessions WHERE session_id LIKE 'GI8930%'"
                    " ORDER BY session_id"
                )
            ).all()
        return [str(r[0]) for r in rows]
    finally:
        engine.dispose()


async def _factory(sync_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(sync_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _customer_id(session: AsyncSession, login: str) -> int:
    return int(
        (
            await session.execute(
                text("SELECT id FROM customer_user WHERE login = :l"), {"l": login}
            )
        ).scalar_one()
    )


@pytest.mark.parametrize("how", ["patch", "delete"])
async def test_disabling_an_agent_ends_its_sessions(sync_url: str, how: str) -> None:
    redis = _FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    victim = await store.create(AGENT, f"gi{AGENT}.agent")
    bystander = await store.create(OTHER_AGENT, f"gi{OTHER_AGENT}.agent")
    engine, factory = await _factory(sync_url)
    try:
        async with factory() as session:
            if how == "patch":
                await admin_users.update_user(
                    AGENT,
                    UserUpdate(valid_id=2),
                    _request(redis),
                    _admin(),
                    session,
                    _settings(),
                )
            else:
                await admin_users.deactivate_user(
                    AGENT, _request(redis), _admin(), session, _settings()
                )
    finally:
        await engine.dispose()
    assert _session_ids(sync_url) == ["GI8930A2", "GI8930C1", "GI8930C2"]
    assert await store.get(victim) is None
    assert await store.get(bystander) is not None


async def test_editing_a_valid_agent_keeps_its_sessions(sync_url: str) -> None:
    redis = _FakeRedis()
    store = SessionStore(redis, _settings())  # type: ignore[arg-type]
    token = await store.create(AGENT, f"gi{AGENT}.agent")
    engine, factory = await _factory(sync_url)
    try:
        async with factory() as session:
            await admin_users.update_user(
                AGENT, UserUpdate(valid_id=1), _request(redis), _admin(), session, _settings()
            )
    finally:
        await engine.dispose()
    assert "GI8930A1" in _session_ids(sync_url)
    assert await store.get(token) is not None


@pytest.mark.parametrize("how", ["password", "patch_invalid", "delete", "bulk_invalid"])
async def test_admin_customer_reset_or_disable_ends_its_sessions(sync_url: str, how: str) -> None:
    redis = _FakeRedis()
    settings = _settings()
    gi_store = SessionStore(redis, settings)  # type: ignore[arg-type]
    portal = CustomerSessionStore(redis, settings)  # type: ignore[arg-type]
    gi_token = await gi_store.create(0, CUST)  # GI customer token: sentinel user id 0
    other_gi = await gi_store.create(0, OTHER_CUST)
    agent_token = await gi_store.create(AGENT, f"gi{AGENT}.agent")
    engine, factory = await _factory(sync_url)
    try:
        async with factory() as session:
            portal_token = await portal.create(await _customer_id(session, CUST), CUST)
            other_portal = await portal.create(await _customer_id(session, OTHER_CUST), OTHER_CUST)
            cid = await _customer_id(session, CUST)
            if how == "delete":
                await admin_customers.deactivate_customer_user(
                    cid, _request(redis), _admin(), session, settings
                )
            elif how == "bulk_invalid":
                await admin_customers.bulk_update_customer_users(
                    CustomerUserBulkUpdate(ids=[cid], valid_id=2),
                    _request(redis),
                    _admin(),
                    session,
                    settings,
                )
            else:
                body = (
                    CustomerUserAdminUpdate(password="a-brand-new-pw-5678")
                    if how == "password"
                    else CustomerUserAdminUpdate(valid_id=2)
                )
                await admin_customers.update_customer_user(
                    cid, body, _request(redis), _admin(), session, settings
                )
    finally:
        await engine.dispose()
    # Znuny rows of that customer only
    assert _session_ids(sync_url) == ["GI8930A1", "GI8930A2", "GI8930C2"]
    # Redis: that customer's GI token and portal session, nobody else's
    assert await gi_store.get(gi_token) is None
    assert await portal.get(portal_token) is None
    assert await gi_store.get(other_gi) is not None
    assert await portal.get(other_portal) is not None
    assert await gi_store.get(agent_token) is not None


async def test_customer_edit_without_password_keeps_sessions(sync_url: str) -> None:
    redis = _FakeRedis()
    engine, factory = await _factory(sync_url)
    try:
        async with factory() as session:
            await admin_customers.update_customer_user(
                await _customer_id(session, CUST),
                CustomerUserAdminUpdate(first_name="Kira"),
                _request(redis),
                _admin(),
                session,
                _settings(),
            )
    finally:
        await engine.dispose()
    assert "GI8930C1" in _session_ids(sync_url)
