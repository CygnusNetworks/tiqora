"""Unit and DB tests for the escalation index builder."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.escalation import destination_time_epoch, escalation_index_build
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

# Fixture calendar: Mon-Fri hours 8..16 (i.e. 08:00-17:00, 9 working hours/day),
# vacation on Jan 1 (recurring).
_WORKING_HOURS: dict[str, list[int]] = {
    "Mon": list(range(8, 17)),
    "Tue": list(range(8, 17)),
    "Wed": list(range(8, 17)),
    "Thu": list(range(8, 17)),
    "Fri": list(range(8, 17)),
    "Sat": [],
    "Sun": [],
}
_VACATION_DAYS: dict[int, dict[int, str]] = {1: {1: "New Year's Day"}}
_VACATION_ONCE: dict[int, dict[int, dict[int, str]]] = {}


def _epoch(dt_str: str) -> int:
    return int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())


def _dest(start: str, minutes: int) -> int:
    return destination_time_epoch(
        _epoch(start), minutes, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )


def test_add_60_minutes_within_working_hours() -> None:
    # Mon 2026-07-20 08:00 + 60 working minutes = 09:00
    assert _dest("2026-07-20 08:00:00", 60) == _epoch("2026-07-20 09:00:00")


def test_add_minutes_spanning_end_of_working_day() -> None:
    # Mon 16:00 + 120m: 60m to 17:00 (end of day), 60m next day from 08:00 → Tue 09:00
    assert _dest("2026-07-20 16:00:00", 120) == _epoch("2026-07-21 09:00:00")


def test_add_minutes_skips_weekend() -> None:
    # Fri 2026-07-24 16:30 + 60m: 30m to 17:00, weekend skipped, Mon 08:00 + 30m
    assert _dest("2026-07-24 16:30:00", 60) == _epoch("2026-07-27 08:30:00")


def test_add_minutes_skips_vacation_day() -> None:
    # Wed 2025-12-31 16:00 + 120m: 60m to 17:00; Thu Jan 1 is vacation;
    # Fri 2026-01-02 08:00 + 60m = 09:00
    assert _dest("2025-12-31 16:00:00", 120) == _epoch("2026-01-02 09:00:00")


def test_start_before_working_hours_advances_to_first_slot() -> None:
    # Mon 06:00 + 30m: working time starts 08:00 → 08:30
    assert _dest("2026-07-20 06:00:00", 30) == _epoch("2026-07-20 08:30:00")


def test_start_mid_hour_partial_consumption() -> None:
    # Mon 08:45 + 30m = 09:15
    assert _dest("2026-07-20 08:45:00", 30) == _epoch("2026-07-20 09:15:00")


def test_multi_day_accumulation() -> None:
    # Mon 08:00 + 3 working days (27h = 1620m): Mon 9h, Tue 9h, Wed 9h → Wed 17:00
    assert _dest("2026-07-20 08:00:00", 27 * 60) == _epoch("2026-07-22 17:00:00")


def test_zero_minutes_returns_start() -> None:
    start = _epoch("2026-07-20 08:00:00")
    assert (
        destination_time_epoch(start, 0, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC")
        == start
    )


def test_no_working_hours_returns_start() -> None:
    # Znuny: no configured working hours → Add() returns without changing time.
    start = _epoch("2026-07-20 08:00:00")
    empty: dict[str, list[int]] = {d: [] for d in _WORKING_HOURS}
    assert destination_time_epoch(start, 60, empty, {}, {}, "UTC") == start


# ---------------------------------------------------------------------------
# DB tests
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


_SYSCONFIG_FIXTURE: dict[str, Any] = {
    "OTRSTimeZone": yaml_encode_effective("UTC"),
    "TimeWorkingHours": yaml_encode_effective(
        {d: [str(h) for h in hours] for d, hours in _WORKING_HOURS.items()}
    ),
    "TimeVacationDays": yaml_encode_effective({1: {1: "New Year's Day"}}),
    "TimeVacationDaysOneTime": yaml_encode_effective({}),
}


def _fixture_sysconfig() -> SysConfig:
    async def fetch(name: str) -> Any | None:
        return _SYSCONFIG_FIXTURE.get(name)

    return SysConfig(fetch=fetch)


async def _insert_ticket(session: AsyncSession, tn: str, state_id: int, queue_id: int = 1) -> int:
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


async def _escalation_columns(session: AsyncSession, ticket_id: int) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                "SELECT escalation_time, escalation_response_time,"
                " escalation_update_time, escalation_solution_time"
                " FROM ticket WHERE id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    assert row is not None
    return {
        "escalation_time": int(row[0]),
        "response": int(row[1]),
        "update": int(row[2]),
        "solution": int(row[3]),
    }


@pytest.mark.db
async def test_escalation_columns_set_from_queue_config(mariadb_znuny_url: str) -> None:
    """Open ticket in a queue with first_response/solution times → columns set."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _fixture_sysconfig()
    try:
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE queue SET first_response_time = 60, solution_time = 240,"
                    " update_time = 120 WHERE id = 1"
                )
            )
            # state 1 = 'new' (state type 'new', not closed/merged/removed)
            ticket_id = await _insert_ticket(session, "ESC_TEST_OPEN", state_id=1)
            await session.commit()

            await escalation_index_build(session, ticket_id, 1, sysconfig)
            await session.commit()

            cols = await _escalation_columns(session, ticket_id)
            # First response and solution set; update stays 0 (no articles yet)
            assert cols["response"] > 0
            assert cols["solution"] > cols["response"]
            assert cols["update"] == 0
            # escalation_time = min of the set ones = response time
            assert cols["escalation_time"] == cols["response"]
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_escalation_zeroed_for_closed_ticket(mariadb_znuny_url: str) -> None:
    """Closed ticket (state type ^close) → all four escalation columns zeroed."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _fixture_sysconfig()
    try:
        async with factory() as session:
            # find a state whose type is 'closed' (seed: 'closed successful')
            state_row = (
                await session.execute(
                    text(
                        "SELECT ts.id FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE tst.name = 'closed' LIMIT 1"
                    )
                )
            ).first()
            assert state_row is not None
            closed_state_id = int(state_row[0])

            ticket_id = await _insert_ticket(session, "ESC_TEST_CLOSED", state_id=closed_state_id)
            # Pre-set non-zero escalation columns so the zeroing is observable
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_time = 123, escalation_response_time = 123,"
                    " escalation_update_time = 123, escalation_solution_time = 123"
                    " WHERE id = :tid"
                ),
                {"tid": ticket_id},
            )
            await session.commit()

            await escalation_index_build(session, ticket_id, 1, sysconfig)
            await session.commit()

            cols = await _escalation_columns(session, ticket_id)
            assert cols == {
                "escalation_time": 0,
                "response": 0,
                "update": 0,
                "solution": 0,
            }
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_escalation_update_time_from_customer_article(mariadb_znuny_url: str) -> None:
    """A visible customer article makes escalation_update_time > 0."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _fixture_sysconfig()
    try:
        async with factory() as session:
            await session.execute(text("UPDATE queue SET update_time = 120 WHERE id = 1"))
            ticket_id = await _insert_ticket(session, "ESC_TEST_UPD", state_id=1)

            st_row = (
                await session.execute(
                    text("SELECT id FROM article_sender_type WHERE name = 'customer' LIMIT 1")
                )
            ).first()
            assert st_row is not None
            cc_row = (
                await session.execute(text("SELECT id FROM communication_channel LIMIT 1"))
            ).first()
            cc_id = int(cc_row[0]) if cc_row else 1

            await session.execute(
                text(
                    "INSERT INTO article (ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:tid, :stid, :ccid, 1, 0, current_timestamp, 1,"
                    " current_timestamp, 1)"
                ),
                {"tid": ticket_id, "stid": int(st_row[0]), "ccid": cc_id},
            )
            await session.commit()

            await escalation_index_build(session, ticket_id, 1, sysconfig)
            await session.commit()

            cols = await _escalation_columns(session, ticket_id)
            assert cols["update"] > 0
    finally:
        await engine.dispose()
