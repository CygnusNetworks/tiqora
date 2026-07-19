"""Schema-ownership gate tests (Phase 5, subtask 1).

Unit tests exercise the pure gate logic (env-only / marker-only / both).
DB-marked tests seed a real MariaDB testcontainer with recent
``ticket_history``/``sessions`` rows to exercise preflight detection and the
``enable_ownership`` flow end to end.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.domain.ownership import (
    KEY_OWNERSHIP_ENABLED,
    KEY_OWNERSHIP_ENABLED_AT,
    REQUIRED_CONFIRM_PHRASE,
    OwnershipConfirmError,
    OwnershipPreflightError,
    OwnershipState,
    enable_ownership,
    get_ownership_state,
    run_preflight,
)
from tiqora.domain.settings_store import get_setting

pytestmark_db = pytest.mark.db


# --- Pure gate logic: env only / marker only / both ------------------------


def test_state_inactive_when_neither_gate_set() -> None:
    state = OwnershipState(env_flag=False, db_marker=False, enabled_at=None)
    assert state.active is False


def test_state_inactive_env_only() -> None:
    state = OwnershipState(env_flag=True, db_marker=False, enabled_at=None)
    assert state.active is False


def test_state_inactive_marker_only() -> None:
    state = OwnershipState(env_flag=False, db_marker=True, enabled_at="2026-07-19T00:00:00+00:00")
    assert state.active is False


def test_state_active_when_both_gates_set() -> None:
    state = OwnershipState(env_flag=True, db_marker=True, enabled_at="2026-07-19T00:00:00+00:00")
    assert state.active is True


def test_confirm_phrase_mismatch_rejected() -> None:
    # No DB round-trip needed: the confirm check runs before any query.
    import asyncio

    async def _run() -> None:
        with pytest.raises(OwnershipConfirmError):
            await enable_ownership(session=None, confirm="wrong phrase")  # type: ignore[arg-type]

    asyncio.run(_run())


def test_required_confirm_phrase_is_exact() -> None:
    assert REQUIRED_CONFIRM_PHRASE == "I have shut down Znuny"


# --- DB-marked: preflight detection + enable_ownership end to end ----------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _reset_history_and_sessions(session: AsyncSession) -> None:
    """The ``mariadb_znuny_url`` fixture is session-scoped and shared across
    tests in this module — clear rows so each test starts from a clean slate.
    """
    await session.execute(text("DELETE FROM ticket_history"))
    await session.execute(text("DELETE FROM sessions"))
    await session.commit()


async def _seed_tiqora_settings_table(session: AsyncSession) -> None:
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_settings ("
                "`key` VARCHAR(200) PRIMARY KEY, value TEXT)"
            )
        )
    await session.commit()


async def _insert_ticket_history(session: AsyncSession, change_time: datetime) -> None:
    await session.execute(
        text(
            "INSERT INTO ticket_history (name, history_type_id, ticket_id, type_id,"
            " queue_id, owner_id, priority_id, state_id, create_time, create_by,"
            " change_time, change_by)"
            " VALUES ('test', 1, 1, 1, 1, 1, 1, 1, :ct, 1, :ct, 1)"
        ),
        {"ct": change_time},
    )
    await session.commit()


async def _insert_session(session: AsyncSession, session_id: str) -> None:
    await session.execute(
        text(
            "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
            " VALUES (:sid, 'UserID', '1', 0)"
        ),
        {"sid": session_id},
    )
    await session.commit()


@pytest.mark.db
async def test_preflight_fails_on_recent_history(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _reset_history_and_sessions(session)
        await _insert_ticket_history(session, datetime.now(UTC).replace(tzinfo=None))
        report = await run_preflight(session, history_watermark_minutes=15)
        assert report.history_quiet is False
        assert report.passed is False
    await engine.dispose()


@pytest.mark.db
async def test_preflight_passes_on_old_history_and_no_sessions(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _reset_history_and_sessions(session)
        old = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        await _insert_ticket_history(session, old)
        report = await run_preflight(session, history_watermark_minutes=15)
        assert report.history_quiet is True
        assert report.sessions_quiet is True
        assert report.passed is True
    await engine.dispose()


@pytest.mark.db
async def test_preflight_fails_on_active_session(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _reset_history_and_sessions(session)
        old = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        await _insert_ticket_history(session, old)
        await _insert_session(session, "sess-1")
        report = await run_preflight(session, history_watermark_minutes=15)
        assert report.sessions_quiet is False
        assert report.passed is False
    await engine.dispose()


@pytest.mark.db
async def test_enable_ownership_refuses_without_force_when_preflight_fails(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_settings_table(session)
        await _reset_history_and_sessions(session)
        await _insert_ticket_history(session, datetime.now(UTC).replace(tzinfo=None))
        with pytest.raises(OwnershipPreflightError):
            await enable_ownership(session, confirm=REQUIRED_CONFIRM_PHRASE)
        marker = await get_setting(session, KEY_OWNERSHIP_ENABLED)
        assert marker is None
    await engine.dispose()


@pytest.mark.db
async def test_enable_ownership_force_overrides_failed_preflight(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_settings_table(session)
        await _reset_history_and_sessions(session)
        await _insert_ticket_history(session, datetime.now(UTC).replace(tzinfo=None))
        report = await enable_ownership(session, confirm=REQUIRED_CONFIRM_PHRASE, force=True)
        assert report.passed is False
        marker = await get_setting(session, KEY_OWNERSHIP_ENABLED)
        assert marker == "enabled"
        enabled_at = await get_setting(session, KEY_OWNERSHIP_ENABLED_AT)
        assert enabled_at is not None
    await engine.dispose()


@pytest.mark.db
async def test_enable_ownership_succeeds_when_quiet_and_sets_both_marker_keys(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_settings_table(session)
        await _reset_history_and_sessions(session)
        old = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        await _insert_ticket_history(session, old)
        report = await enable_ownership(session, confirm=REQUIRED_CONFIRM_PHRASE)
        assert report.passed is True

        state = await get_ownership_state(session, Settings(TIQORA_SCHEMA_OWNERSHIP="1"))
        assert state.env_flag is True
        assert state.db_marker is True
        assert state.active is True
    await engine.dispose()
