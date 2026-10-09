"""SessionCreate writes Znuny ``sessions`` rows (both dialects).

Seed ids live in the band 8970-8979; every row is deleted again after the module.
"""

from __future__ import annotations

import re
import time
from collections.abc import Generator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from tiqora.api.compat import operations
from tiqora.api.compat.operations import (
    op_session_create,
    op_session_get,
    op_session_remove,
    op_ticket_search,
)
from tiqora.api.compat.znuny_session import delete_znuny_agent_sessions
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

AGENT, OTHER_AGENT = 8970, 8971
LOGIN, OTHER_LOGIN = "gi8970.agent", "gi8971.agent"
CUSTOMER = "gi8970.cust@example.test"
PASSWORD = "pw8970-example-only"
PW_HASH = hash_password(PASSWORD, cost=9)
_SEEDED: list[str] = []

_CLEANUP = (
    "DELETE FROM sessions WHERE session_id IN (SELECT session_id FROM"
    " (SELECT session_id FROM sessions WHERE data_key = 'UserLogin'"
    " AND data_value IN ('gi8970.agent', 'gi8971.agent', 'gi8970.cust@example.test')) s)",
    "DELETE FROM sessions WHERE session_id LIKE 'GI8970%'",
    "DELETE FROM ticket WHERE id = 8970",
    "DELETE FROM queue WHERE id = 8970",
    "DELETE FROM group_user WHERE group_id = 8970",
    "DELETE FROM permission_groups WHERE id = 8970",
    "DELETE FROM user_preferences WHERE user_id IN (8970, 8971)",
    "DELETE FROM customer_preferences WHERE user_id = 'gi8970.cust@example.test'",
    "DELETE FROM customer_user WHERE login = 'gi8970.cust@example.test'",
    "DELETE FROM users WHERE id IN (8970, 8971)",
)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = datetime(2024, 6, 1, 12, 0, 0)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        for uid, login, first in ((AGENT, LOGIN, "Gina"), (OTHER_AGENT, OTHER_LOGIN, "Otto")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, title, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, :pw, 'Dr.', :f, 'Example', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "pw": PW_HASH, "f": first, "t": now},
            )
        for key, val in (
            ("UserEmail", "gina8970@example.test"),
            ("UserGoogleAuthenticatorSecretKey", "JBSWY3DPEHPK3PXP"),
            ("UserTOTPSeed", "JBSWY3DPEHPK3PXQ"),
            ("UserAuthBackend", "LDAP"),
            ("UserSMIMEHash", "abc123"),
            ("UserLanguage", "de"),
        ):
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (8970, :k, :v)"
                ),
                {"k": key, "v": val},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (8970, 'gi8970-group', 1, :t, 1, :t, 1)"
            ),
            {"t": now},
        )
        for uid in (AGENT, OTHER_AGENT):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, 8970, 'ro', :t, 1, :t, 1)"
                ),
                {"u": uid, "t": now},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (8970, 'Q8970', 8970, 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {"t": now},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
                " create_by, change_time, change_by) VALUES (8970, '8970001', 'Example', 8970,"
                " 1, 1, 8970, 1, 3, (SELECT id FROM ticket_state WHERE name = 'new'), 'GI8970',"
                " 'gi8970.cust', 0, 0, 0, 0, 0, 0, 0, :t, 8970, :t, 8970)"
            ),
            {"t": now},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, pw, title, first_name,"
                " last_name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, :l, 'GI8970', :pw, '', 'Cora', 'Customer', 1, :t, 1, :t, 1)"
            ),
            {"l": CUSTOMER, "pw": PW_HASH, "t": now},
        )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.data.pop(key, None)

    async def sadd(self, key: str, *members: str) -> int:
        return 0


def _store(redis: _FakeRedis | None = None) -> SessionStore:
    return SessionStore(redis or _FakeRedis(), Settings(secret_key="unit-test-secret-key"))  # type: ignore[arg-type]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"user-agent", b"gi-test-agent/1.0")],
            "client": ("203.0.113.9", 4711),
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


@pytest.fixture
def _no_2fa(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never(session: AsyncSession, user_id: int) -> bool:
        return False

    monkeypatch.setattr(operations, "_agent_password_blocked_by_2fa", _never)


class _Db:
    def __init__(self, sync_url: str) -> None:
        self.engine = create_async_engine(_async_url(sync_url))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def rows(self, sid: str) -> dict[str, tuple[str, int]]:
        async with self.factory() as s:
            res = await s.execute(
                text("SELECT data_key, data_value, serialized FROM sessions WHERE session_id=:s"),
                {"s": sid},
            )
            return {str(k): (str(v), int(z)) for k, v, z in res.all()}

    async def create(self, data: dict[str, Any], settings: Settings | None = None) -> Any:
        async with self.factory() as s:
            return await op_session_create(data, s, _store(), request=_request(), settings=settings)


@pytest.mark.usefixtures("_no_2fa")
async def test_session_create_writes_znuny_rows(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        before = int(time.time())
        out = await db.create({"UserLogin": LOGIN, "Password": PASSWORD})
        sid = out["SessionID"]
        assert re.fullmatch(r"[A-Za-z0-9]{32}", sid)
        rows = await db.rows(sid)
        flat = {k: v for k, (v, _z) in rows.items()}
        assert flat["UserID"] == "8970"
        assert flat["UserLogin"] == LOGIN
        assert flat["UserType"] == "User"
        assert flat["SessionSource"] == "GenericInterface"
        assert flat["UserEmail"] == "gina8970@example.test"
        assert flat["UserFullname"] == "Gina Example"
        assert flat["UserRemoteAddr"] == "203.0.113.9"
        assert flat["UserRemoteUserAgent"] == "gi-test-agent/1.0"
        assert re.fullmatch(r"[A-Za-z0-9]{32}", flat["UserChallengeToken"])
        assert before <= int(flat["UserSessionStart"]) <= int(time.time())
        assert flat["UserLastRequest"] == flat["UserSessionStart"]
        assert "UserPw" not in rows
        assert flat["UserLanguage"] == "de"
        # Allow-list: nothing outside it is copied, whatever it is called.
        for unlisted in (
            "UserGoogleAuthenticatorSecretKey",
            "UserTOTPSeed",
            "UserAuthBackend",
            "UserSMIMEHash",
        ):
            assert unlisted not in rows
        assert all(z == 0 for _v, z in rows.values())

        async with db.factory() as s:
            found = await op_ticket_search(
                {"SessionID": sid, "QueueIDs": [8970]}, s, _store(), request=_request()
            )
            assert found["TicketID"] == [8970]
            got = await op_session_get({"SessionID": sid}, s, _store())
        keys = {e["Key"]: e["Value"] for e in got["SessionData"]}
        assert keys["UserLogin"] == LOGIN
        assert "UserChallengeToken" not in keys
        assert "UserPw" not in keys
        assert "UserGoogleAuthenticatorSecretKey" not in keys

        # SessionRemove deletes only the rows of that SessionID.
        other = (await db.create({"UserLogin": OTHER_LOGIN, "Password": PASSWORD}))["SessionID"]
        async with db.factory() as s:
            assert await op_session_remove({"SessionID": sid}, s, _store()) == {"Success": 1}
        assert await db.rows(sid) == {}
        assert (await db.rows(other))["UserLogin"][0] == OTHER_LOGIN
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
async def test_agent_without_email_preference_gets_login_as_email(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        out = await db.create({"UserLogin": OTHER_LOGIN, "Password": PASSWORD})
        flat = {k: v for k, (v, _z) in (await db.rows(out["SessionID"])).items()}
        assert flat["UserEmail"] == OTHER_LOGIN
    finally:
        await db.engine.dispose()


class _Cfg:
    def __init__(self, module: str) -> None:
        self.module = module

    async def get(self, name: str, default: Any = None) -> Any:
        return self.module if name == "SessionModule" else default


async def test_session_module_gate() -> None:
    from tiqora.api.compat.znuny_session import znuny_session_writes_enabled

    cfg = Settings(secret_key="unit-test-secret-key")
    db_mod = "Kernel::System::AuthSession::DB"
    assert await znuny_session_writes_enabled(cfg, _Cfg(db_mod))  # type: ignore[arg-type]
    for other in ("Kernel::System::AuthSession::FS", "Kernel::System::AuthSession::Redis"):
        assert not await znuny_session_writes_enabled(cfg, _Cfg(other))  # type: ignore[arg-type]


@pytest.mark.usefixtures("_no_2fa")
async def test_session_create_customer_rows(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        out = await db.create({"CustomerUserLogin": CUSTOMER, "Password": PASSWORD})
        flat = {k: v for k, (v, _z) in (await db.rows(out["SessionID"])).items()}
        assert flat["UserType"] == "Customer"
        assert flat["UserCustomerID"] == "GI8970"
        assert flat["UserLogin"] == CUSTOMER
        assert flat["UserID"] == CUSTOMER
        assert flat["UserEmail"] == CUSTOMER
        assert "UserPw" not in flat
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
async def test_gi_session_use_does_not_refresh_last_request(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        sid = (await db.create({"UserLogin": LOGIN, "Password": PASSWORD}))["SessionID"]
        stamp = str(int(time.time()) - 600)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE sessions SET data_value = :v WHERE session_id = :s"
                    " AND data_key = 'UserLastRequest'"
                ),
                {"v": stamp, "s": sid},
            )
        engine.dispose()
        for _ in range(2):
            async with db.factory() as s:
                res = await op_ticket_search(
                    {"SessionID": sid, "QueueIDs": [8970]}, s, _store(), request=_request()
                )
            assert res["TicketID"] == [8970]
        assert (await db.rows(sid))["UserLastRequest"][0] == stamp
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
@pytest.mark.parametrize("variant", ["setting_off", "schema_ownership"])
async def test_session_writes_disabled_by_setting_or_ownership(sync_url: str, variant: str) -> None:
    flags = (
        {"compat_session_write_znuny": False}
        if variant == "setting_off"
        else {"schema_ownership": True}
    )
    cfg = Settings(secret_key="unit-test-secret-key", **flags)
    db = _Db(sync_url)
    redis = _FakeRedis()
    try:
        async with db.factory() as s:
            out = await op_session_create(
                {"UserLogin": LOGIN, "Password": PASSWORD},
                s,
                _store(redis),
                request=_request(),
                settings=cfg,
            )
        sid = out["SessionID"]
        assert await db.rows(sid) == {}
        assert any(sid in k for k in redis.data)
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
async def test_disabled_account_session_stops_working(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        sid = (await db.create({"UserLogin": LOGIN, "Password": PASSWORD}))["SessionID"]
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET valid_id = 2 WHERE id = 8970"))
        try:
            async with db.factory() as s:
                res = await op_ticket_search(
                    {"SessionID": sid, "QueueIDs": [8970]}, s, _store(), request=_request()
                )
            assert res["Error"]["ErrorCode"].endswith("AuthFail")
        finally:
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET valid_id = 1 WHERE id = 8970"))
            engine.dispose()
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
async def test_revocation_deletes_only_that_agents_znuny_sessions(sync_url: str) -> None:
    db = _Db(sync_url)
    try:
        mine = [
            (await db.create({"UserLogin": LOGIN, "Password": PASSWORD}))["SessionID"]
            for _ in range(2)
        ]
        theirs = (await db.create({"UserLogin": OTHER_LOGIN, "Password": PASSWORD}))["SessionID"]
        async with db.factory() as s:
            assert await delete_znuny_agent_sessions(s, AGENT) == 2
            await s.commit()
        assert [await db.rows(sid) for sid in mine] == [{}, {}]
        assert (await db.rows(theirs))["UserID"][0] == "8971"
    finally:
        await db.engine.dispose()


@pytest.mark.usefixtures("_no_2fa")
async def test_session_get_checks_validity_first_like_znuny(sync_url: str) -> None:
    """SessionGet.pm: CheckSessionID first, else SessionGet.SessionInvalid (no data)."""
    invalid = {
        "Error": {
            "ErrorCode": "SessionGet.SessionInvalid",
            "ErrorMessage": "SessionGet: SessionID is Invalid!",
        }
    }
    db = _Db(sync_url)
    engine = create_engine(sync_url)
    try:
        sid = (await db.create({"UserLogin": LOGIN, "Password": PASSWORD}))["SessionID"]
        async with db.factory() as s:
            assert "SessionData" in await op_session_get({"SessionID": sid}, s, _store())

        # idle for longer than SessionMaxIdleTime: the row is still there, but invalid
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE sessions SET data_value = :v WHERE session_id = :s"
                    " AND data_key = 'UserLastRequest'"
                ),
                {"v": str(int(time.time()) - 7 * 24 * 3600), "s": sid},
            )
        assert (await db.rows(sid))["UserLogin"][0] == LOGIN
        async with db.factory() as s:
            assert await op_session_get({"SessionID": sid}, s, _store()) == invalid

        # a disabled account's live session is invalid as well
        sid2 = (await db.create({"UserLogin": LOGIN, "Password": PASSWORD}))["SessionID"]
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET valid_id = 2 WHERE id = 8970"))
        try:
            async with db.factory() as s:
                assert await op_session_get({"SessionID": sid2}, s, _store()) == invalid
        finally:
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET valid_id = 1 WHERE id = 8970"))
    finally:
        engine.dispose()
        await db.engine.dispose()
