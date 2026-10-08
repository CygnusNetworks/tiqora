"""Request-time webservice routes end-to-end (MariaDB and PostgreSQL).

Hits ``/znuny-compat/Webservice(ID)/...`` through the ASGI app: registry lookup, REST route
matching, inbound/outbound mapping and the shared ``op_*`` handlers. Seed ids use 8920-8939
(GI band: agent 8920, group 8921, queue 8922, webservices 8923-8926); everything is deleted
again after the module.
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

from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.password import hash_password

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

NOW = datetime(2026, 3, 1, 12, 0, 0)
PASSWORD = "wsmap-secret"
LOGIN = "wsmap.agent"
AGENT_ID, GROUP_ID, QUEUE_ID = 8920, 8921, 8922
MAP_ID, BROKEN_ID, SOAP_ID, PLAIN_ID = 8923, 8924, 8925, 8926

_OUT_XSLT = (
    '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:output method="xml" encoding="utf-8"/>'
    '<xsl:template match="/RootElement"><R><Number>'
    '<xsl:value-of select="/RootElement/Ticket/TicketNumber"/>'
    "</Number></R></xsl:template></xsl:stylesheet>"
)


def _simple(exact: dict[str, str]) -> dict[str, Any]:
    return {
        "Type": "Simple",
        "Config": {
            "KeyMapExact": exact,
            "KeyMapDefault": {"MapTo": "", "MapType": "Keep"},
            "ValueMapDefault": {"MapTo": "", "MapType": "Keep"},
        },
    }


MAP_CFG: dict[str, Any] = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {
                    "MakeTicket": {"Route": "/Make", "RequestMethod": ["POST"]},
                    "Fetch": {"Route": "/Fetch/:Id", "RequestMethod": ["GET"]},
                }
            },
        },
        "Operation": {
            "MakeTicket": {
                "Type": "Ticket::TicketCreate",
                "MappingInbound": _simple({"Agent": "UserLogin", "Secret": "Password"}),
            },
            "Fetch": {
                "Type": "Ticket::TicketGet",
                "MappingInbound": _simple(
                    {"Id": "TicketID", "Agent": "UserLogin", "Secret": "Password"}
                ),
                "MappingOutbound": {"Type": "XSLT", "Config": {"Template": _OUT_XSLT}},
            },
        },
    }
}

BROKEN_CFG: dict[str, Any] = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {"Bad": {"Route": "/Bad", "RequestMethod": ["GET"]}}
            },
        },
        "Operation": {
            "Bad": {"Type": "Ticket::TicketGet", "MappingInbound": {"Type": "NoSuchMapper"}}
        },
    }
}

PLAIN_CFG: dict[str, Any] = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {"Sess": {"Route": "/Sess", "RequestMethod": ["POST"]}}
            },
        },
        "Operation": {"Sess": {"Type": "Session::SessionCreate"}},
    }
}

SOAP_CFG: dict[str, Any] = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::SOAP",
            "Config": {"NameSpace": "http://example.org/Connector/"},
        },
        "Operation": {
            "Login": {
                "Type": "Session::SessionCreate",
                "MappingOutbound": _simple({"SessionID": "Token"}),
            }
        },
    }
}

_WEBSERVICES = {
    MAP_ID: ("MapREST", MAP_CFG),
    BROKEN_ID: ("BrokenREST", BROKEN_CFG),
    SOAP_ID: ("MapSOAP", SOAP_CFG),
    PLAIN_ID: ("PlainREST", PLAIN_CFG),
}

_TICKET_SCOPE = f"(SELECT id FROM ticket WHERE queue_id = {QUEUE_ID})"


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        (
            "DELETE FROM article_data_mime_attachment WHERE article_id IN "
            f"(SELECT id FROM article WHERE ticket_id IN {_TICKET_SCOPE})",
            {},
        ),
        (
            "DELETE FROM article_data_mime WHERE article_id IN "
            f"(SELECT id FROM article WHERE ticket_id IN {_TICKET_SCOPE})",
            {},
        ),
        (f"DELETE FROM article WHERE ticket_id IN {_TICKET_SCOPE}", {}),
        (f"DELETE FROM ticket_history WHERE ticket_id IN {_TICKET_SCOPE}", {}),
        (f"DELETE FROM ticket WHERE queue_id = {QUEUE_ID}", {}),
        ("DELETE FROM gi_webservice_config WHERE id BETWEEN 8923 AND 8926", {}),
        (f"DELETE FROM queue WHERE id = {QUEUE_ID}", {}),
        (f"DELETE FROM group_user WHERE group_id = {GROUP_ID}", {}),
        (f"DELETE FROM permission_groups WHERE id = {GROUP_ID}", {}),
        (f"DELETE FROM users WHERE id = {AGENT_ID}", {}),
    )


_SEEDED: list[str] = []
_COUNTER_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_number_counter")
_SNAPSHOTS: dict[str, dict[str, int]] = {}


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        _cleanup(sync_url)
        delete_rows_above(sync_url, _SNAPSHOTS[sync_url])
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    _cleanup(sync_url)
    _SNAPSHOTS[sync_url] = snapshot_max_ids(sync_url, _COUNTER_TABLES)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Ws', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "login": LOGIN, "pw": hash_password(PASSWORD), "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'wsmap-group', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP_ID, "t": NOW},
        )
        for perm in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :perm, :t, 1, :t, 1)"
                ),
                {"u": AGENT_ID, "g": GROUP_ID, "perm": perm, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'WsMapQueue', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE_ID, "g": GROUP_ID, "t": NOW},
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


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


async def _client_for(sync_url: str) -> Any:
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
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


def _ticket_number(sync_url: str, ticket_id: int) -> str:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        number = conn.execute(
            text("SELECT tn FROM ticket WHERE id = :id"), {"id": ticket_id}
        ).scalar_one()
    engine.dispose()
    return str(number)


_TICKET = {"Title": "Mapped REST ticket", "QueueID": QUEUE_ID, "StateID": 1, "PriorityID": 3}


async def test_named_operation_route_with_mapping(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        for prefix in ("/znuny-compat/Webservice/MapREST", f"/znuny-compat/WebserviceID/{MAP_ID}"):
            made = await client.post(
                f"{prefix}/Make", json={"Agent": LOGIN, "Secret": PASSWORD, "Ticket": _TICKET}
            )
            assert made.status_code == 200, made.text
            ticket_id = int(made.json()["TicketID"])
            fetched = await client.get(
                f"{prefix}/Fetch/{ticket_id}", params={"Agent": LOGIN, "Secret": PASSWORD}
            )
            assert fetched.status_code == 200, fetched.text
            assert fetched.json() == {"Number": _ticket_number(sync_url, ticket_id)}
    await engine.dispose()


async def test_unknown_webservice_404_and_unmatched_route_500(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        unknown = await client.get("/znuny-compat/Webservice/NoSuchService/Make")
        assert unknown.status_code == 404
        assert unknown.json()["faultcode"] == "Server"
        assert "NoSuchService" in unknown.json()["faultstring"]

        unknown_id = await client.get("/znuny-compat/WebserviceID/8939/Make")
        assert unknown_id.status_code == 404

        unmatched = await client.get("/znuny-compat/Webservice/MapREST/Nothing/Here")
        assert unmatched.status_code == 500
        assert unmatched.headers["content-type"].startswith("text/plain")
        assert unmatched.text == (
            "HTTP::REST Error while determine Operation for request URI '/Nothing/Here'."
        )

        wrong_method = await client.get("/znuny-compat/Webservice/MapREST/Make")
        assert wrong_method.status_code == 500
        assert wrong_method.headers["content-type"].startswith("text/plain")
    await engine.dispose()


async def test_percent_escaped_slash_stays_in_route_variable(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        resp = await client.get(
            "/znuny-compat/Webservice/MapREST/Fetch/a%2Fb", params={"Agent": LOGIN}
        )
        # Matches Fetch with Id "a/b" (not a route miss); TicketGet then rejects the bad id.
        assert not resp.text.startswith("HTTP::REST Error")
    await engine.dispose()


async def test_unknown_mapping_type_is_fault_others_still_work(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        broken = await client.get("/znuny-compat/Webservice/BrokenREST/Bad")
        assert broken.status_code == 500
        body = broken.json()
        assert body["faultcode"] == "Server"
        assert "NoSuchMapper" in body["faultstring"]

        ok = await client.post(
            "/znuny-compat/Webservice/PlainREST/Sess",
            json={"UserLogin": LOGIN, "Password": PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["SessionID"]
    await engine.dispose()


async def test_soap_webservice_uses_operation_name_and_mapping(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    envelope = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soapenv:Body><Login>"
        f"<UserLogin>{LOGIN}</UserLogin><Password>{PASSWORD}</Password>"
        "</Login></soapenv:Body></soapenv:Envelope>"
    ).encode()
    async with client:
        resp = await client.post(
            "/znuny-compat/Webservice/MapSOAP",
            content=envelope,
            headers={"Content-Type": "text/xml"},
        )
        assert resp.status_code == 200, resp.text
        assert "<LoginResponse" in resp.text
        assert 'xmlns="http://example.org/Connector/"' in resp.text
        assert "<Token>" in resp.text
        assert "<SessionID>" not in resp.text

        by_id = await client.post(
            f"/znuny-compat/WebserviceID/{SOAP_ID}",
            content=envelope,
            headers={"Content-Type": "text/xml"},
        )
        assert by_id.status_code == 200 and "<Token>" in by_id.text

        not_post = await client.get("/znuny-compat/Webservice/MapSOAP")
        assert not_post.status_code == 404

        unknown_op = await client.post(
            "/znuny-compat/Webservice/MapSOAP",
            content=envelope.replace(b"Login>", b"Logout>"),
            headers={"Content-Type": "text/xml"},
        )
        assert unknown_op.status_code == 500
        assert "<Fault>" in unknown_op.text
        assert "not registered" in unknown_op.text
    await engine.dispose()


async def test_canonical_routes_unchanged(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        # Body wins over query on the canonical routes (no webservice, no mapping).
        resp = await client.post(
            "/znuny-compat/Session",
            params={"UserLogin": "someone.else", "Password": "wrong"},
            json={"UserLogin": LOGIN, "Password": PASSWORD},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["SessionID"]
    await engine.dispose()


async def test_rest_json_sorted_keys(sync_url: str) -> None:
    client, engine = await _client_for(sync_url)
    async with client:
        resp = await client.post(
            "/znuny-compat/Webservice/PlainREST/Sess",
            json={"UserLogin": LOGIN, "Password": PASSWORD},
        )
        assert resp.status_code == 200
        raw = resp.text
        decoded = json.loads(raw)
        assert raw == json.dumps(decoded, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        assert resp.headers["content-type"] == "application/json; charset=utf-8"
    await engine.dispose()


async def test_admin_reload_requires_admin_and_refreshes(sync_url: str) -> None:
    from tiqora.api.v1.admin.deps import get_admin_user

    client, engine = await _client_for(sync_url)
    async with client:
        denied = await client.post("/znuny-compat/admin/reload")
        assert denied.status_code in (401, 403)

        class _Admin:
            login = "root@localhost"

        client._transport.app.dependency_overrides[get_admin_user] = lambda: _Admin()  # type: ignore[attr-defined]
        ok = await client.post("/znuny-compat/admin/reload")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert {"MapREST", "BrokenREST", "MapSOAP", "PlainREST"} <= set(body["webservices"])
        assert body["webservices_loaded"] == len(body["webservices"])
    await engine.dispose()
