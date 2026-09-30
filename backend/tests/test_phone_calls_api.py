"""DB integration tests: POST /tickets/{id}/phone-calls (Znuny AgentTicketPhone
Inbound/Outbound parity).

One request logs a call on an existing ticket: a ``Phone`` article with the
direction's sender/history type, the booked time bound to that article, the
next state (+ pending time), ticket dynamic fields and attachments -- all or
nothing. Outbound calls take the ticket lock (RequiredLock) and fail with 409
when another agent holds it.

Uses the 861xx id range; the module deletes every row it creates.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

QUEUE_ID = 86100
GROUP_ID = 86130
TICKET_ID = 86170
AGENT_RW = 86101
AGENT_NOTE = 86102  # ro + note only
AGENT_OTHER = 86103
DF_ID = 86190
CUSTOMER_LOGIN = "phonecall.alice"

STATE_OPEN = 4
STATE_CLOSED_SUCCESSFUL = 2
STATE_PENDING_REMINDER = 6


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    arts = f"(SELECT id FROM article WHERE ticket_id = {TICKET_ID})"
    users = f"({AGENT_RW}, {AGENT_NOTE}, {AGENT_OTHER})"
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime_plain WHERE article_id IN {arts}",
        f"DELETE FROM article_flag WHERE article_id IN {arts}",
        f"DELETE FROM time_accounting WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM article WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM dynamic_field_value WHERE field_id = {DF_ID}",
        f"DELETE FROM dynamic_field WHERE id = {DF_ID}",
        f"DELETE FROM ticket_flag WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket_index WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket_lock_index WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket WHERE id = {TICKET_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE user_id IN {users} OR group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id IN {users}",
        f"DELETE FROM customer_user WHERE login = '{CUSTOMER_LOGIN}'",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str, *, lock_id: int = 1, owner_id: int = AGENT_RW) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:rw, 'phonecall.rw', 'x', 'Paula', 'Phone', 1, :t, 1, :t, 1),"
                " (:note, 'phonecall.note', 'x', 'Nora', 'Note', 1, :t, 1, :t, 1),"
                " (:other, 'phonecall.other', 'x', 'Otto', 'Other', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW, "rw": AGENT_RW, "note": AGENT_NOTE, "other": AGENT_OTHER},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'phone-call-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        grants = [(AGENT_RW, "rw"), (AGENT_OTHER, "rw"), (AGENT_NOTE, "ro"), (AGENT_NOTE, "note")]
        for uid, key in grants:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": uid, "gid": GROUP_ID, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'PhoneCallQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " phone, pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:login, 'alice.caller@example.com', 'CUSTPC', 'Alice', 'Caller',"
                " '+49 30 1234567', 'x', 1, :t, 1, :t, 1)"
            ),
            {"login": CUSTOMER_LOGIN, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 0, 'PhoneCallTopic', 'Topic', 1, 'Text', 'Ticket', :cfg, 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "cfg": "---\nDefaultValue: ''\n", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20240601861701', 'Phone call ticket', :qid, :lock, 1,"
                " :owner, 1, 3, :open, 'CUSTPC', :cust,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "tid": TICKET_ID,
                "qid": QUEUE_ID,
                "lock": lock_id,
                "owner": owner_id,
                "open": STATE_OPEN,
                "cust": CUSTOMER_LOGIN,
                "t": NOW,
            },
        )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = list(conn.execute(text(sql), params).all())
    engine.dispose()
    return rows


def _ticket(sync_url: str) -> dict[str, Any]:
    row = _query(
        sync_url,
        "SELECT ticket_state_id, until_time, ticket_lock_id, user_id FROM ticket WHERE id = :t",
        t=TICKET_ID,
    )[0]
    return {"state_id": row[0], "until_time": row[1], "lock_id": row[2], "owner_id": row[3]}


def _article_count(sync_url: str) -> int:
    return int(
        _query(sync_url, "SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=TICKET_ID)[0][0]
    )


@pytest.fixture
async def db(mariadb_znuny_url: str) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield mariadb_znuny_url, factory
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=f"agent{user_id}",
        first_name="Paula",
        last_name="Phone",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _call(**extra: Any) -> dict[str, Any]:
    return {"direction": "inbound", "subject": "Anruf von Alice", "body": "Drucker defekt", **extra}


URL = f"/api/v1/tickets/{TICKET_ID}/phone-calls"


@pytest.mark.asyncio
async def test_inbound_call_logs_customer_article_with_time_and_fields(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    payload = _call(
        time_unit=7,
        dynamic_fields={"PhoneCallTopic": ["Drucker"]},
        attachments=[
            {
                "filename": "notiz.txt",
                "content_type": "text/plain",
                "content_base64": base64.b64encode(b"hallo").decode(),
            }
        ],
    )
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(URL, json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ticket_id"] == TICKET_ID
    assert data["locked"] is False  # inbound: no RequiredLock
    article_id = data["article_id"]

    art = _query(
        url,
        "SELECT st.name, cc.name, a.is_visible_for_customer, m.a_from, m.a_subject, m.a_body"
        " FROM article a JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
        a=article_id,
    )[0]
    assert art[0] == "customer"
    assert art[1] == "Phone"
    assert int(art[2]) == 1
    assert art[3] == '"Alice Caller" <alice.caller@example.com>'
    assert (art[4], art[5]) == ("Anruf von Alice", "Drucker defekt")

    history = [
        r[0]
        for r in _query(
            url,
            "SELECT ht.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
            " WHERE h.article_id = :a",
            a=article_id,
        )
    ]
    assert "PhoneCallCustomer" in history

    ta = _query(
        url,
        "SELECT id, article_id, time_unit FROM time_accounting WHERE ticket_id = :t",
        t=TICKET_ID,
    )
    assert len(ta) == 1
    assert ta[0][1] == article_id
    assert float(ta[0][2]) == 7.0
    assert data["time_accounting_id"] == ta[0][0]

    # Inbound default "open" on an open ticket: no StateUpdate (Znuny no-op).
    async with _client(factory, AGENT_RW) as client:
        again = await client.post(URL, json=_call(state_id=STATE_OPEN))
    assert again.status_code == 201, again.text
    updates = _query(
        url,
        "SELECT COUNT(*) FROM ticket_history h JOIN ticket_history_type ht"
        " ON ht.id = h.history_type_id WHERE h.ticket_id = :t AND ht.name = 'StateUpdate'",
        t=TICKET_ID,
    )
    assert updates[0][0] == 0

    df = _query(
        url,
        "SELECT value_text FROM dynamic_field_value WHERE field_id = :f AND object_id = :t",
        f=DF_ID,
        t=TICKET_ID,
    )
    assert [r[0] for r in df] == ["Drucker"]

    att = _query(
        url,
        "SELECT filename, content FROM article_data_mime_attachment WHERE article_id = :a",
        a=article_id,
    )
    assert [(r[0], bytes(r[1])) for r in att] == [("notiz.txt", b"hallo")]
    assert _ticket(url)["state_id"] == STATE_OPEN


@pytest.mark.asyncio
async def test_outbound_call_locks_and_closes(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, owner_id=AGENT_OTHER)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            URL,
            json=_call(direction="outbound", is_visible_for_customer=False, state_id=STATE_OPEN),
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["locked"] is True
    ticket = _ticket(url)
    assert ticket["lock_id"] == 2
    assert ticket["owner_id"] == AGENT_RW  # RequiredLock: the agent becomes owner

    art = _query(
        url,
        "SELECT st.name, a.is_visible_for_customer FROM article a"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id WHERE a.id = :a",
        a=resp.json()["article_id"],
    )[0]
    assert art[0] == "agent"
    assert int(art[1]) == 0
    history = [
        r[0]
        for r in _query(
            url,
            "SELECT ht.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
            " WHERE h.ticket_id = :t ORDER BY h.id",
            t=TICKET_ID,
        )
    ]
    assert "PhoneCallAgent" in history
    assert history.index("Lock") < history.index("PhoneCallAgent")

    # A second outbound call closing the ticket unlocks it again.
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            URL, json=_call(direction="outbound", state_id=STATE_CLOSED_SUCCESSFUL)
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["locked"] is False  # already held by the caller
    ticket = _ticket(url)
    assert ticket["state_id"] == STATE_CLOSED_SUCCESSFUL
    assert ticket["lock_id"] == 1


@pytest.mark.asyncio
async def test_outbound_call_on_foreign_lock_is_409_and_stores_nothing(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url, lock_id=2, owner_id=AGENT_OTHER)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(URL, json=_call(direction="outbound", time_unit=5))
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["locked_by_name"] == "Otto Other"
    assert _article_count(url) == 0
    assert (
        _query(url, "SELECT COUNT(*) FROM time_accounting WHERE ticket_id = :t", t=TICKET_ID)[0][0]
        == 0
    )


@pytest.mark.asyncio
async def test_pending_state_needs_pending_time(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_RW) as client:
        missing = await client.post(URL, json=_call(state_id=STATE_PENDING_REMINDER))
        assert missing.status_code == 422, missing.text
        assert _article_count(url) == 0
        pending = datetime(2030, 1, 2, 9, 0, 0)
        ok = await client.post(
            URL, json=_call(state_id=STATE_PENDING_REMINDER, pending_time=pending.isoformat())
        )
    assert ok.status_code == 201, ok.text
    ticket = _ticket(url)
    assert ticket["state_id"] == STATE_PENDING_REMINDER
    assert ticket["until_time"] == int(pending.timestamp())


@pytest.mark.asyncio
async def test_unknown_state_is_422(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(URL, json=_call(state_id=987654))
    assert resp.status_code == 422, resp.text
    assert _article_count(url) == 0


@pytest.mark.asyncio
async def test_note_only_agent_is_403(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_NOTE) as client:
        resp = await client.post(URL, json=_call())
    assert resp.status_code == 403, resp.text
    assert _article_count(url) == 0


@pytest.mark.asyncio
async def test_unknown_ticket_is_404(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post("/api/v1/tickets/999999991/phone-calls", json=_call())
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_invalid_direction_is_422(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(URL, json=_call(direction="sideways"))
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_phone_screen_without_configured_dynamic_fields_offers_none(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """Nothing enabled in ``###DynamicField`` → no fields (no "all fields" fallback)."""
    url, factory = db
    _seed(url)
    async with _client(factory, AGENT_RW) as client:
        for screen in ("AgentTicketPhone", "AgentTicketPhoneInbound", "AgentTicketPhoneOutbound"):
            resp = await client.get("/api/v1/reference/dynamic-fields", params={"screen": screen})
            assert resp.status_code == 200, resp.text
            assert resp.json() == []


@pytest.mark.asyncio
async def test_phone_screen_dynamic_fields_report_optional_and_required(
    db: tuple[str, async_sessionmaker[AsyncSession]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.znuny.sysconfig import SysConfig

    url, factory = db
    _seed(url)
    original = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "Ticket::Frontend::AgentTicketPhone###DynamicField":
            return {"PhoneCallTopic": 1, "ProcessManagementProcessID": 1}
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.get(
            "/api/v1/reference/dynamic-fields", params={"screen": "AgentTicketPhone"}
        )
    assert resp.status_code == 200, resp.text
    by_name = {f["name"]: f for f in resp.json()}
    assert by_name["PhoneCallTopic"] == {
        "name": "PhoneCallTopic",
        "label": "Topic",
        "field_type": "Text",
        "required": False,
        "possible_values": None,
    }
    # Znuny's internal process-management fields are never offered.
    assert not any(n.startswith("ProcessManagement") for n in by_name)


@pytest.mark.asyncio
async def test_phone_screen_dynamic_fields_follow_the_screen_config(
    db: tuple[str, async_sessionmaker[AsyncSession]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.znuny.sysconfig import SysConfig

    url, factory = db
    _seed(url)
    original = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "Ticket::Frontend::AgentTicketPhoneOutbound###DynamicField":
            return {"PhoneCallTopic": "2", "Other": "0"}
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.get(
            "/api/v1/reference/dynamic-fields", params={"screen": "AgentTicketPhoneOutbound"}
        )
        bad = await client.get("/api/v1/reference/dynamic-fields", params={"screen": "Nope"})
    assert resp.status_code == 200, resp.text
    assert [(f["name"], f["required"]) for f in resp.json()] == [("PhoneCallTopic", True)]
    assert bad.status_code == 422
