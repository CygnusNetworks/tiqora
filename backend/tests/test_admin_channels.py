"""DB tests for the admin channel-config API (enable flags + config keys
stored in ``tiqora_settings``), following the direct-router-call pattern
used by ``test_admin_api.py``."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import channels as admin_channels
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    # Root (id=1) is present via Znuny's initial_insert seed data loaded by
    # the mariadb_znuny_url fixture; admin.channels routes don't themselves
    # check group membership (that's the get_admin_user dependency, which
    # is bypassed when calling router functions directly, same as
    # test_admin_api.py).
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


async def test_list_channels_defaults_disabled_and_no_config(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            listed = await admin_channels.list_channels(_root_user(), session)
            names = {c.channel for c in listed}
            assert names == {"sms", "whatsapp", "phone", "telegram"}
            assert all(c.enabled is False for c in listed)
    finally:
        await engine.dispose()


def _settings() -> Any:
    from tiqora.config import Settings

    return Settings(environment="test", secret_key="test-secret-key-for-channel-encrypt-32")


async def test_update_channel_enable_and_set_config(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            updated = await admin_channels.update_channel(
                "sms",
                admin_channels.ChannelConfigUpdate(
                    enabled=True,
                    config={
                        "outbound_webhook_url": "https://gw.example.com/send",
                        "inbound_shared_secret": "s3cret",
                    },
                ),
                _root_user(),
                session,
                _settings(),
            )
            assert updated.enabled is True
            assert updated.config["outbound_webhook_url"] == "https://gw.example.com/send"
            # Secret-shaped keys are masked on read.
            assert updated.config["inbound_shared_secret"] == "********"

            # At rest the secret must be Fernet-encrypted, not plaintext.
            from tiqora.channels.common import setting_key
            from tiqora.domain.settings_store import get_setting

            stored = await get_setting(session, setting_key("sms", "inbound_shared_secret"))
            assert stored is not None
            assert stored != "s3cret"
            assert stored.startswith("gAAAA")

            fetched = await admin_channels.get_channel("sms", _root_user(), session)
            assert fetched.enabled is True
            assert fetched.config["inbound_shared_secret"] == "********"
    finally:
        await engine.dispose()


async def test_update_channel_telegram_bot_token_masked(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            updated = await admin_channels.update_channel(
                "telegram",
                admin_channels.ChannelConfigUpdate(
                    enabled=True,
                    config={
                        "bot_token": "123456:ABC-DEF",
                        "queue_name": "Raw",
                        "default_customer_user": "portal-default",
                    },
                ),
                _root_user(),
                session,
                _settings(),
            )
            assert updated.enabled is True
            assert updated.config["bot_token"] == "********"
            assert updated.config["queue_name"] == "Raw"

            from tiqora.channels.common import setting_key
            from tiqora.domain.settings_store import get_setting

            stored = await get_setting(session, setting_key("telegram", "bot_token"))
            assert stored is not None
            assert stored != "123456:ABC-DEF"
            assert stored.startswith("gAAAA")
    finally:
        await engine.dispose()


async def test_update_channel_rejects_unknown_key(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await admin_channels.update_channel(
                    "sms",
                    admin_channels.ChannelConfigUpdate(config={"not_a_real_key": "x"}),
                    _root_user(),
                    session,
                    _settings(),
                )
            assert exc_info.value.status_code == 422
    finally:
        await engine.dispose()


async def test_get_unknown_channel_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await admin_channels.get_channel("carrier-pigeon", _root_user(), session)
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()
