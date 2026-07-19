"""Unit + DB tests for the GenericAgent executor (Phase 4b subtask 3)."""

from __future__ import annotations

import contextlib
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.settings_store import (
    KEY_GENERIC_AGENT_ENABLED,
    set_setting,
)
from tiqora.worker.generic_agent import (
    KEY_GENERIC_AGENT_ALLOW_DELETE,
    GenericAgentJob,
    is_due,
    run_generic_agent_tick,
)

# ---------------------------------------------------------------------------
# Schedule matcher (pure unit tests, no DB)
# ---------------------------------------------------------------------------


def test_is_due_matches_configured_slot() -> None:
    # Wed 2026-07-22 14:30 -> Perl wday: Sun=0..Sat=6, Wed=3
    job = GenericAgentJob(name="j", schedule_days={3}, schedule_hours={14}, schedule_minutes={30})
    assert is_due(job, datetime(2026, 7, 22, 14, 30))


def test_is_due_false_outside_slot() -> None:
    job = GenericAgentJob(name="j", schedule_days={3}, schedule_hours={14}, schedule_minutes={30})
    assert not is_due(job, datetime(2026, 7, 22, 14, 31))
    assert not is_due(job, datetime(2026, 7, 23, 14, 30))  # Thu, not Wed


def test_is_due_false_without_full_schedule() -> None:
    """A job missing any of the three schedule dimensions is manual-only."""
    job = GenericAgentJob(name="j", schedule_days={3}, schedule_hours={14})
    assert not is_due(job, datetime(2026, 7, 22, 14, 30))


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
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
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NOT NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    for stmt in ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(stmt))
    await session.commit()


async def _insert_ticket(
    session: AsyncSession, tn: str, *, state_id: int = 1, queue_id: int = 1
) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, title, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, :qid, 1, 1, 1, 3, :sid, 0, 0, 0, 0, 0, 0, 0,"
            " 'GA Ticket', current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "qid": queue_id, "sid": state_id},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _insert_queue(session: AsyncSession, name: str) -> int:
    await session.execute(
        text(
            "INSERT INTO queue (name, group_id, unlock_timeout, system_address_id,"
            " salutation_id, signature_id, follow_up_id, follow_up_lock, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:name, 1, 0, 1, 1, 1, 1, 0, 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"name": name},
    )
    row = (
        await session.execute(text("SELECT id FROM queue WHERE name = :name"), {"name": name})
    ).first()
    assert row is not None
    return int(row[0])


async def _insert_job_row(session: AsyncSession, job_name: str, key: str, value: str) -> None:
    await session.execute(
        text("INSERT INTO generic_agent_jobs (job_name, job_key, job_value) VALUES (:jn, :k, :v)"),
        {"jn": job_name, "k": key, "v": value},
    )


async def _seed_always_due_job(
    session: AsyncSession, job_name: str, *, state_id: int, actions: dict[str, str]
) -> None:
    """Seed a job with a schedule matching every minute of every day (so
    run_generic_agent_tick's is_due() check always passes regardless of when
    the test runs) plus a StateIDs criterion and the given New* actions."""
    for day in range(7):
        await _insert_job_row(session, job_name, "ScheduleDays", str(day))
    for hour in range(24):
        await _insert_job_row(session, job_name, "ScheduleHours", str(hour))
    for minute in range(60):
        await _insert_job_row(session, job_name, "ScheduleMinutes", str(minute))
    await _insert_job_row(session, job_name, "StateIDs", str(state_id))
    for key, value in actions.items():
        await _insert_job_row(session, job_name, f"New{key}", value)


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


@pytest.mark.db
async def test_run_generic_agent_tick_selects_and_acts_on_matching_tickets(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            # Dedicated source/target queues: Znuny's seed data ships a demo
            # ticket in the default queue, so scoping to a fresh queue (via a
            # QueueIDs criterion below) is required for an exact match count.
            source_queue_id = await _insert_queue(session, "ga-source-queue")
            target_queue_id = await _insert_queue(session, "ga-target-queue")

            matching_id = await _insert_ticket(
                session, "GA_MATCH", state_id=1, queue_id=source_queue_id
            )
            non_matching_id = await _insert_ticket(
                session, "GA_NOMATCH", state_id=2, queue_id=source_queue_id
            )

            await _seed_always_due_job(
                session,
                "ga-move-and-note",
                state_id=1,
                actions={
                    "QueueID": str(target_queue_id),
                    "NoteBody": "moved by generic agent",
                    "NoteSubject": "GA note",
                },
            )
            await _insert_job_row(session, "ga-move-and-note", "QueueIDs", str(source_queue_id))
            await session.commit()
            await set_setting(session, KEY_GENERIC_AGENT_ENABLED, "1")

        result = await run_generic_agent_tick(session_factory=factory)
        assert result["jobs"] == 1
        assert result["matched"] == 1
        assert result["acted"] == 1

        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": matching_id}
                )
            ).first()
            assert row is not None
            assert int(row[0]) == target_queue_id

            other_row = (
                await session.execute(
                    text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": non_matching_id}
                )
            ).first()
            assert other_row is not None
            assert int(other_row[0]) == source_queue_id  # untouched

            assert await _history_count(session, matching_id, "AddNote") == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_run_generic_agent_tick_disabled_by_default(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_GENERIC_AGENT_ENABLED, "0")
        result = await run_generic_agent_tick(session_factory=factory)
        assert result == {"enabled": 0}
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_delete_action_blocked_without_safety_flag(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            queue_id = await _insert_queue(session, "ga-delete-guard-queue")
            ticket_id = await _insert_ticket(
                session, "GA_DELETE_GUARD", state_id=1, queue_id=queue_id
            )
            await _seed_always_due_job(
                session, "ga-delete-job", state_id=1, actions={"Delete": "1"}
            )
            await _insert_job_row(session, "ga-delete-job", "QueueIDs", str(queue_id))
            await session.commit()
            await set_setting(session, KEY_GENERIC_AGENT_ENABLED, "1")
            await set_setting(session, KEY_GENERIC_AGENT_ALLOW_DELETE, "0")

        result = await run_generic_agent_tick(session_factory=factory)
        assert result["matched"] == 1
        assert result["acted"] == 0  # delete blocked -> no action applied

        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
                )
            ).first()
            assert row is not None  # ticket still exists
    finally:
        await engine.dispose()
