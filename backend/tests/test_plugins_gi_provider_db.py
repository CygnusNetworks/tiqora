"""GenericInterface operations and mappings from plugins (D14, both dialects).

Seed band 55430-55449: agent 55430 (``pp55-gi-agent``) and the webservice ``PP55Contracts``
(id 55431). Debugger rows are autoincrement: they are removed with the row marks.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import insert_webservice
from tests._plugin_points import PLUGIN_ID, boot_points_plugin, points_behaviour
from tests._plugins_state_helpers import to_async_url
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.compat.mapping import MappingConfigError, MappingError, build_mapper
from tiqora.api.compat.webservices import OPERATION_TYPES, parse_webservice
from tiqora.api.deps import get_db, get_session_store
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 13, 9, 0, 0)
AGENT_ID, WS_ID = 55430, 55431
LOGIN, PASSWORD = "pp55-gi-agent", "pp55-gi-secret"
BASE = "/znuny-compat/Webservice/PP55Contracts/Contract/1"
_MARK_TABLES = ("tiqora_gi_debug_content", "tiqora_gi_debug_entry")

CONFIG: dict[str, Any] = {
    "Debugger": {"DebugThreshold": "debug", "TestMode": "0"},
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {
                    "ContractGet": {"Route": "/Contract/:TicketID", "RequestMethod": ["POST"]},
                    "ContractMapped": {"Route": "/Mapped/:TicketID", "RequestMethod": ["POST"]},
                }
            },
        },
        "Operation": {
            "ContractGet": {"Type": "Plugin_points::ContractGet"},
            "ContractMapped": {
                "Type": "Plugin_points::ContractGet",
                "MappingOutbound": {"Type": "Plugin_points_Upper"},
            },
        },
    },
}


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


class _Ttl:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM gi_webservice_config WHERE id = :i"), {"i": WS_ID})
        conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": AGENT_ID})
    engine.dispose()


class Env:
    def __init__(self, url: str, factory: async_sessionmaker[AsyncSession]) -> None:
        self.url = url
        self.factory = factory

    def client(self) -> AsyncClient:
        factory = self.factory

        async def _db() -> Any:
            async with factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_store] = lambda: SessionStore(_FakeRedis(), _Ttl())  # type: ignore[arg-type]
        app.state.session_factory = factory
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def call(self, path: str = BASE, **auth: str) -> Any:
        body = auth or {"UserLogin": LOGIN, "Password": PASSWORD}
        async with self.client() as client:
            return await client.post(path, json=body)

    def debug_lines(self) -> list[tuple[str, str]]:
        engine = create_engine(self.url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT c.debug_level, c.subject FROM tiqora_gi_debug_content c"
                        " JOIN tiqora_gi_debug_entry e ON e.id = c.entry_id"
                        " WHERE e.webservice_id = :w ORDER BY c.id"
                    ),
                    {"w": WS_ID},
                ).all()
            return [(r[0], r[1]) for r in rows]
        finally:
            engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _clean(url)
    marks = snapshot_marks(url, _MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Gi', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "login": LOGIN, "pw": hash_password(PASSWORD), "t": NOW},
        )
        insert_webservice(conn, ws_id=WS_ID, name="PP55Contracts", config=CONFIG)
    engine.dispose()
    async_engine = create_async_engine(to_async_url(url))
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(url, factory)
    await async_engine.dispose()
    delete_above_marks(url, marks)
    _clean(url)


async def test_plugin_operation_via_rest(env: Env) -> None:
    resp = await env.call(BASE.replace("/Contract/", "/Mapped/"))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"Contract": "V-1", "UserID": AGENT_ID}
    (call,) = [c for c in points_behaviour().CALLS if c[0] == "gi.op"]
    assert call[1][:2] == ("PP55Contracts", "ContractMapped")
    assert call[1][2]["TicketID"] == "1"  # route variable reaches the plugin
    lines = env.debug_lines()  # debugger rows like a core operation, incl. the plugin mapping
    assert ("debug", "Detected operation 'ContractMapped'") in lines
    assert ("debug", "Outgoing data before mapping") in lines
    assert ("debug", "Outgoing data after mapping") in lines


async def test_auth_failure_returns_znuny_error(env: Env) -> None:
    resp = await env.call(UserLogin=LOGIN, Password="falsch")
    assert resp.status_code == 401, resp.text
    assert resp.json()["Error"]["ErrorCode"].endswith("AuthFail")


@pytest.mark.parametrize("mode", ["raise", "hang", "nondict"])
async def test_operation_failure_is_fault(env: Env, mode: str) -> None:
    points_behaviour().MODE["gi.op"] = mode
    started = time.monotonic()
    resp = await env.call()
    assert resp.status_code == 500, resp.text
    assert resp.json()["faultstring"] == "OperationObject returned an error, cancelling Request"
    assert time.monotonic() - started < 6  # a hanging operation is cut at timeout_seconds=1
    assert ("error", "OperationObject returned an error, cancelling Request") in env.debug_lines()


async def test_disabled_plugin_not_implemented(env: Env) -> None:
    body = {"UserLogin": LOGIN, "Password": PASSWORD}
    async with env.client() as client:  # one app: the webservice snapshot is parsed while active
        assert (await client.post(BASE, json=body)).status_code == 200
        points_behaviour().CALLS.clear()
        set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.DISABLED, None, ())])
        resp = await client.post(BASE, json=body)
    assert resp.status_code == 501, resp.text
    assert resp.json()["Error"]["ErrorCode"] == "Operation.NotImplemented"
    assert points_behaviour().CALLS == []


def test_core_type_wins(env: Env) -> None:
    config = {
        "Provider": {
            "Transport": {"Type": "HTTP::REST", "Config": {}},
            "Operation": {
                "Core": {"Type": "Ticket::TicketGet"},
                "Plug": {"Type": "Plugin_points::ContractGet"},
                "Unknown": {"Type": "Plugin_points::Nope"},
            },
        }
    }
    ws = parse_webservice(1, "ws", config, huge_xml=False)
    assert ws.operations["Core"].handler == OPERATION_TYPES["Ticket::TicketGet"] == "TicketGet"
    assert ws.operations["Plug"].handler == "plugin:Plugin_points::ContractGet"
    assert ws.operations["Plug"].webservice_id == 1
    assert ws.operations["Unknown"].handler is None
    assert "Plugin_points::ContractGet" not in OPERATION_TYPES


def test_core_name_cannot_be_registered(env: Env) -> None:
    from tests.test_plugins_registrar import _registrar
    from tiqora.plugins.registrar import RegistrationRejected

    async def run(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    registrar = _registrar(points=["core.gi.operations"])
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        registrar.gi_operation("Ticket::TicketCreate", run)
    assert registrar.staged == ()
    assert registrar.gi_operation("TicketCreate", run) == "Plugin_hello::TicketCreate"


def test_plugin_mapping(env: Env) -> None:
    mapper = build_mapper({"Type": "Plugin_points_Upper", "Config": {"a": "b"}})
    assert mapper.map({"a": "x"}) == {"a": "X"}
    points_behaviour().MODE["gi.map"] = "raise"
    with pytest.raises(MappingError, match="Plugin_points_Upper: RuntimeError: kaputt"):
        mapper.map({"a": "x"})


def test_plugin_mapping_factory_failure_is_config_error(env: Env) -> None:
    points_behaviour().MODE["gi.map"] = "factory_raise"
    with pytest.raises(MappingConfigError, match="Plugin_points_Upper: RuntimeError"):
        build_mapper({"Type": "Plugin_points_Upper"})


def test_unknown_mapping_type_unchanged(env: Env) -> None:
    with pytest.raises(
        MappingConfigError, match="Mapping type 'Nope' is not supported by Tiqora"
    ) as info:
        build_mapper({"Type": "Nope"})
    assert str(info.value) == "Mapping type 'Nope' is not supported by Tiqora"
    with pytest.raises(MappingConfigError, match="Plugin_points_Missing"):
        build_mapper({"Type": "Plugin_points_Missing"})


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"Success": 1, "Data": {"a": 1}}, {"a": 1}),
        ({"Success": 1}, {}),
        ({"Success": 1, "Data": "text"}, None),
        (
            {"Success": 0, "ErrorCode": "Pp55.Nope", "ErrorMessage": "nein"},
            {"Error": {"ErrorCode": "Pp55.Nope", "ErrorMessage": "nein"}},
        ),
        (
            {"Success": 0},
            {"Error": {"ErrorCode": "Operation.Failed", "ErrorMessage": "The operation failed"}},
        ),
        (
            {"Error": {"ErrorCode": "X", "ErrorMessage": "y"}},
            {"Error": {"ErrorCode": "X", "ErrorMessage": "y"}},
        ),
        ({"Plain": "body"}, {"Plain": "body"}),
    ],
)
def test_znuny_result_translation(result: dict[str, Any], expected: Any) -> None:
    from tiqora.plugins.gi_points import _znuny_result

    assert _znuny_result(result) == expected
