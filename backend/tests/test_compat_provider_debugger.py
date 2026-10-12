"""Provider traffic in the GenericInterface debugger (MariaDB and PostgreSQL).

Seed band 51370-51389: webservices 51370-51373, agent 51374. Debug entry and content ids are
autoincrement: every row this module adds is deleted again via ``tests._row_cleanup`` marks.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.password import hash_password

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

NOW = datetime(2026, 3, 1, 12, 0, 0)
PASSWORD = "dbg-provider-secret"
LOGIN = "gi51370.agent"
AGENT_ID = 51374
WS_DEBUG, WS_ERROR, WS_SOAP, WS_BOOM = 51370, 51371, 51372, 51373
CLIENT = "203.0.113.9"
_TABLES = ("tiqora_gi_debug_content", "tiqora_gi_debug_entry")


def _rest_cfg(threshold: str, routes: dict[str, str]) -> dict[str, Any]:
    return {
        "Debugger": {"DebugThreshold": threshold, "TestMode": "0"},
        "Provider": {
            "Transport": {
                "Type": "HTTP::REST",
                "Config": {
                    "RouteOperationMapping": {
                        name: {"Route": route, "RequestMethod": ["POST"]}
                        for name, route in routes.items()
                    }
                },
            },
            "Operation": {name: {"Type": "Session::SessionCreate"} for name in routes},
        },
    }


_SOAP_CFG: dict[str, Any] = {
    "Debugger": {"DebugThreshold": "debug", "TestMode": "0"},
    "Provider": {
        "Transport": {"Type": "HTTP::SOAP", "Config": {"NameSpace": "http://example.org/Dbg/"}},
        "Operation": {
            "Login": {
                "Type": "Session::SessionCreate",
                "MappingOutbound": {
                    "Type": "Simple",
                    "Config": {
                        "KeyMapExact": {"SessionID": "Token"},
                        "KeyMapDefault": {"MapTo": "", "MapType": "Keep"},
                        "ValueMapDefault": {"MapTo": "", "MapType": "Keep"},
                    },
                },
            }
        },
    },
}

_WEBSERVICES = {
    WS_DEBUG: ("gi51370-debug", _rest_cfg("debug", {"Login": "/Login"})),
    WS_ERROR: ("gi51371-error", _rest_cfg("error", {"Login": "/Login"})),
    WS_SOAP: ("gi51372-soap", _SOAP_CFG),
    WS_BOOM: ("gi51373-boom", _rest_cfg("error", {"Boom": "/Boom"})),
}

_STATE: dict[str, dict[str, int]] = {}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup_rows(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM gi_webservice_config WHERE id BETWEEN 51370 AND 51373"))
        conn.execute(
            text(
                "DELETE FROM sessions WHERE session_id IN (SELECT session_id FROM"
                " (SELECT session_id FROM sessions WHERE data_key = 'UserLogin'"
                " AND data_value = :login) s)"
            ),
            {"login": LOGIN},
        )
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": AGENT_ID})
    engine.dispose()


def _seed(sync_url: str) -> None:
    if sync_url in _STATE:
        return
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup_rows(sync_url)
    _STATE[sync_url] = snapshot_marks(sync_url, _TABLES)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Dbg', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "login": LOGIN, "pw": hash_password(PASSWORD), "t": NOW},
        )
        for ws_id, (name, cfg) in _WEBSERVICES.items():
            raw = yaml.safe_dump(cfg)
            stored: bytes | str = raw if engine.dialect.name == "postgresql" else raw.encode()
            conn.execute(
                text(
                    "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, :name, :config, 1, :t, 1, :t, 1)"
                ),
                {"id": ws_id, "name": name, "config": stored, "t": NOW},
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _STATE.items():
        delete_above_marks(sync_url, marks)
        _cleanup_rows(sync_url)
    _STATE.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


async def _client_for(sync_url: str, *, trusted_proxies: str = "127.0.0.1") -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings
    from tiqora.domain.auth import SessionStore

    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    class _FakeRedis:
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
        session_cookie_name = "tiqora_session"

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test", trusted_proxies_raw=trusted_proxies))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


def _entries(sync_url: str, ws_id: int) -> list[dict[str, Any]]:
    """Entries of *ws_id* (oldest first), each with its lines ``[(level, subject, content)]``."""
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, communication_type, remote_ip, name FROM tiqora_gi_debug_entry"
                    " WHERE webservice_id = :ws ORDER BY id"
                ),
                {"ws": ws_id},
            ).all()
            out = []
            for entry_id, ctype, remote_ip, name in rows:
                lines = conn.execute(
                    text(
                        "SELECT debug_level, subject, content FROM tiqora_gi_debug_content"
                        " WHERE entry_id = :e ORDER BY id"
                    ),
                    {"e": entry_id},
                ).all()
                out.append(
                    {
                        "type": ctype,
                        "remote_ip": remote_ip,
                        "name": name,
                        "lines": [
                            (lvl, subj, bytes(body).decode("utf-8") if body is not None else "")
                            for lvl, subj, body in lines
                        ],
                    }
                )
        return out
    finally:
        engine.dispose()


def _count_all(sync_url: str) -> int:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(text("SELECT COUNT(*) FROM tiqora_gi_debug_entry")).scalar_one()
            )
    finally:
        engine.dispose()


def _clear(sync_url: str, ws_id: int) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM tiqora_gi_debug_content WHERE entry_id IN"
                    " (SELECT id FROM tiqora_gi_debug_entry WHERE webservice_id = :ws)"
                ),
                {"ws": ws_id},
            )
            conn.execute(
                text("DELETE FROM tiqora_gi_debug_entry WHERE webservice_id = :ws"), {"ws": ws_id}
            )
    finally:
        engine.dispose()


async def test_rest_provider_call_logged_with_client_ip(sync_url: str) -> None:
    _clear(sync_url, WS_DEBUG)
    client, engine = await _client_for(sync_url)
    async with client:
        resp = await client.post(
            "/znuny-compat/Webservice/gi51370-debug/Login",
            json={"UserLogin": LOGIN, "Password": PASSWORD},
            headers={"X-Forwarded-For": CLIENT},
        )
    await engine.dispose()
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["SessionID"]
    assert session_id

    entries = _entries(sync_url, WS_DEBUG)
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["type"], entry["remote_ip"], entry["name"]) == ("Provider", CLIENT, "Login")
    # Provider.pm: started, detected operation, before mapping (in), before mapping (out)
    assert [subject for _, subject, _ in entry["lines"]] == [
        "Communication sequence started",
        "Detected operation 'Login'",
        "Incoming data before mapping",
        "Outgoing data before mapping",
    ]
    assert {level for level, _, _ in entry["lines"]} == {"debug"}
    blob = "\n".join(body for _, _, body in entry["lines"])
    assert PASSWORD not in blob
    assert session_id not in blob
    parsed = json.loads(entry["lines"][2][2])
    assert parsed == {"Password": "***", "UserLogin": LOGIN}
    assert json.loads(entry["lines"][3][2])["SessionID"] == "***"
    assert json.loads(entry["lines"][0][2])["RequestMethod"] == "POST"


async def test_untrusted_peer_ignores_forwarded_for(sync_url: str) -> None:
    _clear(sync_url, WS_DEBUG)
    client, engine = await _client_for(sync_url, trusted_proxies="")
    async with client:
        resp = await client.post(
            "/znuny-compat/Webservice/gi51370-debug/Login",
            json={"UserLogin": LOGIN, "Password": PASSWORD},
            headers={"X-Forwarded-For": CLIENT},
        )
    await engine.dispose()
    assert resp.status_code == 200, resp.text
    assert _entries(sync_url, WS_DEBUG)[0]["remote_ip"] == "127.0.0.1"


async def test_threshold_error_logs_only_faults(sync_url: str) -> None:
    _clear(sync_url, WS_ERROR)
    client, engine = await _client_for(sync_url)
    async with client:
        ok = await client.post(
            "/znuny-compat/Webservice/gi51371-error/Login",
            json={"UserLogin": LOGIN, "Password": PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        assert _entries(sync_url, WS_ERROR) == []

        bad = await client.post(
            "/znuny-compat/Webservice/gi51371-error/Login",
            json={"UserLogin": LOGIN, "Password": "wrong-password-value"},
        )
        assert bad.status_code == 401, bad.text
    await engine.dispose()
    entries = _entries(sync_url, WS_ERROR)
    assert len(entries) == 1
    lines = entries[0]["lines"]
    assert lines and {level for level, _, _ in lines} == {"error"}
    assert "AuthFail" in lines[0][1]
    assert "wrong-password-value" not in "\n".join(body for _, _, body in lines)


async def test_operation_fault_keeps_trail_after_rollback(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.api.compat import router
    from tiqora.api.compat.provider import OperationFault

    _clear(sync_url, WS_BOOM)

    async def _boom(
        operation: str, data: dict[str, Any], session: AsyncSession, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        # a write on the request session that is rolled back together with the request
        await session.execute(
            text("UPDATE users SET first_name = 'Rolled' WHERE id = :id"), {"id": AGENT_ID}
        )
        raise OperationFault("OperationObject returned an error, cancelling Request")

    monkeypatch.setattr(router, "_dispatch_operation", _boom)
    client, engine = await _client_for(sync_url)
    async with client:
        resp = await client.post("/znuny-compat/Webservice/gi51373-boom/Boom", json={"A": "b"})
    await engine.dispose()
    assert resp.status_code == 500
    assert resp.json()["faultstring"] == "OperationObject returned an error, cancelling Request"

    entries = _entries(sync_url, WS_BOOM)
    assert len(entries) == 1
    assert entries[0]["name"] == "Boom"
    assert entries[0]["lines"] == [
        ("error", "OperationObject returned an error, cancelling Request", "No data provided")
    ]
    check = create_engine(sync_url)
    try:
        with check.connect() as conn:
            name = conn.execute(
                text("SELECT first_name FROM users WHERE id = :id"), {"id": AGENT_ID}
            ).scalar_one()
    finally:
        check.dispose()
    assert name == "Dbg"


async def test_canonical_routes_not_logged(sync_url: str) -> None:
    before = _count_all(sync_url)
    client, engine = await _client_for(sync_url)
    envelope = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body><SessionCreate>"
        f"<UserLogin>{LOGIN}</UserLogin><Password>{PASSWORD}</Password>"
        "</SessionCreate></soapenv:Body></soapenv:Envelope>"
    ).encode()
    async with client:
        rest = await client.post(
            "/znuny-compat/Session", json={"UserLogin": LOGIN, "Password": PASSWORD}
        )
        soap = await client.post(
            "/znuny-compat/soap/anything", content=envelope, headers={"Content-Type": "text/xml"}
        )
    await engine.dispose()
    assert rest.status_code == 200, rest.text
    assert soap.status_code == 200, soap.text
    assert _count_all(sync_url) == before


async def test_soap_provider_logged(sync_url: str) -> None:
    _clear(sync_url, WS_SOAP)
    envelope = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body><Login>"
        f"<UserLogin>{LOGIN}</UserLogin><Password>{PASSWORD}</Password>"
        "</Login></soapenv:Body></soapenv:Envelope>"
    ).encode()
    client, engine = await _client_for(sync_url)
    async with client:
        resp = await client.post(
            "/znuny-compat/Webservice/gi51372-soap",
            content=envelope,
            headers={"Content-Type": "text/xml", "X-Forwarded-For": CLIENT},
        )
    await engine.dispose()
    assert resp.status_code == 200, resp.text
    entries = _entries(sync_url, WS_SOAP)
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["type"], entry["remote_ip"], entry["name"]) == ("Provider", CLIENT, "Login")
    assert [subject for _, subject, _ in entry["lines"]] == [
        "Communication sequence started",
        "Detected operation 'Login'",
        "Incoming data before mapping",
        "Outgoing data before mapping",
        "Outgoing data after mapping",
    ]
    blob = "\n".join(body for _, _, body in entry["lines"])
    assert PASSWORD not in blob
    after = json.loads(entry["lines"][4][2])
    assert after["Token"] == "***"
