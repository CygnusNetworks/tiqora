"""DB integration tests for the SOAP GenericInterface compat transport.

Hits the real HTTP surface (POST /znuny-compat/soap/{webservice}) through
FastAPI's ASGI app — unlike test_compat_operations.py (which calls the
op_* handlers directly), these tests exercise routing + the SOAP codec +
the *same* shared operation handlers end-to-end, proving SOAP and REST go
through identical domain logic (only the wire codec differs).

WSDL note: Znuny's GenericInterface provider does not auto-serve a WSDL for
the generic ticket connector either — ``scripts/test/Console/Command/Admin/
WebService/GenericTicketConnectorSOAP.wsdl`` in the Znuny source is a
hand-maintained sample, not something the server generates on request.
Tiqora matches this: point a SOAP client (e.g. zeep, soap-ui) directly at
the endpoint URL with a manually authored/adapted WSDL (or skip WSDL
entirely and build the envelope by hand, as these tests do) — there is no
``?wsdl`` route.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2026, 3, 1, 12, 0, 0)
PW_HASH = hash_password("soaptestpass")

SOAP_ENVELOPE_OPEN = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>'
)
SOAP_ENVELOPE_CLOSE = "</soapenv:Body></soapenv:Envelope>"


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _create_tiqora_tables(session: AsyncSession) -> None:
    ddls = [
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
    ]
    for ddl in ddls:
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}

    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        # Idempotent: mariadb_znuny_url is session-scoped and shared across
        # every test module in this run, so a second call from another test
        # module must not fail on duplicate seed rows.
        existing = conn.execute(text("SELECT id FROM users WHERE login = 'soap.agent'")).first()
        if existing is not None:
            return {"queue_id": 953, "state_id": 1, "priority_id": 3}

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (951, 'soap.agent', :pw, 'Soap', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"pw": PW_HASH, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (952, 'soap-compat-group', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for perm in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user"
                    " (user_id, group_id, permission_key,"
                    "  create_time, create_by, change_time, change_by)"
                    " VALUES (951, 952, :perm, :t, 1, :t, 1)"
                ),
                {"perm": perm, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue"
                " (id, name, group_id, system_address_id, salutation_id, signature_id,"
                "  follow_up_id, follow_up_lock, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (953, 'SoapCompatQueue', 952, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        ids["queue_id"] = 953
        ids["state_id"] = 1
        ids["priority_id"] = 3

    return ids


async def _client_for(mariadb_znuny_url: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings
    from tiqora.domain.auth import SessionStore

    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
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
    # TicketCreate/TicketUpdate read the session factory off app.state
    # directly (not via a FastAPI dependency) — see api/compat/router.py
    # _dispatch_operation.
    app.state.session_factory = factory

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


def _soap_body(operation: str, inner_xml: str) -> bytes:
    return (
        f"{SOAP_ENVELOPE_OPEN}<{operation}>{inner_xml}</{operation}>{SOAP_ENVELOPE_CLOSE}".encode()
    )


def _extract_text(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.DOTALL)
    return m.group(1) if m else None


@pytest.mark.asyncio
async def test_soap_session_create_returns_well_formed_response(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        body = _soap_body(
            "SessionCreate",
            "<UserLogin>soap.agent</UserLogin><Password>soaptestpass</Password>",
        )
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/xml")
    xml = resp.text
    assert "<SessionCreateResponse" in xml
    session_id = _extract_text(xml, "SessionID")
    assert session_id, f"No SessionID in response: {xml}"
    await engine.dispose()


@pytest.mark.asyncio
async def test_soap_session_create_auth_fail_is_soap_fault(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        body = _soap_body(
            "SessionCreate",
            "<UserLogin>soap.agent</UserLogin><Password>wrong</Password>",
        )
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=body,
            headers={"Content-Type": "text/xml"},
        )

    assert resp.status_code == 401, resp.text
    assert "<Fault>" in resp.text
    assert "AuthFail" in resp.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_soap_ticket_create_then_get_round_trips_through_shared_domain_logic(
    mariadb_znuny_url: str,
) -> None:
    """A ticket created via the SOAP transport must be retrievable via
    SOAP TicketGet — proving both go through the same op_ticket_create /
    op_ticket_get domain handlers as the REST transport (not a SOAP-only
    reimplementation)."""
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        await _create_tiqora_tables_via_client(engine)

        create_body = _soap_body(
            "TicketCreate",
            "<UserLogin>soap.agent</UserLogin><Password>soaptestpass</Password>"
            "<Ticket>"
            "<Title>SOAP created ticket</Title>"
            f"<QueueID>{ids['queue_id']}</QueueID>"
            f"<StateID>{ids['state_id']}</StateID>"
            f"<PriorityID>{ids['priority_id']}</PriorityID>"
            "</Ticket>",
        )
        create_resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=create_body,
            headers={"Content-Type": "text/xml"},
        )
        assert create_resp.status_code == 200, create_resp.text
        assert "<TicketCreateResponse" in create_resp.text
        ticket_id = _extract_text(create_resp.text, "TicketID")
        assert ticket_id, f"No TicketID in response: {create_resp.text}"

        get_body = _soap_body(
            "TicketGet",
            "<UserLogin>soap.agent</UserLogin><Password>soaptestpass</Password>"
            f"<TicketID>{ticket_id}</TicketID>",
        )
        get_resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=get_body,
            headers={"Content-Type": "text/xml"},
        )

    assert get_resp.status_code == 200, get_resp.text
    xml = get_resp.text
    assert "<TicketGetResponse" in xml
    assert "SOAP created ticket" in xml
    assert f"<TicketID>{ticket_id}</TicketID>" in xml
    await engine.dispose()


async def _create_tiqora_tables_via_client(engine: Any) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _create_tiqora_tables(session)


@pytest.mark.asyncio
async def test_soap_ticket_search_returns_well_formed_response(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        body = _soap_body(
            "TicketSearch",
            "<UserLogin>soap.agent</UserLogin><Password>soaptestpass</Password>"
            f"<QueueIDs>{ids['queue_id']}</QueueIDs>",
        )
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=body,
            headers={"Content-Type": "text/xml"},
        )

    assert resp.status_code == 200, resp.text
    assert "<TicketSearchResponse" in resp.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_soap_unsupported_operation_returns_fault_501(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        body = _soap_body("TicketHistoryGet", "<TicketID>1</TicketID>")
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=body,
            headers={"Content-Type": "text/xml"},
        )

    assert resp.status_code == 501
    assert "<Fault>" in resp.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_soap_malformed_envelope_returns_400_fault(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=b"<not well formed xml",
            headers={"Content-Type": "text/xml"},
        )

    assert resp.status_code == 400
    assert "<Fault>" in resp.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_soap_xxe_payload_rejected_not_resolved(mariadb_znuny_url: str) -> None:
    """A malicious SOAP client attempting XXE gets a 400 Fault, and the
    external entity is never resolved by the ASGI app."""
    _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url)

    async with client:
        xxe_body = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            b"<soapenv:Body><TicketGet><TicketID>&xxe;</TicketID></TicketGet></soapenv:Body>"
            b"</soapenv:Envelope>"
        )
        resp = await client.post(
            "/znuny-compat/soap/GenericTicketConnectorSOAP",
            content=xxe_body,
            headers={"Content-Type": "text/xml"},
        )

    # Rejected before resolution: the Fault may reference the attempted
    # SYSTEM identifier in its diagnostic text (defusedxml's own error
    # message), but actual /etc/passwd *content* must never appear.
    assert resp.status_code == 400
    assert "<Fault>" in resp.text
    assert "root:" not in resp.text
    await engine.dispose()
