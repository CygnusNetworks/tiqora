"""DB-backed tests for the escalation sweep (Phase 4b subtask 1).

Mirrors the pattern used by tests/test_escalation.py and
tests/test_postmaster_db.py: a MariaDB testcontainer loaded with the real
Znuny schema, exercising escalation.py's sweep_ticket/run_escalation_tick
against genuine ticket/ticket_history/tiqora_event_outbox rows.
"""

from __future__ import annotations

import contextlib
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.settings_store import KEY_ESCALATION_ENABLED, set_setting
from tiqora.worker import escalation as escalation_module
from tiqora.worker.escalation import run_escalation_tick, sweep_ticket
from tiqora.znuny.sysconfig import SysConfig


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    """Create tiqora_* tables in the testcontainer DB (Alembic not run there).

    Mirrors tests/test_ticket_write_service.py's helper; schema matches
    tiqora.db.tiqora.models exactly (note the ``key`` column, not
    ``key_name``, since domain.settings_store reads/writes via the ORM model).
    """
    ddl = [
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_settings (
            `key` VARCHAR(200) PRIMARY KEY,
            value TEXT
        )""",
    ]
    for stmt in ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(stmt))
    await session.commit()


async def _insert_ticket(
    session: AsyncSession, tn: str, state_id: int = 1, queue_id: int = 1
) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, :qid, 1, 1, 1, 3, :sid, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "qid": queue_id, "sid": state_id},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _history_count(session: AsyncSession, ticket_id: int, history_type: str) -> int:
    row = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid AND ht.name = :htype"
            ),
            {"tid": ticket_id, "htype": history_type},
        )
    ).first()
    assert row is not None
    return int(row[0])


async def _outbox_count(session: AsyncSession, ticket_id: int, event_type: str) -> int:
    row = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM tiqora_event_outbox"
                " WHERE ticket_id = :tid AND event_type = :et"
            ),
            {"tid": ticket_id, "et": event_type},
        )
    ).first()
    assert row is not None
    return int(row[0])


@pytest.mark.db
async def test_sweep_fires_start_event_once_and_is_idempotent(mariadb_znuny_url: str) -> None:
    """Ticket crossing into escalation → Start event/history exactly once, rerun is a no-op."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await session.execute(
                text(
                    "UPDATE queue SET first_response_time = 60, solution_time = 0,"
                    " update_time = 0 WHERE id = 1"
                )
            )
            ticket_id = await _insert_ticket(session, "ESC_SWEEP_START", state_id=1)
            await session.commit()

            sysconfig = SysConfig(session)
            fired1 = await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=86400
            )
            await session.commit()
            assert fired1["start"] == 1
            assert fired1["stop"] == 0

            assert await _history_count(session, ticket_id, "EscalationResponseTimeStart") == 1
            assert await _outbox_count(session, ticket_id, "EscalationResponseTimeStart") == 1

            # Rerun: no state change → no new Start row (idempotent).
            fired2 = await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=86400
            )
            await session.commit()
            assert fired2["start"] == 0
            assert fired2["stop"] == 0
            assert await _history_count(session, ticket_id, "EscalationResponseTimeStart") == 1
            assert await _outbox_count(session, ticket_id, "EscalationResponseTimeStart") == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_sweep_fires_stop_event_when_ticket_closes(mariadb_znuny_url: str) -> None:
    """Escalated ticket moved to a closed state → columns zeroed, Stop event fired once."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await session.execute(
                text(
                    "UPDATE queue SET first_response_time = 60, solution_time = 0,"
                    " update_time = 0 WHERE id = 1"
                )
            )
            ticket_id = await _insert_ticket(session, "ESC_SWEEP_STOP", state_id=1)
            await session.commit()

            sysconfig = SysConfig(session)
            await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=86400
            )
            await session.commit()

            closed_row = (
                await session.execute(
                    text(
                        "SELECT ts.id FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE tst.name = 'closed' LIMIT 1"
                    )
                )
            ).first()
            assert closed_row is not None
            await session.execute(
                text("UPDATE ticket SET ticket_state_id = :sid WHERE id = :tid"),
                {"sid": int(closed_row[0]), "tid": ticket_id},
            )
            await session.commit()

            fired = await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=86400
            )
            await session.commit()
            assert fired["stop"] == 1
            assert await _history_count(session, ticket_id, "EscalationResponseTimeStop") == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_sweep_notify_before_dedupes_on_exact_destination(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NotifyBefore fires once per destination-time value, not once per tick."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _noop_index_build(
        session: AsyncSession, ticket_id: int, user_id: int, sysconfig: object
    ) -> None:
        return None

    monkeypatch.setattr(escalation_module, "escalation_index_build", _noop_index_build)

    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            ticket_id = await _insert_ticket(session, "ESC_SWEEP_NOTIFY", state_id=1)
            future_epoch = int(time.time()) + 3600
            await session.execute(
                text("UPDATE ticket SET escalation_response_time = :ts WHERE id = :tid"),
                {"ts": future_epoch, "tid": ticket_id},
            )
            await session.commit()

            sysconfig = SysConfig(session)
            fired1 = await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=7200
            )
            await session.commit()
            assert fired1["notify_before"] == 1
            assert (
                await _history_count(session, ticket_id, "EscalationResponseTimeNotifyBefore") == 1
            )

            fired2 = await sweep_ticket(
                session, sysconfig, ticket_id, user_id=1, notify_before_seconds=7200
            )
            await session.commit()
            assert fired2["notify_before"] == 0
            assert (
                await _history_count(session, ticket_id, "EscalationResponseTimeNotifyBefore") == 1
            )
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_run_escalation_tick_disabled_by_default(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
        result = await run_escalation_tick(session_factory=factory)
        assert result == {"enabled": 0}
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_run_escalation_tick_sweeps_when_enabled(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await session.execute(
                text(
                    "UPDATE queue SET first_response_time = 60, solution_time = 0,"
                    " update_time = 0 WHERE id = 1"
                )
            )
            await _insert_ticket(session, "ESC_TICK_ENABLED", state_id=1)
            await session.commit()
            await set_setting(session, KEY_ESCALATION_ENABLED, "1")

        result = await run_escalation_tick(session_factory=factory)
        assert result["swept"] >= 1
        assert result["start"] >= 1
    finally:
        await engine.dispose()
