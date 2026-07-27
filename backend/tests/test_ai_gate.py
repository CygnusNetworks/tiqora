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
    require_feature_allowed,
    require_tiqora_primary,
    set_auto_reply_paused,
    set_operation_mode,
)
from tiqora.ai.models import FEATURE_AUTO_REPLY
from tiqora.db.tiqora.base import TiqoraBase

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
