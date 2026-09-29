"""DB round-trip: signed/encrypted event notifications (Znuny ``SecurityOptionsGet``).

``notification_event_item`` rows exactly as Znuny's AdminNotificationEvent
stores them (``EmailSecuritySettings`` = ``1``, ``EmailSigningCrypting``,
``EmailMissingSigningKeys``/``EmailMissingCryptingKeys``) drive
``worker.notifications.process_event``; a recording mail sender captures the
exact bytes, which are read back with the *recipient's* keys through
``crypto.mime_walk``. Keys are generated at runtime with real gpg/openssl.

Uses the 798xx id range; everything it creates is deleted again.
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
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.config import get_settings
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.crypto.mime_walk import walk_message
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime_store import SmimeStore
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, set_setting
from tiqora.worker.notifications import process_event
from tiqora.znuny.sysconfig import SysConfig

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]

NOW = datetime(2026, 9, 29, 12, 0, 0)
QUEUE_ID = 79800
AGENT = 79801
NOKEY_AGENT = 79802
CUSTOMER_LOGIN = "crypto.notify.customer"
NAME_PREFIX = "crypto-notify "
NOKEY = "nokey@example.net"
BODY = "Vertraulich: Ihr Zugangscode lautet 4711."


def _sync_cleanup(conn: Any) -> None:
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
    ids = f"(SELECT id FROM notification_event WHERE name LIKE '{NAME_PREFIX}%')"
    for sql in (
        f"DELETE FROM notification_event_item WHERE notification_id IN {ids}",
        f"DELETE FROM notification_event_message WHERE notification_id IN {ids}",
        f"DELETE FROM notification_event WHERE name LIKE '{NAME_PREFIX}%'",
        f"DELETE FROM user_preferences WHERE user_id IN ({AGENT}, {NOKEY_AGENT})",
        f"DELETE FROM users WHERE id IN ({AGENT}, {NOKEY_AGENT})",
        f"DELETE FROM customer_user WHERE login = '{CUSTOMER_LOGIN}'",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
    ):
        conn.execute(text(sql))


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        _sync_cleanup(conn)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _module_cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    yield from cleanup_module(
        mariadb_znuny_url,
        tables=("ticket_number_counter",),
        setting_keys=(KEY_NOTIFICATION_SENDER_EMAIL,),
    )


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    # support side knows the customer's certificate (encryption target)
    SmimeStore(w.cert_dir, w.private_dir).add_certificate(w.customer_cert.cert_pem)
    # recipient side: own certificate + key, the CA as trust anchor
    cstore = SmimeStore(os.path.join(w.root, "cc"), os.path.join(w.root, "cp"))
    cstore.add_certificate(w.customer_cert.cert_pem)
    cstore.add_private_key(w.customer_cert.encrypted_key("c"), "c")
    cstore.add_certificate(w.ca.cert_pem)
    yield w
    w.cleanup()


def _recipient_config(world: CryptoWorld) -> CryptoConfig:
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


def _seed(url: str, world: CryptoWorld) -> int:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _sync_cleanup(conn)
        for uid, login, email in (
            (AGENT, "crypto.notify.agent", CUSTOMER),
            (NOKEY_AGENT, "crypto.notify.nokey", NOKEY),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :login, 'x', 'Crypto', 'Notify', 1, :t, 1, :t, 1)"
                ),
                {"uid": uid, "login": login, "t": NOW},
            )
            for key, value in (("UserEmail", email), ("UserLanguage", "en")):
                conn.execute(
                    text(
                        "INSERT INTO user_preferences (user_id, preferences_key,"
                        " preferences_value) VALUES (:uid, :k, :v)"
                    ),
                    {"uid": uid, "k": key, "v": value},
                )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:login, :email, 'cust', 'Carla', 'Customer', 1, :t, 1, :t, 1)"
            ),
            {"login": CUSTOMER_LOGIN, "email": CUSTOMER, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, unlock_timeout, system_address_id,"
                " salutation_id, signature_id, follow_up_id, follow_up_lock, default_sign_key,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'CryptoNotifyQueue', 1, 0, 1, 1, 1, 1, 0, :dsk, 1,"
                " :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "dsk": f"PGP::Detached::{_znuny_id(world)}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
                " ticket_priority_id, ticket_state_id, customer_user_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, title, create_time, create_by,"
                " change_time, change_by)"
                " VALUES ('CRYPTONOTIFY1', :qid, 1, :oid, :oid, 3, 1, :cuid, 0, 0, 0, 0, 0, 0,"
                " 0, 'Crypto notify', :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "oid": AGENT, "cuid": CUSTOMER_LOGIN, "t": NOW},
        )
        tid = int(
            conn.execute(text("SELECT id FROM ticket WHERE tn = 'CRYPTONOTIFY1'")).scalar_one()
        )
    engine.dispose()
    return tid


def _add_notification(url: str, name: str, items: dict[str, list[str]]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO notification_event (name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:n, 1, :t, 1, :t, 1)"
            ),
            {"n": NAME_PREFIX + name, "t": NOW},
        )
        nid = int(
            conn.execute(
                text("SELECT id FROM notification_event WHERE name = :n"),
                {"n": NAME_PREFIX + name},
            ).scalar_one()
        )
        base = {"Events": ["CryptoNotifyEvent"], "Transports": ["Email"]}
        for key, values in {**base, **items}.items():
            for value in values:
                conn.execute(
                    text(
                        "INSERT INTO notification_event_item"
                        " (notification_id, event_key, event_value) VALUES (:nid, :k, :v)"
                    ),
                    {"nid": nid, "k": key, "v": value},
                )
        conn.execute(
            text(
                "INSERT INTO notification_event_message"
                " (notification_id, subject, text, content_type, language)"
                " VALUES (:nid, :s, :b, 'text/plain', 'en')"
            ),
            {"nid": nid, "s": f"crypto-notify {name}", "b": BODY},
        )
    engine.dispose()


@pytest.fixture
async def env(
    mariadb_znuny_url: str, world: CryptoWorld, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, int, async_sessionmaker[AsyncSession]]]:
    tid = _seed(mariadb_znuny_url, world)
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_GNUPGHOME", world.support_home)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_CERT_DIR", world.cert_dir)
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_PRIVATE_DIR", world.private_dir)
    monkeypatch.setenv("TIQORA_CRYPTO_PGP_ENABLED", "1")
    monkeypatch.setenv("TIQORA_CRYPTO_SMIME_ENABLED", "1")
    get_settings.cache_clear()
    engine = create_async_engine(mariadb_znuny_url.replace("mysql+pymysql", "mysql+aiomysql"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await set_setting(session, KEY_NOTIFICATION_SENDER_EMAIL, SUPPORT)
    try:
        yield mariadb_znuny_url, tid, factory
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url)
        get_settings.cache_clear()


async def _run(factory: async_sessionmaker[AsyncSession], tid: int) -> tuple[Any, int, int]:
    sender = CapturingMailSender()
    async with factory() as session, session.begin():
        sent, failed = await process_event(
            session,
            SysConfig(session),
            sender,
            event_type="CryptoNotifyEvent",
            ticket_id=tid,
            payload={},
            user_id=1,
            settings=get_settings(),
        )
    return sender, sent, failed


def _rows(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(url)
    with engine.begin() as conn:
        rows = list(conn.execute(text(sql), params).all())
    engine.dispose()
    return rows


def _security(**items: str) -> dict[str, list[str]]:
    return {"EmailSecuritySettings": ["1"], **{k: [v] for k, v in items.items()}}


@pytest.mark.asyncio
async def test_customer_notification_pgp_signed_and_encrypted_round_trip(
    env: tuple[str, int, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, tid, factory = env
    _add_notification(
        url,
        "customer pgp",
        {"Recipients": ["Customer"], **_security(EmailSigningCrypting="PGPSignCrypt")},
    )
    sender, sent, failed = await _run(factory, tid)
    assert (sent, failed) == (1, 0)
    [message] = sender.sent
    raw = message.tiqora_raw
    assert b"multipart/encrypted" in raw and b"4711" not in raw
    assert str(message["To"]) == CUSTOMER and SUPPORT in str(message["From"])
    walked = walk_message(raw, _recipient_config(world))
    assert walked.security is not None
    assert walked.security.method == "pgp" and walked.security.encrypted
    assert walked.security.status == "verified"
    assert walked.content is not None and b"4711" in walked.content
    # signed with the queue default sign key (the support key)
    assert walked.security.key_id == world.support_fp

    [(aid, body)] = _rows(
        url,
        "SELECT a.id, m.a_body FROM article a JOIN article_data_mime m ON m.article_id = a.id"
        " WHERE a.ticket_id = :t",
        t=tid,
    )
    assert "4711" in body
    [(plain,)] = _rows(url, "SELECT body FROM article_data_mime_plain WHERE article_id = :a", a=aid)
    assert bytes(plain) == raw
    flags = dict(
        _rows(
            url,
            "SELECT article_key, article_value FROM article_flag"
            " WHERE article_id = :a AND article_key LIKE 'TiqoraCrypto%'",
            a=aid,
        )
    )
    assert flags["TiqoraCryptoVerify"] == "pgp:verified"
    assert flags["TiqoraCryptoLayers"] == "signed,encrypted"


@pytest.mark.asyncio
async def test_agent_notification_smime_signed_and_encrypted_round_trip(
    env: tuple[str, int, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, tid, factory = env
    _add_notification(
        url,
        "agent smime",
        {"Recipients": ["AgentOwner"], **_security(EmailSigningCrypting="SMIMESignCrypt")},
    )
    sender, sent, failed = await _run(factory, tid)
    assert (sent, failed) == (1, 0)
    raw = sender.sent[0].tiqora_raw
    assert b"application/pkcs7-mime" in raw and b"4711" not in raw
    walked = walk_message(raw, _recipient_config(world))
    assert walked.security is not None
    assert walked.security.method == "smime" and walked.security.encrypted
    assert walked.security.status == "verified", walked.security.detail
    assert walked.content is not None and b"4711" in walked.content
    # agent notifications create no article
    assert _rows(url, "SELECT id FROM article WHERE ticket_id = :t", t=tid) == []


@pytest.mark.asyncio
async def test_missing_encryption_key_skip_sends_nothing(
    env: tuple[str, int, async_sessionmaker[AsyncSession]],
) -> None:
    url, tid, factory = env
    _add_notification(
        url,
        "skip",
        {
            "Recipients": ["RecipientAgents"],
            "RecipientAgents": [str(NOKEY_AGENT)],
            **_security(EmailSigningCrypting="PGPSignCrypt", EmailMissingCryptingKeys="Skip"),
        },
    )
    sender, sent, failed = await _run(factory, tid)
    assert (sent, failed) == (0, 0)
    assert sender.sent == []


@pytest.mark.asyncio
async def test_missing_encryption_key_send_goes_out_signed_only(
    env: tuple[str, int, async_sessionmaker[AsyncSession]], world: CryptoWorld
) -> None:
    url, tid, factory = env
    _add_notification(
        url,
        "send unencrypted",
        {
            "Recipients": ["RecipientAgents"],
            "RecipientAgents": [str(NOKEY_AGENT)],
            **_security(EmailSigningCrypting="PGPSignCrypt", EmailMissingCryptingKeys="Send"),
        },
    )
    sender, sent, failed = await _run(factory, tid)
    assert (sent, failed) == (1, 0)
    raw = sender.sent[0].tiqora_raw
    assert b"multipart/signed" in raw and b"multipart/encrypted" not in raw
    assert str(sender.sent[0]["To"]) == NOKEY
    walked = walk_message(raw, _recipient_config(world))
    assert walked.security is not None
    assert walked.security.signed and not walked.security.encrypted
    assert walked.security.status == "verified"


@pytest.mark.asyncio
async def test_security_off_sends_plain(
    env: tuple[str, int, async_sessionmaker[AsyncSession]],
) -> None:
    url, tid, factory = env
    _add_notification(
        url,
        "plain",
        {
            "Recipients": ["AgentOwner"],
            # level configured but checkbox off: Znuny ignores it
            "EmailSigningCrypting": ["PGPSignCrypt"],
        },
    )
    sender, sent, _failed = await _run(factory, tid)
    assert sent == 1
    assert not getattr(sender.sent[0], "tiqora_raw", b"")
