from __future__ import annotations

import itertools
import time
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.settings_store import (
    KEY_PENDING_CHECK_ENABLED,
    KEY_PENDING_CHECK_REMINDER_INTERVAL_SECONDS,
    KEY_UNLOCK_TIMEOUT_ENABLED,
    set_setting,
)
from tiqora.worker.pending_check import reminder_due, run_pending_check_tick
from tiqora.worker.unlock_timeout import run_unlock_timeout_tick, working_timeout_due

WEEKDAYS_9_TO_17 = {
    "Mon": list(range(9, 17)),
    "Tue": list(range(9, 17)),
    "Wed": list(range(9, 17)),
    "Thu": list(range(9, 17)),
    "Fri": list(range(9, 17)),
}


def test_unlock_timeout_counts_only_calendar_working_time() -> None:
    # Friday 16:30 + 60 working minutes is Monday 09:30, not Friday 17:30.
    started = int(datetime(2026, 9, 4, 16, 30, tzinfo=UTC).timestamp())
    assert not working_timeout_due(
        started,
        60,
        int(datetime(2026, 9, 7, 9, 29, tzinfo=UTC).timestamp()),
        WEEKDAYS_9_TO_17,
        {},
        {},
        "UTC",
    )
    assert working_timeout_due(
        started,
        60,
        int(datetime(2026, 9, 7, 9, 30, tzinfo=UTC).timestamp()),
        WEEKDAYS_9_TO_17,
        {},
        {},
        "UTC",
    )


def test_pending_reminders_require_full_configured_elapsed_cadence() -> None:
    assert not reminder_due(last_sent_epoch=7_199, now_epoch=7_200, interval_seconds=7_200)
    assert reminder_due(last_sent_epoch=7_199, now_epoch=14_399, interval_seconds=7_200)


# ---------------------------------------------------------------------------
# DB-backed behaviour (Znuny Maint::Ticket::UnlockTimeout / PendingCheck)
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    conn = await session.connection()
    await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    await session.commit()


_SYSCONFIG_SEQ = itertools.count(90_000)


async def _set_sysconfig(session: AsyncSession, name: str, value: object) -> None:
    """Insert/replace one system-wide sysconfig_default row (YAML effective value)."""
    effective = yaml.safe_dump(value, default_flow_style=False)
    await session.execute(text("DELETE FROM sysconfig_modified WHERE name = :name"), {"name": name})
    await session.execute(text("DELETE FROM sysconfig_default WHERE name = :name"), {"name": name})
    await session.execute(
        text(
            "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
            " is_readonly, is_required, is_valid, has_configlevel, user_modification_possible,"
            " user_modification_active, xml_content_raw, xml_content_parsed, xml_filename,"
            " effective_value, is_dirty, exclusive_lock_guid, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:id, :name, :name, 'Core', 0, 0, 0, 1, 0, 0, 0, :name, :name,"
            " 'Ticket.xml', :eff, 0, '0', current_timestamp, 1, current_timestamp, 1)"
        ),
        {"id": next(_SYSCONFIG_SEQ), "name": name, "eff": effective},
    )


async def _make_queue(session: AsyncSession, name: str, *, unlock_timeout: int) -> int:
    await session.execute(
        text(
            "INSERT INTO queue (name, group_id, unlock_timeout, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:name, 1, :ut, 1, 1, 1, 1, 0, 1, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"name": name, "ut": unlock_timeout},
    )
    row = (
        await session.execute(text("SELECT id FROM queue WHERE name = :name"), {"name": name})
    ).first()
    assert row is not None
    return int(row[0])


async def _make_ticket(
    session: AsyncSession,
    tn: str,
    *,
    queue_id: int,
    state_id: int,
    lock_id: int = 2,
    unlock_epoch: int = 0,
    until_time: int = 0,
) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time, escalation_time,"
            " escalation_update_time, escalation_response_time, escalation_solution_time,"
            " archive_flag, title, create_time, create_by, change_time, change_by)"
            " VALUES (:tn, :qid, :lid, 1, 1, 3, :sid, :to, :ut, 0, 0, 0, 0, 0,"
            " 'Maintenance worker ticket', current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "tn": tn,
            "qid": queue_id,
            "lid": lock_id,
            "sid": state_id,
            "to": unlock_epoch,
            "ut": until_time,
        },
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _lock_id_of(session: AsyncSession, ticket_id: int) -> int:
    row = (
        await session.execute(
            text("SELECT ticket_lock_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
        )
    ).first()
    assert row is not None
    return int(row[0])


async def _state_id_of(session: AsyncSession, ticket_id: int) -> int:
    row = (
        await session.execute(
            text("SELECT ticket_state_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
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
async def test_unlock_timeout_is_off_until_enabled(mariadb_znuny_url: str) -> None:
    """The takeover flag defaults OFF — a tick must not touch any ticket."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_UNLOCK_TIMEOUT_ENABLED, "0")
            queue_id = await _make_queue(session, "unlock-q-off", unlock_timeout=1)
            ticket_id = await _make_ticket(
                session, "UNLOCK_OFF", queue_id=queue_id, state_id=4, unlock_epoch=1
            )
            await session.commit()

        assert await run_unlock_timeout_tick(session_factory=factory) == {"enabled": 0}
        async with factory() as session:
            assert await _lock_id_of(session, ticket_id) == 2
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_unlock_timeout_unlocks_only_eligible_tickets(mariadb_znuny_url: str) -> None:
    """Working-time timeout, queue timeout, state type and lock all gate the unlock."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_UNLOCK_TIMEOUT_ENABLED, "1")
            # A 24/7 calendar keeps the working-time maths independent of when
            # the suite runs, so "60 minutes ago" really is 60 working minutes.
            await _set_sysconfig(
                session,
                "TimeWorkingHours",
                {day: list(range(24)) for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")},
            )
            await _set_sysconfig(session, "TimeVacationDays", {})
            await _set_sysconfig(session, "TimeVacationDaysOneTime", {})

            queue_id = await _make_queue(session, "unlock-q-on", unlock_timeout=60)
            no_timeout_queue = await _make_queue(session, "unlock-q-zero", unlock_timeout=0)

            due = await _make_ticket(
                session, "UNLOCK_DUE", queue_id=queue_id, state_id=4, unlock_epoch=now - 3600
            )
            not_due = await _make_ticket(
                session, "UNLOCK_NOTDUE", queue_id=queue_id, state_id=4, unlock_epoch=now - 60
            )
            closed = await _make_ticket(
                session, "UNLOCK_CLOSED", queue_id=queue_id, state_id=2, unlock_epoch=now - 3600
            )
            already_unlocked = await _make_ticket(
                session,
                "UNLOCK_FREE",
                queue_id=queue_id,
                state_id=4,
                lock_id=1,
                unlock_epoch=now - 3600,
            )
            no_timeout = await _make_ticket(
                session,
                "UNLOCK_NOQT",
                queue_id=no_timeout_queue,
                state_id=4,
                unlock_epoch=now - 3600,
            )
            await session.commit()

        totals = await run_unlock_timeout_tick(session_factory=factory)
        assert totals["errors"] == 0

        async with factory() as session:
            assert await _lock_id_of(session, due) == 1
            assert await _lock_id_of(session, not_due) == 2
            assert await _lock_id_of(session, closed) == 2
            assert await _lock_id_of(session, already_unlocked) == 1
            assert await _lock_id_of(session, no_timeout) == 2
            # Znuny writes an Unlock history row and Tiqora emits the legacy
            # NotificationLockTimeout event for the notification engine.
            assert await _outbox_count(session, due, "NotificationLockTimeout") == 1

        # Idempotent: the ticket is unlocked now, so a rerun is a no-op.
        again = await run_unlock_timeout_tick(session_factory=factory)
        assert again["unlocked"] == 0
        async with factory() as session:
            assert await _outbox_count(session, due, "NotificationLockTimeout") == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_unlock_timeout_respects_sla_calendar_over_queue(mariadb_znuny_url: str) -> None:
    """A ticket's SLA calendar wins over the queue/default one (TicketCalendarGet).

    The default calendar has no working hours, so its timeout elapses at once
    (``destination_time_epoch`` returns the start unchanged, as Znuny does).
    Calendar 1 is 24/7, so on it 60 minutes have demonstrably not passed yet.
    The SLA ticket therefore stays locked while its sibling is unlocked.
    """
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_UNLOCK_TIMEOUT_ENABLED, "1")
            await _set_sysconfig(session, "TimeWorkingHours", {})
            await _set_sysconfig(session, "TimeZone::Calendar1Name", "Around the clock")
            await _set_sysconfig(
                session,
                "TimeWorkingHours::Calendar1",
                {day: list(range(24)) for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")},
            )

            queue_id = await _make_queue(session, "unlock-q-sla", unlock_timeout=60)
            await session.execute(
                text(
                    "INSERT INTO sla (name, calendar_name, first_response_time,"
                    " update_time, solution_time, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES ('unlock-sla', '1', 0, 0, 0, 1, current_timestamp, 1,"
                    " current_timestamp, 1)"
                )
            )
            sla_row = (
                await session.execute(text("SELECT id FROM sla WHERE name = 'unlock-sla'"))
            ).first()
            assert sla_row is not None

            with_sla = await _make_ticket(
                session, "UNLOCK_SLA", queue_id=queue_id, state_id=4, unlock_epoch=now - 60
            )
            await session.execute(
                text("UPDATE ticket SET sla_id = :sid WHERE id = :tid"),
                {"sid": int(sla_row[0]), "tid": with_sla},
            )
            without_sla = await _make_ticket(
                session, "UNLOCK_NOSLA", queue_id=queue_id, state_id=4, unlock_epoch=now - 60
            )
            await session.commit()

        assert (await run_unlock_timeout_tick(session_factory=factory))["errors"] == 0
        async with factory() as session:
            assert await _lock_id_of(session, with_sla) == 2  # SLA calendar: not due yet
            assert await _lock_id_of(session, without_sla) == 1  # default calendar: due
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_pending_check_is_off_until_enabled(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_PENDING_CHECK_ENABLED, "0")
            await session.commit()
        assert await run_pending_check_tick(session_factory=factory) == {"enabled": 0}
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_pending_auto_transitions_and_unlocks_on_closed(mariadb_znuny_url: str) -> None:
    """Due 'pending auto' tickets follow Ticket::StateAfterPending; a closed
    target also unlocks the ticket. Tickets still pending are left alone."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_PENDING_CHECK_ENABLED, "1")
            await _set_sysconfig(
                session,
                "Ticket::StateAfterPending",
                {"pending auto close+": "closed successful"},
            )
            queue_id = await _make_queue(session, "pending-q-auto", unlock_timeout=0)
            due = await _make_ticket(
                session,
                "PEND_DUE",
                queue_id=queue_id,
                state_id=7,  # pending auto close+
                until_time=now - 60,
            )
            future = await _make_ticket(
                session,
                "PEND_FUTURE",
                queue_id=queue_id,
                state_id=7,
                until_time=now + 3600,
            )
            await session.commit()

        totals = await run_pending_check_tick(session_factory=factory)
        assert totals["transitioned"] == 1
        assert totals["errors"] == 0

        async with factory() as session:
            assert await _state_id_of(session, due) == 2  # closed successful
            assert await _lock_id_of(session, due) == 1  # closed => unlocked
            assert await _state_id_of(session, future) == 7
            assert await _lock_id_of(session, future) == 2
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_pending_reminder_fires_once_per_cadence_in_working_time(
    mariadb_znuny_url: str,
) -> None:
    """A due 'pending reminder' ticket emits NotificationPendingReminder during
    working time, and not again within the configured cadence."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_PENDING_CHECK_ENABLED, "1")
            await set_setting(session, KEY_PENDING_CHECK_REMINDER_INTERVAL_SECONDS, "7200")
            await _set_sysconfig(
                session,
                "TimeWorkingHours",
                {day: list(range(24)) for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")},
            )
            queue_id = await _make_queue(session, "pending-q-rem", unlock_timeout=0)
            ticket_id = await _make_ticket(
                session,
                "PEND_REMIND",
                queue_id=queue_id,
                state_id=6,  # pending reminder
                until_time=now - 60,
            )
            await session.commit()

        first = await run_pending_check_tick(session_factory=factory)
        assert first["reminded"] == 1
        second = await run_pending_check_tick(session_factory=factory)
        assert second["reminded"] == 0

        async with factory() as session:
            assert await _outbox_count(session, ticket_id, "NotificationPendingReminder") == 1
            # The state is untouched — reminders never transition a ticket.
            assert await _state_id_of(session, ticket_id) == 6
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_pending_reminder_skipped_outside_working_time(mariadb_znuny_url: str) -> None:
    """No working time in the last ten minutes means no reminder (upstream's
    Delta(ForWorkingTime => 1) guard)."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = int(time.time())
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await set_setting(session, KEY_PENDING_CHECK_ENABLED, "1")
            await _set_sysconfig(session, "TimeWorkingHours", {})
            queue_id = await _make_queue(session, "pending-q-off", unlock_timeout=0)
            ticket_id = await _make_ticket(
                session,
                "PEND_NOWORK",
                queue_id=queue_id,
                state_id=6,
                until_time=now - 60,
            )
            await session.commit()

        totals = await run_pending_check_tick(session_factory=factory)
        assert totals["reminded"] == 0
        async with factory() as session:
            assert await _outbox_count(session, ticket_id, "NotificationPendingReminder") == 0
    finally:
        await engine.dispose()
