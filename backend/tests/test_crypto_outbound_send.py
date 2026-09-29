"""DB integration: signed/encrypted sending through the real API send paths.

Reply (``POST /tickets/{id}/articles``), forward, new email ticket and the
GenericInterface ``ArticleSend`` go through
``deliver_agent_email_reply`` → ``mime_build``; ``aiosmtplib.send`` is
replaced by a recorder, so the test sees the exact bytes that would go on the
wire. Each sent mail is read back with the *customer's* keys (inbound walk),
the stored article must hold the clear text, ``article_data_mime_plain`` the
sent raw mail and the ``TiqoraCrypto*`` flags the result. Encryption with a
recipient that has no usable key is a 422 and sends/stores nothing.

Uses the 797xx id range; everything it creates is deleted again.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._crypto_mail_fixtures import CUSTOMER, SUPPORT, CryptoWorld
from tests._row_cleanup import cleanup_module
from tiqora.config import get_settings
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.crypto.mime_walk import walk_message
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

NOW = datetime(2026, 9, 29, 12, 0, 0)
QUEUE_ID = 79700
SA_ID = 7970  # system_address.id is SMALLINT
GROUP_ID = 79730
AGENT = 79701
QUEUE_NAME = "CryptoOutboundQueue"


def _sync(url: str) -> Any:
    return create_engine(url)


def _async_url(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    tickets = [
        r[0] for r in conn.execute(text(f"SELECT id FROM ticket WHERE queue_id = {QUEUE_ID}"))
    ]
    for tid in tickets:
        arts = f"(SELECT id FROM article WHERE ticket_id = {tid})"
        for sql in (
            f"DELETE FROM ticket_history WHERE ticket_id = {tid}",
            f"DELETE FROM tiqora_mail_log WHERE ticket_id = {tid}",
            f"DELETE FROM article_data_mime_attachment WHERE article_id IN {arts}",
            f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
            f"DELETE FROM article_data_mime_plain WHERE article_id IN {arts}",
            f"DELETE FROM article_flag WHERE article_id IN {arts}",
            f"DELETE FROM article_search_index WHERE ticket_id = {tid}",
            f"DELETE FROM article WHERE ticket_id = {tid}",
            f"DELETE FROM ticket_flag WHERE ticket_id = {tid}",
            f"DELETE FROM ticket_index WHERE ticket_id = {tid}",
            f"DELETE FROM ticket_lock_index WHERE ticket_id = {tid}",
            f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {tid}",
            f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {tid}",
            f"DELETE FROM ticket WHERE id = {tid}",
        ):
            conn.execute(text(sql))
    for sql in (
        "DELETE FROM tiqora_mail_log WHERE subject LIKE 'crypto-out %'"
        " OR subject LIKE '%crypto-out %'",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM system_address WHERE id = {SA_ID}",
        f"DELETE FROM group_user WHERE user_id = {AGENT} OR group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id = {AGENT}",
    ):
        conn.execute(text(sql))


def _seed(url: str, default_sign_key: str | None) -> None:
    engine = _sync(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(text("DELETE FROM tiqora_mail_outbound"))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'crypto.outbound', 'x', 'Crypto', 'Out', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW, "uid": AGENT},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'crypto-outbound-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'rw', :t, 1, :t, 1)"
            ),
            {"uid": AGENT, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id, queue_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :addr, 'Tiqora Support', 'test', 1, 1, 1, :t, 1, :t)"
            ),
            {"id": SA_ID, "addr": SUPPORT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, default_sign_key, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, :name, :gid, :sa, 1, 1, 1, 0, :dsk, 1, :t, 1, :t, 1)"
            ),
            {
                "qid": QUEUE_ID,
                "name": QUEUE_NAME,
                "gid": GROUP_ID,
                "sa": SA_ID,
                "dsk": default_sign_key,
                "t": NOW,
            },
        )
    engine.dispose()


def _teardown(url: str) -> None:
    engine = _sync(url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _counter_cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    yield from cleanup_module(mariadb_znuny_url, tables=("ticket_number_counter",))


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    # support side knows the customer's certificate (encryption target)
    SmimeStore(w.cert_dir, w.private_dir).add_certificate(w.customer_cert.cert_pem)
    # customer side: own certificate + key, the CA as trust anchor
    cstore = SmimeStore(os.path.join(w.root, "cc"), os.path.join(w.root, "cp"))
    cstore.add_certificate(w.customer_cert.cert_pem)
    cstore.add_private_key(w.customer_cert.encrypted_key("c"), "c")
    cstore.add_certificate(w.ca.cert_pem)
    yield w
    w.cleanup()


def _customer_config(world: CryptoWorld) -> CryptoConfig:
    return CryptoConfig(
        pgp=PgpConfig(enabled=True, homedir=world.customer_home),
        smime=SmimeConfig(
            enabled=True,
            cert_path=os.path.join(world.root, "cc"),
            private_path=os.path.join(world.root, "cp"),
        ),
    )


def _znuny_id(world: CryptoWorld) -> str:
    key = PgpEngine.from_config(world.config().pgp).find_key(world.support_fp)
    assert key is not None
    return key.znuny_key_id


class Sent:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def send(self, message: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
        self.calls.append((message, kwargs))
        return {}, "250 OK"


@pytest.fixture
async def env(
    mariadb_znuny_url: str, world: CryptoWorld, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession], Sent]]:
    _seed(mariadb_znuny_url, f"PGP::Detached::{_znuny_id(world)}")
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_GNUPGHOME", world.support_home)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_CERT_DIR", world.cert_dir)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_PRIVATE_DIR", world.private_dir)
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_ENABLED", "1")
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_ENABLED", "1")
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    sent = Sent()
    monkeypatch.setattr("tiqora.channels.email.smtp.aiosmtplib.send", sent.send)
    engine = create_async_engine(_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    try:
        yield mariadb_znuny_url, factory, sent
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)
        get_settings.cache_clear()


def _client(factory: async_sessionmaker[AsyncSession]) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=AGENT, login="crypto.outbound", first_name="C", last_name="O", auth_method="session"
    )
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _rows(url: str, sql: str, **params: Any) -> list[Any]:
    engine = _sync(url)
    with engine.begin() as conn:
        rows = list(conn.execute(text(sql), params).all())
    engine.dispose()
    return rows


def _flags(url: str, article_id: int) -> dict[str, str]:
    rows = _rows(
        url,
        "SELECT article_key, article_value FROM article_flag"
        " WHERE article_id = :a AND article_key LIKE 'TiqoraCrypto%'",
        a=article_id,
    )
    return {str(k): str(v) for k, v in rows}


async def _new_ticket(client: Any, subject: str) -> int:
    resp = await client.post(
        "/api/v1/tickets",
        json={
            "title": subject,
            "queue_id": QUEUE_ID,
            "state_id": 1,
            "priority_id": 3,
            "owner_id": AGENT,
        },
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["ticket_id"])


def _email(subject: str, security: dict[str, Any] | None, *, to: str = CUSTOMER) -> dict[str, Any]:
    return {
        "sender_type": "agent",
        "channel": "email",
        "subject": subject,
        "body": "Streng vertrauliche Antwort: Ihr Zugangscode lautet 4711.",
        "to_address": f"Carla Customer <{to}>",
        "email_security": security,
    }


def _raw(sent: Sent) -> bytes:
    assert len(sent.calls) == 1
    message, kwargs = sent.calls[0]
    assert isinstance(message, bytes), "secured mail must be sent as raw bytes"
    assert CUSTOMER in kwargs["recipients"]
    return message


@pytest.mark.asyncio
async def test_crypto_options_for_ticket_and_new_ticket(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent], world: CryptoWorld
) -> None:
    _url, factory, _sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out options")
        resp = await client.get(
            f"/api/v1/tickets/{tid}/crypto-options",
            params={"to": f"Carla <{CUSTOMER}>", "cc": "nokey@example.net"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is True and body["from_address"] == SUPPORT
        assert body["default"] == {
            "backend": "pgp",
            "method": "detached",
            "sign_key": _znuny_id(world),
            "encrypt": False,
            "encrypt_keys": None,
        }
        pgp = next(b for b in body["backends"] if b["backend"] == "pgp")
        assert {r["address"]: r["status"] for r in pgp["recipients"]} == {
            CUSTOMER: "ok",
            "nokey@example.net": "missing",
        }
        new = await client.get(
            "/api/v1/tickets/crypto-options", params={"queue_id": QUEUE_ID, "to": CUSTOMER}
        )
        assert new.status_code == 200, new.text
        assert new.json()["default"]["sign_key"] == _znuny_id(world)


@pytest.mark.asyncio
async def test_reply_pgp_sign_and_encrypt_is_sent_secured_and_stored_clear(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent], world: CryptoWorld
) -> None:
    url, factory, sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out pgp")
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json=_email(
                "crypto-out pgp reply",
                {"backend": "pgp", "sign_key": _znuny_id(world), "encrypt": True},
            ),
        )
        assert resp.status_code == 201, resp.text
        aid = int(resp.json()["article_id"])
    raw = _raw(sent)
    assert b"multipart/encrypted" in raw and b"4711" not in raw
    walked = walk_message(raw, _customer_config(world))
    assert walked.security is not None
    assert walked.security.status == "verified" and walked.security.encrypted
    assert walked.content is not None and b"4711" in walked.content
    body = _rows(url, "SELECT a_body FROM article_data_mime WHERE article_id = :a", a=aid)
    assert "4711" in body[0][0]
    plain = _rows(url, "SELECT body FROM article_data_mime_plain WHERE article_id = :a", a=aid)
    assert bytes(plain[0][0]) == raw
    flags = _flags(url, aid)
    assert flags["TiqoraCryptoVerify"] == "pgp:verified"
    assert flags["TiqoraCryptoLayers"] == "signed,encrypted"


@pytest.mark.asyncio
async def test_encrypt_without_recipient_key_is_422_and_nothing_happens(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent],
) -> None:
    url, factory, sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out missing")
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json=_email(
                "crypto-out missing", {"backend": "pgp", "encrypt": True}, to="nokey@example.net"
            ),
        )
    assert resp.status_code == 422, resp.text
    assert "nokey@example.net (missing)" in resp.json()["detail"]
    assert sent.calls == []
    assert _rows(url, "SELECT id FROM article WHERE ticket_id = :t", t=tid) == []


@pytest.mark.asyncio
async def test_email_security_on_a_note_is_rejected(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent],
) -> None:
    _url, factory, _sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out note")
        payload = _email("crypto-out note", {"backend": "pgp", "encrypt": True})
        payload["channel"] = "note"
        resp = await client.post(f"/api/v1/tickets/{tid}/articles", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_forward_smime_signed_and_encrypted(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent], world: CryptoWorld
) -> None:
    url, factory, sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out fwd")
        note = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json={
                "sender_type": "agent",
                "channel": "note",
                "subject": "crypto-out note",
                "body": "internal",
                "is_visible_for_customer": False,
            },
        )
        assert note.status_code == 201
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles/{note.json()['article_id']}/forward",
            json={
                "to_address": CUSTOMER,
                "subject": "Fwd: crypto-out fwd",
                "body": "Weitergeleitet: Zugangscode 4711",
                "email_security": {"backend": "smime", "sign_key": SUPPORT, "encrypt": True},
            },
        )
        assert resp.status_code == 201, resp.text
        aid = int(resp.json()["article_id"])
    raw = _raw(sent)
    assert b"application/pkcs7-mime" in raw and b"4711" not in raw
    walked = walk_message(raw, _customer_config(world))
    assert walked.security is not None
    assert walked.security.method == "smime"
    assert walked.security.status == "verified" and walked.security.encrypted
    assert _flags(url, aid)["TiqoraCryptoVerify"] == "smime:verified"
    history = _rows(
        url,
        "SELECT t.name FROM ticket_history h JOIN ticket_history_type t"
        " ON t.id = h.history_type_id WHERE h.article_id = :a",
        a=aid,
    )
    assert ("Forward",) in [tuple(r) for r in history]


@pytest.mark.asyncio
async def test_generic_interface_article_send_with_email_security(
    env: tuple[str, async_sessionmaker[AsyncSession], Sent], world: CryptoWorld
) -> None:
    from tiqora.api.compat.operations import op_ticket_update
    from tiqora.znuny.sysconfig import SysConfig

    url, factory, sent = env
    async with _client(factory) as client:
        tid = await _new_ticket(client, "crypto-out gi")

    async def _fetch(name: str) -> Any:
        return None

    async with factory() as session:
        result = await op_ticket_update(
            {
                "SessionID": "crypto-outbound-test-session",
                "TicketID": tid,
                "Article": {
                    "ArticleSend": 1,
                    "CommunicationChannel": "Email",
                    "Subject": "crypto-out gi",
                    "Body": "GI Zugangscode 4711",
                    "To": CUSTOMER,
                    "ContentType": "text/plain; charset=utf-8",
                    "EmailSecurity": {"Backend": "PGP", "SignKey": world.support_fp},
                },
            },
            session,
            factory,
            _SessionStoreStub(AGENT),  # type: ignore[arg-type]
            SysConfig(fetch=_fetch),
        )
    assert "TicketID" in result, result
    raw = _raw(sent)
    assert b"multipart/signed" in raw
    walked = walk_message(raw, _customer_config(world))
    assert walked.security is not None and walked.security.status == "verified"
    arts = _rows(
        url,
        "SELECT a.id FROM article a JOIN article_data_mime m ON m.article_id = a.id"
        " WHERE a.ticket_id = :t AND m.a_subject LIKE '%crypto-out gi%'",
        t=tid,
    )
    assert len(arts) == 1
    assert _flags(url, int(arts[0][0]))["TiqoraCryptoLayers"] == "signed"


class _SessionStoreStub:
    """Minimal session store for op_* calls: every SessionID is the agent's."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    async def get(self, session_id: str) -> tuple[int, str]:
        return self.user_id, "crypto.outbound"
