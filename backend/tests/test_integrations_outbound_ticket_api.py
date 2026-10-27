"""DB integration tests for ``POST /api/v1/integrations/outbound-ticket``.

Dedicated id block 94300-94399 (see ``test_integrations_customer_tickets_api.py``
for the convention). The agent has ``create``/``note``/``rw`` on queue A only;
queue B has no permission for the agent. Mail goes through a capturing sender
injected via ``TicketWriteService(mail_sender=...)``; the outbound SMTP
resolution is monkeypatched.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from email.message import EmailMessage
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.ai.models  # noqa: F401  (registers tiqora_ai_* tables on TiqoraBase)
from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.channels.email.outbound_reply import OutboundMailError
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain import mail_outbound
from tiqora.domain.api_key_scopes import path_to_area, scopes_allow
from tiqora.domain.ticket_write_service import TicketWriteService
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 1, 12, 0, 0)

_UID_AGENT = 94301
_LOGIN_AGENT = "outboundticket.api.agent"
_GROUP_A = 94330
_GROUP_B = 94331
_QUEUE_A = 94300
_QUEUE_B = 94301
_QUEUE_A_NAME = "OutboundQ-94300"
_QUEUE_B_NAME = "OutboundQ-94301"
_URL = "/api/v1/integrations/outbound-ticket"
_TITLE = "Ihre Zugangsdaten 94300"
_TO = "mentor@example.org"
_BODY = "Login: mentor1\nPasswort: Geheim-94300"


def _mysql_async(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _purge_tickets(conn: Any) -> None:
    """Delete every ticket in the two test queues plus all rows hanging off them."""
    ids = [
        int(r[0])
        for r in conn.execute(
            text("SELECT id FROM ticket WHERE queue_id IN (:a, :b)"),
            {"a": _QUEUE_A, "b": _QUEUE_B},
        )
    ]
    if not ids:
        return
    from sqlalchemy import bindparam

    def _del(sql: str) -> None:
        conn.execute(text(sql).bindparams(bindparam("ids", expanding=True)), {"ids": ids})

    articles = "(SELECT id FROM article WHERE ticket_id IN :ids)"
    for table, col in conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = DATABASE() AND column_name = 'article_id'"
            " AND table_name <> 'article'"
        )
    ).all():
        _del(f"DELETE FROM `{table}` WHERE `{col}` IN {articles}")
    for table in conn.execute(
        text(
            "SELECT table_name FROM information_schema.columns"
            " WHERE table_schema = DATABASE() AND column_name = 'ticket_id'"
            " AND table_name <> 'article'"
        )
    ).scalars():
        _del(f"DELETE FROM `{table}` WHERE ticket_id IN :ids")
    _del("DELETE FROM article WHERE ticket_id IN :ids")
    _del("DELETE FROM ticket WHERE id IN :ids")


def _cleanup(conn: Any) -> None:
    _purge_tickets(conn)
    conn.execute(
        text("DELETE FROM group_user WHERE user_id = :u OR group_id IN (:a, :b)"),
        {"u": _UID_AGENT, "a": _GROUP_A, "b": _GROUP_B},
    )
    conn.execute(text("DELETE FROM queue WHERE id IN (:a, :b)"), {"a": _QUEUE_A, "b": _QUEUE_B})
    conn.execute(
        text("DELETE FROM permission_groups WHERE id IN (:a, :b)"), {"a": _GROUP_A, "b": _GROUP_B}
    )
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_AGENT})


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Netadmin', 'Bot', 1, :t, 1, :t, 1)"
            ),
            {"id": _UID_AGENT, "login": _LOGIN_AGENT, "t": NOW},
        )
        for gid in (_GROUP_A, _GROUP_B):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "name": f"outbound-{gid}", "t": NOW},
            )
        for key in ("create", "note", "rw"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :key, :t, 1, :t, 1)"
                ),
                {"uid": _UID_AGENT, "gid": _GROUP_A, "key": key, "t": NOW},
            )
        for qid, name, gid in (
            (_QUEUE_A, _QUEUE_A_NAME, _GROUP_A),
            (_QUEUE_B, _QUEUE_B_NAME, _GROUP_B),
        ):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "name": name, "gid": gid, "t": NOW},
            )
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Generator[str, None, None]:
    snapshot = snapshot_max_ids(mariadb_znuny_url)
    _seed(mariadb_znuny_url)
    yield mariadb_znuny_url
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()
    # The endpoint commits mail-log rows and ticket numbers outside our ids.
    delete_rows_above(mariadb_znuny_url, snapshot)


class _FailingSender:
    last_smtp_code = None
    last_smtp_detail = None

    async def send(self, message: EmailMessage, *, envelope_from: str | None = None) -> None:
        raise OutboundMailError("relay refused")


def _smtp(enabled: bool) -> Any:
    async def _resolve(session: Any, settings: Any = None) -> Any:
        return mail_outbound.ResolvedOutboundSmtp(
            enabled=enabled,
            host="smtp.example.org",
            port=25,
            security="none",
            auth_type="none",
            auth_user="",
            auth_password="",
            from_default="",
            timeout_seconds=5,
            source="db",
        )

    return _resolve


async def _post(
    sync_url: str,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    *,
    sender: Any | None = None,
    smtp_enabled: bool = True,
) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.v1 import integrations
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mail_sender = sender if sender is not None else CapturingMailSender()

    def _write_service(session: Any, settings: Any) -> TicketWriteService:
        return TicketWriteService(session, factory, SysConfig(session), mail_sender=mail_sender)

    monkeypatch.setattr(integrations, "_write_service", _write_service)
    monkeypatch.setattr(mail_outbound, "resolve_outbound_smtp", _smtp(smtp_enabled))

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=_UID_AGENT,
        login=_LOGIN_AGENT,
        first_name="Netadmin",
        last_name="Bot",
        auth_method="session",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(_URL, json=payload)
    await engine.dispose()
    return resp


def _payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "queue": _QUEUE_A_NAME,
        "to_address": _TO,
        "subject": _TITLE,
        "body": _BODY,
    }
    base.update(over)
    return base


def _rows(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    engine.dispose()
    return rows


def _tickets_with_title(sync_url: str) -> list[Any]:
    return _rows(sync_url, "SELECT id FROM ticket WHERE title = :t", t=_TITLE)


@pytest.mark.asyncio
async def test_creates_closed_ticket_with_sent_email(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = CapturingMailSender()
    resp = await _post(seeded, monkeypatch, _payload(), sender=sender)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    tid = out["ticket_id"]
    assert out["ticket_number"]

    ticket = _rows(
        seeded,
        "SELECT t.queue_id, t.title, t.tn, st.name FROM ticket t"
        " JOIN ticket_state s ON s.id = t.ticket_state_id"
        " JOIN ticket_state_type st ON st.id = s.type_id WHERE t.id = :id",
        id=tid,
    )
    assert len(ticket) == 1
    assert ticket[0][0] == _QUEUE_A
    assert ticket[0][1] == _TITLE
    assert ticket[0][2] == out["ticket_number"]
    assert ticket[0][3] == "closed"

    arts = _rows(
        seeded,
        "SELECT a.id, a.is_visible_for_customer, c.name, m.a_to FROM article a"
        " JOIN communication_channel c ON c.id = a.communication_channel_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.ticket_id = :id",
        id=tid,
    )
    assert len(arts) == 1
    assert arts[0][0] == out["article_id"]
    assert arts[0][1] == 1
    assert arts[0][2] == "Email"
    assert _TO in arts[0][3]

    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert _TO in (msg["To"] or "")
    body = msg.get_body(("plain",))
    assert body is not None
    assert "Geheim-94300" in body.get_content()

    paused = _rows(
        seeded, "SELECT ai_paused_at FROM tiqora_ai_ticket_state WHERE ticket_id = :id", id=tid
    )
    assert len(paused) == 1 and paused[0][0] is not None
    # set_ai_paused only: no extra "KI-Automatik pausiert" internal note.
    notes = _rows(
        seeded,
        "SELECT 1 FROM article_data_mime m JOIN article a ON a.id = m.article_id"
        " WHERE a.ticket_id = :id AND m.a_subject LIKE '%%KI-Automatik pausiert%%'",
        id=tid,
    )
    assert notes == []


@pytest.mark.asyncio
async def test_no_agent_notification_event(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _post(seeded, monkeypatch, _payload())
    assert resp.status_code == 201, resp.text
    tid = resp.json()["ticket_id"]
    events = _rows(
        seeded,
        "SELECT event_type FROM tiqora_event_outbox WHERE ticket_id = :id",
        id=tid,
    )
    types = {r[0] for r in events}
    assert "ArticleCreate" in types  # the article event still exists
    # No article notification for the agents. (Closing unlocks the ticket and
    # emits NotificationLockTimeout, which skips the acting owner: nobody.)
    assert not types & {"NotificationNewTicket", "NotificationAddNote", "NotificationFollowUp"}


@pytest.mark.asyncio
async def test_close_false_leaves_ticket_open(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _post(seeded, monkeypatch, _payload(close=False))
    assert resp.status_code == 201, resp.text
    rows = _rows(
        seeded,
        "SELECT st.name FROM ticket t JOIN ticket_state s ON s.id = t.ticket_state_id"
        " JOIN ticket_state_type st ON st.id = s.type_id WHERE t.id = :id",
        id=resp.json()["ticket_id"],
    )
    assert rows[0][0] == "new"


@pytest.mark.asyncio
async def test_priority_from_postmaster_default(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production renamed "3 normal"; the configured default must win over the Znuny name.
    async def _high(self: SysConfig) -> str:
        return "4 high"

    monkeypatch.setattr(SysConfig, "postmaster_default_priority", _high)
    resp = await _post(seeded, monkeypatch, _payload())
    assert resp.status_code == 201, resp.text
    rows = _rows(
        seeded,
        "SELECT p.name FROM ticket t JOIN ticket_priority p ON p.id = t.ticket_priority_id"
        " WHERE t.id = :id",
        id=resp.json()["ticket_id"],
    )
    assert rows[0][0] == "4 high"


@pytest.mark.asyncio
async def test_unknown_queue_404(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _post(seeded, monkeypatch, _payload(queue="Gibt-es-nicht-94300"))
    assert resp.status_code == 404, resp.text
    assert _tickets_with_title(seeded) == []


@pytest.mark.asyncio
async def test_queue_without_create_403(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sender = CapturingMailSender()
    resp = await _post(seeded, monkeypatch, _payload(queue=_QUEUE_B_NAME), sender=sender)
    assert resp.status_code == 403, resp.text
    assert _tickets_with_title(seeded) == []
    assert sender.sent == []


@pytest.mark.asyncio
async def test_smtp_disabled_503_creates_nothing(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = CapturingMailSender()
    resp = await _post(seeded, monkeypatch, _payload(), sender=sender, smtp_enabled=False)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "outbound_mail_disabled"
    assert _tickets_with_title(seeded) == []
    assert sender.sent == []


@pytest.mark.asyncio
async def test_send_failure_502_creates_nothing(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = await _post(seeded, monkeypatch, _payload(), sender=_FailingSender())
    assert resp.status_code == 502, resp.text
    assert _tickets_with_title(seeded) == []
    assert (
        _rows(seeded, "SELECT 1 FROM tiqora_ai_ticket_state WHERE ai_paused_by = :u", u=_UID_AGENT)
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("to_address", ["a@b.de, c@d.de", "kein-mail", "", "a@b.de;c@d.de"])
async def test_invalid_to_address_422(
    seeded: str, monkeypatch: pytest.MonkeyPatch, to_address: str
) -> None:
    resp = await _post(seeded, monkeypatch, _payload(to_address=to_address))
    assert resp.status_code == 422, resp.text
    assert _tickets_with_title(seeded) == []


def test_readonly_key_scope_403() -> None:
    path = _URL
    assert path_to_area(path) == "tickets"
    assert not scopes_allow(frozenset({"tickets:ro"}), method="POST", path=path)
    assert not scopes_allow(frozenset({"read"}), method="POST", path=path)
    assert scopes_allow(frozenset({"tickets:rw"}), method="POST", path=path)
