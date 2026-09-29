"""DB tests: POST /tickets with ``phone_call`` (Znuny AgentTicketPhone parity).

The phone form creates the ticket and its first Phone article in one request:
pending time on the initial state, responsible, time units booked on the
article, and -- for an inbound call with ``send_auto_response`` -- the queue's
"auto reply" to the customer (AutoResponseForWebTickets), with the email
pipeline's loop protection.

Uses the 863xx id range; the module deletes every row it creates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.db.tiqora.base import TiqoraBase

from ._row_cleanup import delete_rows_above, snapshot_max_ids

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
QUEUE_ID = 86300
GROUP_ID = 86330
AGENT = 86301
RESPONSIBLE = 86302
AUTO_RESPONSE_ID = 86390
CUSTOMER_LOGIN = "newphone.kim"
CUSTOMER_EMAIL = "kim.newphone@example.com"

STATE_OPEN = 4
STATE_PENDING_REMINDER = 6
STATE_CLOSED_SUCCESSFUL = 2


def _to_async_url(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    tickets = f"(SELECT id FROM ticket WHERE queue_id = {QUEUE_ID})"
    arts = f"(SELECT a.id FROM article a WHERE a.ticket_id IN {tickets})"
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id IN {tickets}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
        f"DELETE FROM article_flag WHERE article_id IN {arts}",
        f"DELETE FROM time_accounting WHERE ticket_id IN {tickets}",
        f"DELETE FROM article WHERE ticket_id IN {tickets}",
        f"DELETE FROM ticket_flag WHERE ticket_id IN {tickets}",
        f"DELETE FROM ticket_index WHERE ticket_id IN {tickets}",
        f"DELETE FROM ticket_lock_index WHERE ticket_id IN {tickets}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id IN {tickets}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id IN {tickets}",
        f"DELETE FROM tiqora_mail_log WHERE ticket_id IN {tickets}",
        f"DELETE FROM ticket WHERE queue_id = {QUEUE_ID}",
        f"DELETE FROM ticket_loop_protection WHERE sent_to = '{CUSTOMER_EMAIL}'",
        f"DELETE FROM queue_auto_response WHERE queue_id = {QUEUE_ID}",
        f"DELETE FROM auto_response WHERE id = {AUTO_RESPONSE_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id IN ({AGENT}, {RESPONSIBLE})",
        f"DELETE FROM customer_user WHERE login = '{CUSTOMER_LOGIN}'",
    ):
        conn.execute(text(sql))


@pytest.fixture(autouse=True, scope="module")
def _module_rows(mariadb_znuny_url: str) -> Iterator[None]:
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    snapshot = snapshot_max_ids(mariadb_znuny_url)
    yield
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()
    delete_rows_above(mariadb_znuny_url, snapshot)


def _seed(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:a, 'newphone.agent', 'x', 'Nina', 'Phone', 1, :t, 1, :t, 1),"
                " (:r, 'newphone.resp', 'x', 'Rolf', 'Resp', 1, :t, 1, :t, 1)"
            ),
            {"a": AGENT, "r": RESPONSIBLE, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:g, 'newphone-grp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'NewPhoneQueue', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_ID, "g": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, :e, 'NEWPHONECO', 'Kim', 'Kunde', 'x', 1, :t, 1, :t, 1)"
            ),
            {"l": CUSTOMER_LOGIN, "e": CUSTOMER_EMAIL, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO auto_response (id, type_id, system_address_id, name, text0, text1,"
                " content_type, comments, valid_id, create_by, create_time, change_by,"
                " change_time)"
                " VALUES (:id, 1, 1, 'newphone auto reply', 'Eingang: <OTRS_CUSTOMER_SUBJECT>',"
                " 'Wir haben Ihren Anruf erfasst.', 'text/plain', 'test', 1, 1, :t, 1, :t)"
            ),
            {"id": AUTO_RESPONSE_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue_auto_response (queue_id, auto_response_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, :ar, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_ID, "ar": AUTO_RESPONSE_ID, "t": NOW},
        )
    engine.dispose()


def _rows(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(url)
    with engine.begin() as conn:
        out = list(conn.execute(text(sql), params).all())
    engine.dispose()
    return out


@pytest.fixture
async def env(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, Any, CapturingMailSender]]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.v1 import tickets as tickets_api
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser
    from tiqora.domain.ticket_write_service import TicketWriteService
    from tiqora.znuny.sysconfig import SysConfig

    _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    monkeypatch.setattr(
        tickets_api,
        "_write_service",
        lambda session, settings: TicketWriteService(
            session, factory, SysConfig(session), mail_sender=sender
        ),
    )

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=AGENT,
        login="newphone.agent",
        first_name="Nina",
        last_name="Phone",
        auth_method="session",
    )
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield mariadb_znuny_url, c, sender
    finally:
        await engine.dispose()


def _payload(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Drucker streikt",
        "queue_id": QUEUE_ID,
        "state_id": STATE_OPEN,
        "priority_id": 3,
        "owner_id": AGENT,
        "customer_id": "NEWPHONECO",
        "customer_user_id": CUSTOMER_LOGIN,
        "phone_call": {"direction": "inbound", "body": "Kunde meldet Papierstau.", "time_unit": 4},
        "send_auto_response": True,
    }
    body.update(extra)
    return body


@pytest.mark.asyncio
async def test_inbound_phone_ticket_with_auto_reply(env: Any) -> None:
    url, client, sender = env
    pending = datetime(2030, 3, 4, 9, 0, 0)
    resp = await client.post(
        "/api/v1/tickets",
        json=_payload(
            state_id=STATE_PENDING_REMINDER,
            pending_time=pending.isoformat(),
            responsible_id=RESPONSIBLE,
        ),
    )
    assert resp.status_code == 201, resp.text
    tid = resp.json()["ticket_id"]

    ticket = _rows(
        url,
        "SELECT ticket_state_id, until_time, responsible_user_id FROM ticket WHERE id = :t",
        t=tid,
    )[0]
    assert (ticket[0], ticket[1], ticket[2]) == (
        STATE_PENDING_REMINDER,
        int(pending.timestamp()),
        RESPONSIBLE,
    )

    articles = _rows(
        url,
        "SELECT a.id, st.name, cc.name, m.a_from, m.a_subject FROM article a"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_data_mime m ON m.article_id = a.id"
        " WHERE a.ticket_id = :t ORDER BY a.id",
        t=tid,
    )
    first = articles[0]
    assert (first[1], first[2]) == ("customer", "Phone")
    assert first[3] == f'"Kim Kunde" <{CUSTOMER_EMAIL}>'
    assert first[4] == "Drucker streikt"  # subject defaults to the title

    ta = _rows(url, "SELECT article_id, time_unit FROM time_accounting WHERE ticket_id = :t", t=tid)
    assert [(r[0], float(r[1])) for r in ta] == [(first[0], 4.0)]

    history = [
        r[0]
        for r in _rows(
            url,
            "SELECT ht.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
            " WHERE h.ticket_id = :t ORDER BY h.id",
            t=tid,
        )
    ]
    assert "PhoneCallCustomer" in history
    assert "SendAutoReply" in history
    assert "SetPendingTime" in history

    assert len(sender.sent) == 1
    assert sender.sent[0]["To"] == CUSTOMER_EMAIL
    assert sender.sent[0]["Subject"].endswith("Eingang: Drucker streikt")


@pytest.mark.asyncio
async def test_no_auto_reply_without_flag_or_for_outbound(env: Any) -> None:
    url, client, sender = env
    no_flag = await client.post("/api/v1/tickets", json=_payload(send_auto_response=False))
    assert no_flag.status_code == 201, no_flag.text
    outbound = await client.post(
        "/api/v1/tickets",
        json=_payload(phone_call={"direction": "outbound", "body": "Rückruf erledigt."}),
    )
    assert outbound.status_code == 201, outbound.text
    assert sender.sent == []
    sender_type = _rows(
        url,
        "SELECT st.name FROM article a JOIN article_sender_type st"
        " ON st.id = a.article_sender_type_id WHERE a.ticket_id = :t",
        t=outbound.json()["ticket_id"],
    )
    assert [r[0] for r in sender_type] == ["agent"]


@pytest.mark.asyncio
async def test_auto_reply_respects_sysconfig_switch(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.znuny.sysconfig import SysConfig

    _url, client, sender = env
    original = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "AutoResponseForWebTickets":
            return 0
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    resp = await client.post("/api/v1/tickets", json=_payload())
    assert resp.status_code == 201, resp.text
    assert sender.sent == []


@pytest.mark.asyncio
async def test_auto_reply_loop_protection(env: Any) -> None:
    url, client, sender = env
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    engine = create_engine(url)
    with engine.begin() as conn:
        for _ in range(40):  # PostmasterMaxEmails default
            conn.execute(
                text("INSERT INTO ticket_loop_protection (sent_to, sent_date) VALUES (:s, :d)"),
                {"s": CUSTOMER_EMAIL, "d": today},
            )
    engine.dispose()
    resp = await client.post("/api/v1/tickets", json=_payload())
    assert resp.status_code == 201, resp.text
    assert sender.sent == []
    history = [
        r[0]
        for r in _rows(
            url,
            "SELECT ht.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id WHERE h.ticket_id = :t",
            t=resp.json()["ticket_id"],
        )
    ]
    assert "LoopProtection" in history


@pytest.mark.asyncio
async def test_pending_state_without_time_is_422(env: Any) -> None:
    url, client, _sender = env
    before = _rows(url, "SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE_ID)[0][0]
    resp = await client.post("/api/v1/tickets", json=_payload(state_id=STATE_PENDING_REMINDER))
    assert resp.status_code == 422, resp.text
    after = _rows(url, "SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE_ID)[0][0]
    assert after == before
