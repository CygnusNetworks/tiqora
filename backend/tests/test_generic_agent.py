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
    build_ticket_query,
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
# build_ticket_query (pure unit tests, no DB)
# ---------------------------------------------------------------------------


def test_build_query_close_time_point_before() -> None:
    """Znuny Archive-job shape: CloseTime as TimePoint '1 year Before'."""
    built = build_ticket_query(
        {
            "StateIDs": ["2", "3"],
            "CloseTimeSearchType": ["TimePoint"],
            "TicketCloseTimePoint": ["1"],
            "TicketCloseTimePointFormat": ["year"],
            "TicketCloseTimePointStart": ["Before"],
        }
    )
    assert built is not None
    where_sql, params = built
    assert "EXISTS" in where_sql
    assert "ticket_history" in where_sql
    assert 525600 in params.values()  # 1 year in minutes


def test_build_query_time_point_inactive_without_search_type() -> None:
    """TimePoint keys with an empty/absent *SearchType MUST NOT filter.

    The real Znuny Archive job stores TicketChangeTimePoint=1/day rows while
    ChangeTimeSearchType is empty — only CloseTime is active.
    """
    built = build_ticket_query(
        {
            "StateIDs": ["2"],
            "ChangeTimeSearchType": [""],
            "TicketChangeTimePoint": ["1"],
            "TicketChangeTimePointFormat": ["day"],
            "TicketChangeTimePointStart": ["Last"],
        }
    )
    assert built is not None
    where_sql, _ = built
    assert "change_time" not in where_sql


def test_build_query_change_time_point_last() -> None:
    built = build_ticket_query(
        {
            "StateIDs": ["2"],
            "ChangeTimeSearchType": ["TimePoint"],
            "TicketChangeTimePoint": ["2"],
            "TicketChangeTimePointFormat": ["day"],
            "TicketChangeTimePointStart": ["Last"],
        }
    )
    assert built is not None
    where_sql, params = built
    assert "change_time >= DATE_SUB" in where_sql
    assert 2880 in params.values()


def test_build_query_defaults_to_not_archived() -> None:
    built = build_ticket_query({"StateIDs": ["2"]})
    assert built is not None
    where_sql, _ = built
    assert "archive_flag = 0" in where_sql


def test_build_query_archive_flag_variants() -> None:
    built = build_ticket_query({"StateIDs": ["2"], "SearchInArchive": ["ArchivedTickets"]})
    assert built is not None
    assert "archive_flag = 1" in built[0]

    built = build_ticket_query({"StateIDs": ["2"], "SearchInArchive": ["AllTickets"]})
    assert built is not None
    assert "archive_flag" not in built[0]


def test_build_query_archive_clause_does_not_satisfy_criteria_guard() -> None:
    """The implicit archive_flag clause must not let a criteria-less job run."""
    assert build_ticket_query({}) is None
    assert build_ticket_query({"SearchInArchive": ["NotArchivedTickets"]}) is None


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
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
    ]
    for stmt in ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(stmt))
    await session.commit()


async def _insert_ticket(
    session: AsyncSession,
    tn: str,
    *,
    state_id: int = 1,
    queue_id: int = 1,
    archive_flag: int = 0,
) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, title, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, :qid, 1, 1, 1, 3, :sid, 0, 0, 0, 0, 0, 0, :af,"
            " 'GA Ticket', current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "qid": queue_id, "sid": state_id, "af": archive_flag},
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


async def _insert_close_history(
    session: AsyncSession, ticket_id: int, *, state_id: int, days_ago: int
) -> None:
    """Seed the StateUpdate history row Znuny's CloseTime search derives from."""
    await session.execute(
        text(
            "INSERT INTO ticket_history (name, history_type_id, ticket_id, article_id,"
            " type_id, queue_id, owner_id, priority_id, state_id,"
            " create_time, create_by, change_time, change_by)"
            " SELECT '%%closed', ht.id, :tid, NULL, 1, 1, 1, 3, :sid,"
            " DATE_SUB(current_timestamp, INTERVAL :days DAY), 1,"
            " DATE_SUB(current_timestamp, INTERVAL :days DAY), 1"
            " FROM ticket_history_type ht WHERE ht.name = 'StateUpdate'"
        ),
        {"tid": ticket_id, "sid": state_id, "days": days_ago},
    )


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


@pytest.mark.db
async def test_archive_job_replays_znuny_archive_semantics(mariadb_znuny_url: str) -> None:
    """Replay of the production Znuny "Archive" job: NewArchiveFlag=y on tickets
    closed more than 1 year ago (CloseTime TimePoint), scoped to a queue."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            # Earlier tests in this module leave their always-due jobs behind;
            # clear them so this tick runs exactly one job.
            await session.execute(text("DELETE FROM generic_agent_jobs"))
            queue_id = await _insert_queue(session, "ga-archive-queue")

            old_closed_id = await _insert_ticket(
                session, "GA_ARCH_OLD", state_id=2, queue_id=queue_id
            )
            await _insert_close_history(session, old_closed_id, state_id=2, days_ago=400)

            fresh_closed_id = await _insert_ticket(
                session, "GA_ARCH_FRESH", state_id=2, queue_id=queue_id
            )
            await _insert_close_history(session, fresh_closed_id, state_id=2, days_ago=10)

            already_archived_id = await _insert_ticket(
                session, "GA_ARCH_DONE", state_id=2, queue_id=queue_id, archive_flag=1
            )
            await _insert_close_history(session, already_archived_id, state_id=2, days_ago=400)

            await _seed_always_due_job(
                session, "ga-archive-job", state_id=2, actions={"ArchiveFlag": "y"}
            )
            await _insert_job_row(session, "ga-archive-job", "QueueIDs", str(queue_id))
            # Same key/value shape as the production job rows:
            await _insert_job_row(session, "ga-archive-job", "CloseTimeSearchType", "TimePoint")
            await _insert_job_row(session, "ga-archive-job", "TicketCloseTimePoint", "1")
            await _insert_job_row(session, "ga-archive-job", "TicketCloseTimePointFormat", "year")
            await _insert_job_row(session, "ga-archive-job", "TicketCloseTimePointStart", "Before")
            await _insert_job_row(
                session, "ga-archive-job", "SearchInArchive", "NotArchivedTickets"
            )
            # Inactive TimePoint rows the real job also carries — must not filter:
            await _insert_job_row(session, "ga-archive-job", "ChangeTimeSearchType", "")
            await _insert_job_row(session, "ga-archive-job", "TicketChangeTimePoint", "1")
            await _insert_job_row(session, "ga-archive-job", "TicketChangeTimePointFormat", "day")
            await _insert_job_row(session, "ga-archive-job", "TicketChangeTimePointStart", "Last")
            await session.commit()
            await set_setting(session, KEY_GENERIC_AGENT_ENABLED, "1")

        result = await run_generic_agent_tick(session_factory=factory)
        assert result["jobs"] == 1
        assert result["matched"] == 1  # only the old closed, unarchived ticket
        assert result["acted"] == 1

        async with factory() as session:
            flags = {}
            for tid in (old_closed_id, fresh_closed_id, already_archived_id):
                row = (
                    await session.execute(
                        text("SELECT archive_flag FROM ticket WHERE id = :tid"), {"tid": tid}
                    )
                ).first()
                assert row is not None
                flags[tid] = int(row[0])
            assert flags[old_closed_id] == 1
            assert flags[fresh_closed_id] == 0
            assert flags[already_archived_id] == 1

            assert await _history_count(session, old_closed_id, "ArchiveFlagUpdate") == 1
            outbox = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM tiqora_event_outbox"
                        " WHERE ticket_id = :tid AND event_type = 'TicketArchiveFlagUpdate'"
                    ),
                    {"tid": old_closed_id},
                )
            ).first()
            assert outbox is not None and int(outbox[0]) == 1

        # Second run: nothing left to archive (SearchInArchive default excludes
        # archived tickets — no endless re-archiving).
        result2 = await run_generic_agent_tick(session_factory=factory)
        assert result2["matched"] == 0
    finally:
        await engine.dispose()
