"""Background workers must send through the *configured* relay.

Both takeover workers used to build their sender straight from the
``TIQORA_SMTP_*`` environment. Those variables are unset whenever the relay
is configured through the admin UI instead, so the sender fell back to
``localhost:25`` and every notification and auto-response failed with
``SMTPConnectError`` — while the tick still reported ``sent: 0, errors: 0``
and looked healthy.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.smtp import SmtpMailSender
from tiqora.config import get_settings
from tiqora.domain.mail_outbound import build_outbound_sender


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    conn = await session.connection()
    await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    await session.commit()


@pytest.fixture(autouse=True, scope="module")
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    """This module only touches the singleton outbound row; drop it again.

    The generic id-snapshot helper cannot cover it: the tiqora_* tables are
    created by the test itself, so at module-setup time there is nothing to
    snapshot yet.
    """
    yield
    engine = create_engine(mariadb_znuny_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_mail_outbound WHERE id = 1"))
    finally:
        engine.dispose()


async def _set_outbound(session: AsyncSession, *, enabled: int, host: str, port: int) -> None:
    await session.execute(text("DELETE FROM tiqora_mail_outbound WHERE id = 1"))
    await session.execute(
        text(
            "INSERT INTO tiqora_mail_outbound (id, enabled, host, port, security, auth_type,"
            " auth_user, auth_password, from_default, timeout_seconds,"
            " oauth2_token_config_name)"
            " VALUES (1, :en, :host, :port, 'starttls', 'none', '', '', 'x@example.com', 30, '')"
        ),
        {"en": enabled, "host": host, "port": port},
    )
    await session.commit()


@pytest.mark.db
async def test_worker_sender_uses_configured_relay(mariadb_znuny_url: str) -> None:
    """A relay configured in the database wins over the unset environment."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _set_outbound(session, enabled=1, host="relay.example.com", port=2525)

        async with factory() as session:
            sender = await build_outbound_sender(session, sendmail_bcc="bcc@example.com")

        assert isinstance(sender, SmtpMailSender)
        # Without the fix these were the env defaults, localhost:25.
        assert sender._host == "relay.example.com"
        assert sender._port == 2525
        assert sender._security == "starttls"
        assert sender.sendmail_bcc == "bcc@example.com"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_worker_sender_falls_back_to_env_when_no_relay(mariadb_znuny_url: str) -> None:
    """A disabled row must not shadow an explicitly configured environment."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _set_outbound(session, enabled=0, host="ignored.example.com", port=2525)

        async with factory() as session:
            sender = await build_outbound_sender(session)

        settings = get_settings()
        assert isinstance(sender, SmtpMailSender)
        assert sender._host == settings.smtp_host
        assert sender._port == settings.smtp_port
    finally:
        await engine.dispose()
