"""DB integration: inbound crypto end to end (postmaster → article → API).

* A PGP/MIME (Thunderbird-style) and an S/MIME (Outlook-style) mail go
  through ``process_message``: the article holds body and attachments in
  clear, the ``TiqoraCrypto*`` flags carry the security result, the original
  mail is kept in ``article_data_mime_plain`` and the article API exposes
  ``security``.
* Legacy decrypt-on-view: an article stored while crypto was off (as Znuny
  leaves never-opened encrypted mail) is decrypted by the read endpoints
  without touching the stored article; the result is cached in the flags.

Uses the 796xx id range; everything it creates is deleted again.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._crypto_mail_fixtures import (
    SAMPLE_PDF,
    CryptoWorld,
    mail,
    mixed_entity,
    outlook_smime_signed,
    outlook_smime_signed_entity,
    strip_mime_version,
    text_entity,
    thunderbird_pgp_mime,
)
from tests._row_cleanup import cleanup_module
from tiqora.config import get_settings
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

NOW = datetime(2026, 9, 29, 12, 0, 0)
QUEUE_ID = 79600
QUEUE_NAME = "CryptoInboundQueue"
GROUP_ID = 79630
AGENT = 79601


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
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE user_id = {AGENT} OR group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id = {AGENT}",
        "DELETE FROM tiqora_mail_log WHERE subject LIKE 'crypto-inbound %' OR subject = '...'",
    ):
        conn.execute(text(sql))


def _seed(url: str) -> None:
    engine = _sync(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'crypto.inbound', 'x', 'Crypto', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW, "uid": AGENT},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'crypto-inbound-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "name": QUEUE_NAME, "gid": GROUP_ID, "t": NOW},
        )
    engine.dispose()


def _teardown(url: str) -> None:
    engine = _sync(url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _counter_cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    # ticket numbers drawn by create_ticket(); the rest is removed by _cleanup
    yield from cleanup_module(mariadb_znuny_url, tables=("ticket_number_counter",))


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    yield w
    w.cleanup()


@pytest.fixture
async def env(
    mariadb_znuny_url: str, world: CryptoWorld, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    from tiqora.crypto.article_view import clear_cache

    _seed(mariadb_znuny_url)
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_GNUPGHOME", world.support_home)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_CERT_DIR", world.cert_dir)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_PRIVATE_DIR", world.private_dir)
    _set_enabled(monkeypatch, True)
    clear_cache()
    engine = create_async_engine(_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield mariadb_znuny_url, factory
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)
        clear_cache()
        get_settings.cache_clear()


def _set_enabled(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_ENABLED", "1" if on else "0")
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_ENABLED", "1" if on else "0")
    get_settings.cache_clear()


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _account() -> MailAccount:
    return MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=QUEUE_ID,
        trusted=1,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


def _routed(raw: bytes, subject: str) -> bytes:
    """Route to the test queue via X-OTRS-Queue (trusted account)."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    head = head.replace(b"Subject: ", b"X-Crypto-Old-Subject: ", 1)
    extra = f"\r\nSubject: {subject}\r\nX-OTRS-Queue: {QUEUE_NAME}".encode()
    return head + extra + sep + body


async def _ingest(factory: async_sessionmaker[AsyncSession], raw: bytes) -> int:
    from tiqora.channels.email.pipeline import process_message

    async with factory() as session, session.begin():
        result = await process_message(
            session, factory, _sysconfig(), raw=raw, account=_account(), user_id=1
        )
    assert result.outcome == "new_ticket", result
    assert result.article_id is not None
    return int(result.article_id)


def _row(url: str, sql: str, **params: Any) -> Any:
    engine = _sync(url)
    with engine.begin() as conn:
        row = conn.execute(text(sql), params).first()
    engine.dispose()
    return row


def _flags(url: str, article_id: int) -> dict[str, str]:
    engine = _sync(url)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT article_key, article_value FROM article_flag"
                " WHERE article_id = :a AND article_key LIKE 'TiqoraCrypto%'"
            ),
            {"a": article_id},
        ).all()
    engine.dispose()
    return {str(k): str(v) for k, v in rows}


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
        id=AGENT, login="crypto.inbound", first_name="C", last_name="A", auth_method="session"
    )
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _ticket_of(url: str, article_id: int) -> int:
    return int(_row(url, "SELECT ticket_id FROM article WHERE id = :a", a=article_id)[0])


# ---------------------------------------------------------------- at ingest


@pytest.mark.asyncio
async def test_pgp_mime_mail_is_stored_in_clear_with_security(
    env: tuple[str, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, factory = env
    raw = thunderbird_pgp_mime(world)
    article_id = await _ingest(factory, _routed(raw, "..."))
    body, subject = _row(
        url,
        "SELECT a_body, a_subject FROM article_data_mime WHERE article_id = :a",
        a=article_id,
    )
    assert "invoice is attached" in body
    assert "BEGIN PGP" not in body
    assert subject == "Invoice 2026-17 question"  # protected header (outer was "...")
    att = _row(
        url,
        "SELECT filename, content FROM article_data_mime_attachment WHERE article_id = :a",
        a=article_id,
    )
    assert att[0] == "invoice.pdf" and bytes(att[1]) == SAMPLE_PDF
    flags = _flags(url, article_id)
    assert flags["TiqoraCryptoVerify"] == "pgp:verified"
    assert flags["TiqoraCryptoLayers"] == "signed,encrypted"
    assert flags["TiqoraCryptoKeyID"] == world.customer_fp
    plain = _row(
        url, "SELECT body FROM article_data_mime_plain WHERE article_id = :a", a=article_id
    )
    assert b"BEGIN PGP MESSAGE" in bytes(plain[0])

    ticket_id = _ticket_of(url, article_id)
    async with _client(factory) as client:
        listed = (await client.get(f"/api/v1/tickets/{ticket_id}/articles")).json()
        assert listed[0]["security"] == {
            "method": "pgp",
            "signed": True,
            "encrypted": True,
            "status": "verified",
            "signer": "Carla Customer <customer@example.com>",
            "key_id": world.customer_fp,
            "detail": "good signature",
        }
        body_resp = (
            await client.get(f"/api/v1/tickets/{ticket_id}/articles/{article_id}/body")
        ).json()
        assert body_resp["security"]["status"] == "verified"
        assert "invoice is attached" in body_resp["body"]


@pytest.mark.asyncio
async def test_smime_encrypted_signed_mail(
    env: tuple[str, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, factory = env
    enc = strip_mime_version(world.smime_encrypt(outlook_smime_signed_entity(world)))
    article_id = await _ingest(factory, _routed(mail(enc), "crypto-inbound smime"))
    body = _row(url, "SELECT a_body FROM article_data_mime WHERE article_id = :a", a=article_id)[0]
    assert "reset my VPN token" in body
    assert (
        _row(
            url,
            "SELECT COUNT(*) FROM article_data_mime_attachment WHERE article_id = :a"
            " AND filename IN ('smime.p7m', 'smime.p7s')",
            a=article_id,
        )[0]
        == 0
    )
    flags = _flags(url, article_id)
    assert flags["TiqoraCryptoVerify"] == "smime:verified"
    assert flags["TiqoraCryptoLayers"] == "signed,encrypted"
    assert flags["TiqoraCryptoSigner"] == "customer@example.com"


@pytest.mark.asyncio
async def test_crypto_failure_never_blocks_delivery(
    env: tuple[str, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, factory = env
    enc = strip_mime_version(world.smime_encrypt(text_entity("hi\r\n")))
    raw = _routed(mail(enc, to="nobody@example.com"), "crypto-inbound undecryptable")
    article_id = await _ingest(factory, raw)
    assert _flags(url, article_id)["TiqoraCryptoVerify"] == "smime:decrypt_failed"
    assert (
        _row(
            url,
            "SELECT filename FROM article_data_mime_attachment WHERE article_id = :a",
            a=article_id,
        )[0]
        == "smime.p7m"
    )


@pytest.mark.asyncio
async def test_plain_signed_mail_keeps_signature_status_only(
    env: tuple[str, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, factory = env
    article_id = await _ingest(factory, _routed(outlook_smime_signed(world), "crypto-inbound sig"))
    flags = _flags(url, article_id)
    assert flags["TiqoraCryptoVerify"] == "smime:verified"
    assert flags["TiqoraCryptoLayers"] == "signed"


@pytest.mark.asyncio
async def test_smime_fetch_from_customer_runs_before_inbound_crypto(
    env: tuple[str, async_sessionmaker[AsyncSession]],
    world: CryptoWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``000-SMIMEFetchFromCustomer`` pre-filter: called with ``From`` before the verify."""
    from tiqora.channels.email import pipeline
    from tiqora.crypto import customer_fetch
    from tiqora.crypto import inbound as inbound_mod

    _url, factory = env
    order: list[str] = []

    async def fake_fetch(_s: Any, _settings: Any, cfg: Any, from_header: str) -> Any:
        assert cfg.smime.fetch_from_customer
        order.append(f"fetch:{from_header}")
        return customer_fetch.FetchReport()

    real_inbound = inbound_mod.process_inbound_crypto

    async def spy_inbound(*args: Any, **kwargs: Any) -> Any:
        order.append("inbound")
        return await real_inbound(*args, **kwargs)

    monkeypatch.setattr(customer_fetch, "fetch_for_sender", fake_fetch)
    monkeypatch.setattr(inbound_mod, "process_inbound_crypto", spy_inbound)

    async def _fetch(name: str) -> Any:
        return "1" if name == "SMIME::FetchFromCustomer" else None

    raw = _routed(outlook_smime_signed(world), "crypto-inbound fetch")
    async with factory() as session, session.begin():
        await pipeline.process_message(
            session, factory, SysConfig(fetch=_fetch), raw=raw, account=_account(), user_id=1
        )
    assert order[0].startswith("fetch:") and "customer@example.com" in order[0]
    assert order[1] == "inbound"

    # Switched off (Znuny default): no fetch.
    order.clear()
    await _ingest(factory, _routed(outlook_smime_signed(world), "crypto-inbound nofetch"))
    assert order == ["inbound"]


# ---------------------------------------------------------- decrypt on view


@pytest.mark.asyncio
async def test_legacy_encrypted_article_is_decrypted_on_view(
    env: tuple[str, async_sessionmaker[AsyncSession]],
    world: CryptoWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, factory = env
    entity = mixed_entity("Legacy secret text\r\n", [("report.pdf", "application/pdf", SAMPLE_PDF)])
    raw = _routed(mail(strip_mime_version(world.smime_encrypt(entity))), "crypto-inbound legacy")
    # Stored the way Znuny leaves a never-opened encrypted mail: smime.p7m
    # attachment, no text body, raw mail in article_data_mime_plain.
    _set_enabled(monkeypatch, False)
    article_id = await _ingest(factory, raw)
    engine = _sync(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO article_data_mime_plain (article_id, body, create_time, create_by,"
                " change_time, change_by) VALUES (:a, :b, :t, 1, :t, 1)"
            ),
            {"a": article_id, "b": raw, "t": NOW},
        )
    engine.dispose()
    assert _flags(url, article_id) == {}
    _set_enabled(monkeypatch, True)

    ticket_id = _ticket_of(url, article_id)
    base = f"/api/v1/tickets/{ticket_id}/articles/{article_id}"
    async with _client(factory) as client:
        body = (await client.get(f"{base}/body")).json()
        assert "Legacy secret text" in body["body"]
        assert body["security"]["status"] == "decrypted"
        assert body["security"]["encrypted"] is True
        atts = (await client.get(f"{base}/attachments")).json()
        assert [(a["id"], a["filename"]) for a in atts] == [(-1, "report.pdf")]
        pdf = await client.get(f"{base}/attachments/-1?download=1")
        assert pdf.status_code == 200 and pdf.content == SAMPLE_PDF
        assert (await client.get(f"{base}/attachments/-2")).status_code == 404
        listed = (await client.get(f"/api/v1/tickets/{ticket_id}/articles")).json()
        assert listed[0]["security"]["status"] == "decrypted"

    # read-only: the stored article is untouched, only the flag cache is new
    stored = _row(
        url,
        "SELECT filename FROM article_data_mime_attachment WHERE article_id = :a",
        a=article_id,
    )
    assert stored[0] == "smime.p7m"
    assert _flags(url, article_id)["TiqoraCryptoVerify"] == "smime:decrypted"


@pytest.mark.asyncio
async def test_legacy_inline_pgp_body_without_raw(
    env: tuple[str, async_sessionmaker[AsyncSession]],
    world: CryptoWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, factory = env
    armored = world.pgp_encrypt(b"inline legacy secret", sign=True).decode()
    _set_enabled(monkeypatch, False)
    article_id = await _ingest(factory, _routed(mail(text_entity(armored)), "crypto-inbound inl"))
    _set_enabled(monkeypatch, True)
    ticket_id = _ticket_of(url, article_id)
    async with _client(factory) as client:
        body = (await client.get(f"/api/v1/tickets/{ticket_id}/articles/{article_id}/body")).json()
    assert body["body"].strip() == "inline legacy secret"
    assert body["security"]["status"] == "verified"
    assert "BEGIN PGP" in str(
        _row(url, "SELECT a_body FROM article_data_mime WHERE article_id = :a", a=article_id)[0]
    )
