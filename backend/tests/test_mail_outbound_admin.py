"""DB tests for admin outbound-mail settings (GET/PUT, encrypted password).

Uses unique seed behaviour via the singleton table (id=1); each test
creates tiqora tables and works against a fresh row or default empty GET.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import mail_outbound as admin_mail
from tiqora.config import get_settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraMailOutbound
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.mail_outbound import SINGLETON_ID

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Clear singleton between tests sharing the session-scoped container.
        conn.execute(text("DELETE FROM tiqora_mail_outbound"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


async def test_get_mail_outbound_defaults(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_mail.get_mail_outbound(_root_user(), session)
            assert out.enabled is False
            assert out.host == ""
            assert out.port == 25
            assert out.security == "none"
            assert out.has_password is False
            assert not hasattr(out, "auth_password") or "auth_password" not in out.model_dump()
    finally:
        await engine.dispose()


async def test_put_mail_outbound_encrypts_password_and_has_password(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_mail.put_mail_outbound(
                admin_mail.MailOutboundUpdate(
                    enabled=True,
                    host="mail.example.com",
                    port=587,
                    security="starttls",
                    auth_type="password",
                    auth_user="agent@example.com",
                    auth_password="s3cret-pass",
                    from_default="Helpdesk <help@example.com>",
                    timeout_seconds=30,
                ),
                _root_user(),
                session,
            )
            assert out.enabled is True
            assert out.host == "mail.example.com"
            assert out.port == 587
            assert out.security == "starttls"
            assert out.auth_user == "agent@example.com"
            assert out.has_password is True
            assert out.from_default == "Helpdesk <help@example.com>"
            assert out.timeout_seconds == 30
            dumped = out.model_dump()
            assert "auth_password" not in dumped
            assert "s3cret-pass" not in str(dumped)

            # Stored ciphertext is not plaintext and decrypts correctly.
            row = (
                await session.execute(
                    select(TiqoraMailOutbound).where(TiqoraMailOutbound.id == SINGLETON_ID)
                )
            ).scalar_one()
            assert row.auth_password != "s3cret-pass"
            assert "s3cret" not in row.auth_password
            assert decrypt_secret(settings.secret_key, row.auth_password) == "s3cret-pass"

            # Empty password on PUT must keep the existing one.
            out2 = await admin_mail.put_mail_outbound(
                admin_mail.MailOutboundUpdate(
                    host="mail2.example.com",
                    auth_password="",
                ),
                _root_user(),
                session,
            )
            assert out2.host == "mail2.example.com"
            assert out2.has_password is True
            row2 = (
                await session.execute(
                    select(TiqoraMailOutbound).where(TiqoraMailOutbound.id == SINGLETON_ID)
                )
            ).scalar_one()
            assert decrypt_secret(settings.secret_key, row2.auth_password) == "s3cret-pass"

            # Omitted password also keeps existing.
            out3 = await admin_mail.put_mail_outbound(
                admin_mail.MailOutboundUpdate(port=465, security="ssl"),
                _root_user(),
                session,
            )
            assert out3.port == 465
            assert out3.security == "ssl"
            assert out3.has_password is True
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_encrypt_secret_roundtrip_unit() -> None:
    token = encrypt_secret("test-secret-key", "hello")
    assert token != "hello"
    assert decrypt_secret("test-secret-key", token) == "hello"
    assert decrypt_secret("test-secret-key", "not-a-token") is None
