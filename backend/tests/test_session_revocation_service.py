"""Validity write services and the one session-ending path (wave 4, SCIM task 3).

DB, both dialects. Seed band 63040-63079: agents 63040 (actor) .. 63044, customer logins
``sr63040.*@example.test``, ``sessions`` ids ``SR6304*``. Invalidation rows are counted
before/after (shared table).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis
from tests.test_identity_store import _to_async_url
from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.common import CUSTOMER_USER_CACHE_TYPES, USER_CACHE_TYPES
from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain import user_provisioning as up
from tiqora.domain.auth import AuthenticatedUser, SessionStore
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.domain.session_revocation import end_agent_sessions, end_customer_sessions

pytestmark = pytest.mark.db

NOW = datetime(2020, 1, 1, 12, 0, 0)
ACTOR = 63040
AGENT_A, AGENT_B, AGENT_C, AGENT_D = 63041, 63042, 63043, 63044
CUST_A, CUST_B = "sr63040.kim@example.test", "sr63040.lee@example.test"
_URLS: set[str] = set()
_INVAL_MARK: dict[str, int] = {}


def _settings() -> Settings:
    return Settings(secret_key="unit-test-secret-key")


def _stmts(url: str) -> tuple[tuple[str, dict[str, object]], ...]:
    band = {"lo": 63040, "hi": 63079}
    return (
        ("DELETE FROM sessions WHERE session_id LIKE 'SR6304%'", {}),
        (
            "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
            {"mx": _INVAL_MARK.get(url, 2**62)},
        ),
        ("DELETE FROM customer_user WHERE login LIKE 'sr63040.%'", {}),
        ("DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi", band),
        ("UPDATE users SET change_by = 1, create_by = 1 WHERE id BETWEEN :lo AND :hi", band),
        ("DELETE FROM users WHERE id BETWEEN :lo AND :hi", band),
    )


def _run(url: str, stmts: tuple[tuple[str, dict[str, object]], ...]) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in stmts:
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


def _seed(url: str) -> None:
    _run(url, ())
    if url not in _INVAL_MARK:
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _INVAL_MARK[url] = int(row.scalar_one())
        finally:
            engine.dispose()
    _run(url, _stmts(url))
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for uid in (ACTOR, AGENT_A, AGENT_B, AGENT_C, AGENT_D):
                conn.execute(
                    text(
                        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :l, 'x', 'Sr', 'Agent', 1, :t, 1, :t, 1)"
                    ),
                    {"id": uid, "l": f"sr{uid}.agent", "t": NOW},
                )
            for login in (CUST_A, CUST_B):
                conn.execute(
                    text(
                        "INSERT INTO customer_user (login, email, customer_id, pw, first_name,"
                        " last_name, valid_id, create_time, create_by, change_time, change_by)"
                        " VALUES (:l, :l, 'SR63040', 'x', 'Kim', 'Kunde', 1, :t, 1, :t, 1)"
                    ),
                    {"l": login, "t": NOW},
                )
            for sid, uid, utype in (
                ("SR6304A", str(AGENT_B), "User"),
                ("SR6304B", str(AGENT_C), "User"),
                ("SR6304C", str(AGENT_D), "User"),
                ("SR6304D", CUST_A, "Customer"),
                ("SR6304E", CUST_B, "Customer"),
            ):
                for key, val in (("UserID", uid), ("UserType", utype)):
                    conn.execute(
                        text(
                            "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                            " VALUES (:s, :k, :v, 0)"
                        ),
                        {"s": sid, "k": key, "v": val},
                    )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _run(url, _stmts(url))
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _seed(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


async def _inval(session: AsyncSession, types: tuple[str, ...]) -> int:
    count = 0
    for cache_type in types:
        count += int(
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE cache_type = :c"),
                    {"c": cache_type},
                )
            ).scalar_one()
        )
    return count


async def _znuny_session_ids(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT DISTINCT session_id FROM sessions WHERE session_id LIKE 'SR6304%'"
            " ORDER BY session_id"
        )
    )
    return [str(r[0]) for r in rows.all()]


def _request(redis: FakeRedis) -> Any:
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


async def test_set_agent_validity_writes_like_admin(session: AsyncSession) -> None:
    user = await session.get(Users, AGENT_A)
    assert user is not None
    before = await _inval(session, USER_CACHE_TYPES)
    assert await up.set_agent_validity(session, user, valid_id=2, actor_id=ACTOR) is True
    await session.commit()
    row = (
        await session.execute(
            text("SELECT valid_id, change_by, change_time FROM users WHERE id = :i"),
            {"i": AGENT_A},
        )
    ).one()
    assert (row[0], row[1]) == (2, ACTOR)
    assert row[2] > NOW
    after = await _inval(session, USER_CACHE_TYPES)
    assert after - before == len(set(USER_CACHE_TYPES))
    # unchanged value: no write, no new signal rows
    assert await up.set_agent_validity(session, user, valid_id=2, actor_id=ACTOR) is False
    await session.commit()
    assert await _inval(session, USER_CACHE_TYPES) == after


async def test_set_customer_validity(session: AsyncSession) -> None:
    from sqlalchemy import select

    cu = (
        await session.execute(select(CustomerUser).where(CustomerUser.login == CUST_A))
    ).scalar_one()
    before = await _inval(session, CUSTOMER_USER_CACHE_TYPES)
    assert await up.set_customer_validity(session, cu, valid_id=2, actor_id=ACTOR) is True
    await session.commit()
    row = (
        await session.execute(
            text("SELECT valid_id, change_by FROM customer_user WHERE login = :l"), {"l": CUST_A}
        )
    ).one()
    assert (row[0], row[1]) == (2, ACTOR)
    after = await _inval(session, CUSTOMER_USER_CACHE_TYPES)
    assert after - before == len(set(CUSTOMER_USER_CACHE_TYPES))
    assert await up.set_customer_validity(session, cu, valid_id=2, actor_id=ACTOR) is False
    await session.commit()
    assert await _inval(session, CUSTOMER_USER_CACHE_TYPES) == after


async def test_end_agent_sessions_removes_all_kinds(session: AsyncSession) -> None:
    redis = FakeRedis()
    settings = _settings()
    store = SessionStore(redis, settings)  # type: ignore[arg-type]
    full = await store.create(AGENT_B, f"sr{AGENT_B}.agent")
    pending = await store.create_pending(AGENT_B, f"sr{AGENT_B}.agent", 300)
    enroll = await store.create_enroll(AGENT_B, f"sr{AGENT_B}.agent", 300)
    other = await store.create(AGENT_C, f"sr{AGENT_C}.agent")
    removed = await end_agent_sessions(
        session,
        redis,  # type: ignore[arg-type]
        settings,
        AGENT_B,
        reason="account_disabled",
    )
    assert removed == 3
    assert await store.get(full) is None
    assert await store.get_pending(pending) is None
    assert await store.get_enroll(enroll) is None
    assert await store.get(other) is not None
    assert await _znuny_session_ids(session) == ["SR6304B", "SR6304C", "SR6304D", "SR6304E"]


class _BrokenRedis(FakeRedis):
    async def smembers(self, key: str) -> set[str]:
        raise ConnectionError("redis down")

    async def scan_iter(self, match: str = "*", count: int | None = None) -> AsyncIterator[str]:
        raise ConnectionError("redis down")
        yield ""  # pragma: no cover - makes this an async generator


async def test_end_agent_sessions_redis_down_never_raises(session: AsyncSession) -> None:
    removed = await end_agent_sessions(
        session,
        _BrokenRedis(),  # type: ignore[arg-type]
        _settings(),
        AGENT_B,
        reason="account_disabled",
    )
    assert removed == 0
    assert "SR6304A" not in await _znuny_session_ids(session)


async def test_end_customer_sessions_portal_and_gi_tokens(session: AsyncSession) -> None:
    redis = FakeRedis()
    settings = _settings()
    gi = SessionStore(redis, settings)  # type: ignore[arg-type]
    portal = CustomerSessionStore(redis, settings)  # type: ignore[arg-type]
    gi_token = await gi.create(0, CUST_A)  # GI customer tokens use the sentinel user id 0
    other_gi = await gi.create(0, CUST_B)
    portal_token = await portal.create(1, CUST_A)
    other_portal = await portal.create(2, CUST_B)
    removed = await end_customer_sessions(
        session,
        redis,  # type: ignore[arg-type]
        settings,
        [CUST_A],
        reason="account_disabled",
    )
    assert removed == 2
    assert await gi.get(gi_token) is None
    assert await portal.get(portal_token) is None
    assert await gi.get(other_gi) is not None
    assert await portal.get(other_portal) is not None
    assert "SR6304D" not in await _znuny_session_ids(session)
    assert "SR6304E" in await _znuny_session_ids(session)
    # Redis outage: still no raise, Znuny rows of the other login are deleted
    assert (
        await end_customer_sessions(
            session,
            _BrokenRedis(),  # type: ignore[arg-type]
            settings,
            [CUST_B],
            reason="account_disabled",
        )
        == 0
    )
    assert "SR6304E" not in await _znuny_session_ids(session)


async def test_admin_deactivate_uses_service(session: AsyncSession) -> None:
    redis = FakeRedis()
    settings = _settings()
    store = SessionStore(redis, settings)  # type: ignore[arg-type]
    victim = await store.create(AGENT_C, f"sr{AGENT_C}.agent")
    bystander = await store.create(AGENT_D, f"sr{AGENT_D}.agent")
    before = await _inval(session, USER_CACHE_TYPES)
    await admin_users.deactivate_user(AGENT_C, _request(redis), _admin(), session, settings)
    row = (
        await session.execute(
            text("SELECT valid_id, change_by FROM users WHERE id = :i"), {"i": AGENT_C}
        )
    ).one()
    assert (row[0], row[1]) == (2, 1)
    assert await _inval(session, USER_CACHE_TYPES) - before == len(set(USER_CACHE_TYPES))
    assert await store.get(victim) is None
    assert await store.get(bystander) is not None
    assert "SR6304B" not in await _znuny_session_ids(session)
    assert "SR6304C" in await _znuny_session_ids(session)
