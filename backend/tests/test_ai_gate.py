"""DB tests for the AI Readiness-Gate (``tiqora.ai.gate``, plan §3.0).

Follows the direct-function-call pattern from ``test_admin_daemons.py``:
local testcontainer only (never Prod), exercise the gate helpers against a
real async session.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai.gate import (
    OPERATION_MODE_PARALLEL,
    OPERATION_MODE_TIQORA_PRIMARY,
    AiAutoReplyPausedError,
    AiGateError,
    get_operation_mode,
    is_auto_reply_paused,
    is_tiqora_primary,
    queue_serves_tiqora_only_channel,
    require_feature_allowed,
    require_tiqora_primary,
    set_auto_reply_paused,
    set_operation_mode,
)
from tiqora.ai.models import FEATURE_AUTO_REPLY
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.settings_store import set_setting

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_settings"))
    engine.dispose()


async def test_default_operation_mode_is_parallel(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await get_operation_mode(session) == OPERATION_MODE_PARALLEL
            assert await is_tiqora_primary(session) is False
    finally:
        await engine.dispose()


async def test_set_operation_mode_rejects_invalid_value(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(ValueError, match="Invalid operation_mode"):
                await set_operation_mode(session, "znuny_primary")
    finally:
        await engine.dispose()


async def test_require_tiqora_primary_raises_when_parallel(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(AiGateError):
                await require_tiqora_primary(session)
    finally:
        await engine.dispose()


async def test_require_tiqora_primary_ok_after_switch_and_regression_allowed(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            await require_tiqora_primary(session)  # does not raise
            assert await is_tiqora_primary(session) is True

            # Regression to parallel must always be allowed (no gate on the
            # mode switch itself, only on enabling AI features).
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)
            assert await is_tiqora_primary(session) is False
            with pytest.raises(AiGateError):
                await require_tiqora_primary(session)
    finally:
        await engine.dispose()


async def test_auto_reply_kill_switch_blocks_even_when_primary(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            assert await is_auto_reply_paused(session) is False
            await require_feature_allowed(session, FEATURE_AUTO_REPLY)

            await set_auto_reply_paused(session, True)
            assert await is_auto_reply_paused(session) is True
            with pytest.raises(AiAutoReplyPausedError):
                await require_feature_allowed(session, FEATURE_AUTO_REPLY)

            await set_auto_reply_paused(session, False)
            await require_feature_allowed(session, FEATURE_AUTO_REPLY)
    finally:
        await engine.dispose()


async def test_telegram_source_channel_bypasses_parallel_gate(mariadb_znuny_url: str) -> None:
    """Plan §3.0 / T5 relaxation: a Telegram-sourced auto-reply run may
    proceed in ``parallel`` operation — Telegram has no Znuny counterpart to
    double-answer alongside (see TIQORA_ONLY_CHANNELS)."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await is_tiqora_primary(session) is False
            # Case-insensitive: the outbox payload channel value.
            await require_feature_allowed(
                session, FEATURE_AUTO_REPLY, source_channel="Telegram"
            )
    finally:
        await engine.dispose()


async def test_email_source_channel_still_requires_primary(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await is_tiqora_primary(session) is False
            with pytest.raises(AiGateError):
                await require_feature_allowed(session, FEATURE_AUTO_REPLY, source_channel="email")
            # No source_channel at all behaves the same as before (regression
            # guard for every non-outbox caller, e.g. Manual Assist re-checks
            # that never pass one).
            with pytest.raises(AiGateError):
                await require_feature_allowed(session, FEATURE_AUTO_REPLY)
    finally:
        await engine.dispose()


async def test_kill_switch_blocks_tiqora_only_channel_too(mariadb_znuny_url: str) -> None:
    """The kill-switch (plan #10) always wins, even for the channel
    exception — a Telegram-sourced run must not slip past ``paused``."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_auto_reply_paused(session, True)
            with pytest.raises(AiAutoReplyPausedError):
                await require_feature_allowed(
                    session, FEATURE_AUTO_REPLY, source_channel="telegram"
                )
            await set_auto_reply_paused(session, False)
    finally:
        await engine.dispose()


async def test_queue_serves_tiqora_only_channel_true_and_false(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Seed queue "Raw" (id=2) is part of the default Znuny fixture data.
            await set_setting(session, "channel.telegram.queue_name", "Raw")
            assert await queue_serves_tiqora_only_channel(session, 2) is True
            # A different existing queue ("Junk", id=3) does not match.
            assert await queue_serves_tiqora_only_channel(session, 3) is False
            # Nonexistent queue id.
            assert await queue_serves_tiqora_only_channel(session, 999_999) is False
            await session.execute(
                text("DELETE FROM tiqora_settings WHERE `key` = 'channel.telegram.queue_name'")
            )
            await session.commit()
    finally:
        await engine.dispose()
