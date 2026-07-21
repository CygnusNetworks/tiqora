"""Postmaster integration: a PGP-signed inbound mail gets its verification
flag set on the created article (tiqora.crypto.inbound wired into
tiqora.channels.email.pipeline.process_message).

DB-marked (needs a real ticket/article write path); skipped if the ``gpg``
binary is not on PATH.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.pipeline import process_message
from tiqora.config import get_settings
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.znuny.sysconfig import SysConfig

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
]

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    """create_ticket() writes to tiqora_cache_invalidation and
    tiqora_event_outbox; Alembic is not run against this testcontainer."""
    for ddl in (
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
    ):
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _make_account() -> MailAccount:
    return MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=2,
        trusted=1,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


def _gen_pgp_key(gnupghome: str) -> str:
    import gnupg

    gpg = gnupg.GPG(gnupghome=gnupghome)
    gpg.encoding = "utf-8"
    input_data = gpg.gen_key_input(
        name_email="customer@example.com",
        passphrase="",
        key_type="RSA",
        key_length=2048,
        no_protection=True,
    )
    key = gpg.gen_key(input_data)
    assert key.fingerprint
    clear_signed = gpg.sign("please help with my ticket", keyid=key.fingerprint, clearsign=True)
    return str(clear_signed.data.decode("utf-8"))


def _build_raw_email(signed_body: str) -> bytes:
    message = (
        "From: customer@example.com\r\n"
        "To: support@tiqora.test\r\n"
        "Subject: PGP-signed request\r\n"
        "X-OTRS-Queue: Raw\r\n"
        "X-OTRS-State: new\r\n"
        "X-OTRS-Priority: 3 normal\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n" + signed_body
    )
    return message.encode("utf-8")


@pytest.mark.asyncio
async def test_signed_inbound_mail_sets_crypto_verify_flag(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as gnupghome:  # noqa: S108 — short gpg-agent socket path
        signed_body = _gen_pgp_key(gnupghome)

        monkeypatch.setenv("TIQORA_CRYPTO_PGP_ENABLED", "1")
        monkeypatch.setenv("TIQORA_CRYPTO_PGP_GNUPGHOME", gnupghome)
        get_settings.cache_clear()
        try:
            engine = create_async_engine(_mysql_async(mariadb_znuny_url))
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            sysconfig = _make_sysconfig()
            account = _make_account()
            raw = _build_raw_email(signed_body)

            async with factory() as session:
                await _seed_tiqora_tables(session)

            async with factory() as session, session.begin():
                result = await process_message(
                    session,
                    factory,
                    sysconfig,
                    raw=raw,
                    account=account,
                    user_id=1,
                )
                assert result.outcome == "new_ticket"
                assert result.ticket_id is not None

            async with factory() as session:
                article_id = (
                    await session.execute(
                        text("SELECT id FROM article WHERE ticket_id = :tid"),
                        {"tid": result.ticket_id},
                    )
                ).scalar_one()
                flag_value = (
                    await session.execute(
                        text(
                            "SELECT article_value FROM article_flag"
                            " WHERE article_id = :aid AND article_key = 'TiqoraCryptoVerify'"
                        ),
                        {"aid": article_id},
                    )
                ).scalar_one()
                assert flag_value == "pgp:verified"
            await engine.dispose()
        finally:
            get_settings.cache_clear()
