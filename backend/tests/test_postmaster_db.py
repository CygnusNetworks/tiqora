"""DB-backed tests for the postmaster pipeline building blocks.

Uses the shared MariaDB testcontainer fixture (mirrors tests/test_escalation.py
and tests/test_search_flag.py). Covers: postmaster_filter matching incl.
negation/stop, ticket_loop_protection day-counter, and the TicketCheckNumber
merge-chain walk / References lookup used by follow-up detection.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email import loop_protection
from tiqora.channels.email.filters import apply_filters
from tiqora.znuny.followup import find_ticket_by_references, ticket_check_number
from tiqora.znuny.history import add_merged


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _pg_async(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _insert_ticket(session: AsyncSession, tn: str, state_id: int = 1) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, 1, 1, 1, 1, 3, :sid, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "sid": state_id},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# postmaster_filter
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_filter_match_sets_header(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('spam-to-junk', 0, 'Match', 'Subject', 'SPAM', 0)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('spam-to-junk', 0, 'Set', 'X-OTRS-Queue', 'Junk', 0)"
                )
            )
            await session.commit()

            get_param = {"Subject": "Buy now [SPAM]"}
            await apply_filters(session, get_param)
            assert get_param["X-OTRS-Queue"] == "Junk"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_filter_negation_and_no_match_skips(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # f_not=1: matches when Subject does NOT contain "internal".
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('not-internal', 0, 'Match', 'Subject', 'internal', 1)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('not-internal', 0, 'Set', 'X-OTRS-Priority', '5 very high', 0)"
                )
            )
            await session.commit()

            external = {"Subject": "External customer request"}
            await apply_filters(session, external)
            assert external.get("X-OTRS-Priority") == "5 very high"

            internal = {"Subject": "internal memo"}
            await apply_filters(session, internal)
            assert "X-OTRS-Priority" not in internal
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_filter_stop_after_match_halts_later_filters(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('a-first', 1, 'Match', 'Subject', 'stopme', 0)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('a-first', 1, 'Set', 'X-OTRS-Queue', 'First', 0)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('b-second', 0, 'Match', 'Subject', 'stopme', 0)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('b-second', 0, 'Set', 'X-OTRS-Queue', 'Second', 0)"
                )
            )
            await session.commit()

            get_param = {"Subject": "please stopme here"}
            await apply_filters(session, get_param)
            # 'a-first' sorts before 'b-second' and has StopAfterMatch.
            assert get_param["X-OTRS-Queue"] == "First"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# loop protection
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_loop_protection_check_and_record(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM ticket_loop_protection"))
            await session.commit()

            to = "loop-test@example.com"
            assert await loop_protection.check(session, to=to, max_emails=2)
            await loop_protection.record(session, to=to)
            await session.commit()

            assert await loop_protection.check(session, to=to, max_emails=2)
            await loop_protection.record(session, to=to)
            await session.commit()

            # Third attempt with max_emails=2 should be blocked.
            assert not await loop_protection.check(session, to=to, max_emails=2)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_loop_protection_per_address_override(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM ticket_loop_protection"))
            await session.commit()

            to = "override@example.com"
            await loop_protection.record(session, to=to)
            await session.commit()

            # Default max is high, but a per-address override of 1 blocks the 2nd send.
            assert not await loop_protection.check(
                session, to=to, max_emails=40, max_emails_per_address={to: 1}
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# follow-up: merge chain + references
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_ticket_check_number_walks_merge_chain(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Znuny's Merged history regex (%%..%%<digits>$) requires numeric TNs.
            main_tn = "20260719101234"
            merge_tn = "20260719101235"
            main_id = await _insert_ticket(session, main_tn)
            merged_id = await _insert_ticket(session, merge_tn)

            merged_state_row = (
                await session.execute(
                    text(
                        "SELECT ts.id FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE tst.name = 'merged' LIMIT 1"
                    )
                )
            ).first()
            assert merged_state_row is not None
            await session.execute(
                text("UPDATE ticket SET ticket_state_id = :sid WHERE id = :tid"),
                {"sid": int(merged_state_row[0]), "tid": merged_id},
            )
            await add_merged(
                session,
                ticket_id=merged_id,
                main_tn=main_tn,
                main_ticket_id=main_id,
                merge_tn=merge_tn,
                merge_ticket_id=merged_id,
                user_id=1,
            )
            await session.commit()

            resolved = await ticket_check_number(session, merge_tn)
            assert resolved == main_id
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_ticket_check_number_open_ticket_no_walk(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ticket_id = await _insert_ticket(session, "OPEN_TN_TEST")
            await session.commit()
            resolved = await ticket_check_number(session, "OPEN_TN_TEST")
            assert resolved == ticket_id
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_find_ticket_by_references(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ticket_id = await _insert_ticket(session, "REF_TEST")
            await session.execute(
                text(
                    "INSERT INTO article (ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, insert_fingerprint, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:tid, 2, 1, 1, 0, 'ref-fp-1', current_timestamp, 1,"
                    " current_timestamp, 1)"
                ),
                {"tid": ticket_id},
            )
            art_row = (
                await session.execute(
                    text("SELECT id FROM article WHERE insert_fingerprint = 'ref-fp-1'")
                )
            ).first()
            assert art_row is not None
            article_id = int(art_row[0])
            await session.execute(
                text(
                    "INSERT INTO article_data_mime (article_id, a_message_id, a_body,"
                    " a_content_type, incoming_time, create_time, create_by, change_time,"
                    " change_by)"
                    " VALUES (:aid, '<orig@example.com>', 'body', 'text/plain', 0,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"aid": article_id},
            )
            await session.commit()

            found = await find_ticket_by_references(session, ["orig@example.com"])
            assert found == ticket_id

            not_found = await find_ticket_by_references(session, ["nonexistent@example.com"])
            assert not_found is None
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Postgres smoke test — same building blocks, other dialect (dual-dialect
# parity is a repo-wide Phase 2 exit criterion; other test_*.py files follow
# the same "one combined dialect-parity test" convention for db-touching code).
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_postmaster_building_blocks_on_postgres(postgres_znuny_url: str) -> None:
    engine = create_async_engine(_pg_async(postgres_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Filters: match + Set.
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('pg-spam', 0, 'Match', 'Subject', 'SPAM', 0)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES ('pg-spam', 0, 'Set', 'X-OTRS-Queue', 'Junk', 0)"
                )
            )
            await session.commit()
            get_param = {"Subject": "Buy now [SPAM]"}
            await apply_filters(session, get_param)
            assert get_param["X-OTRS-Queue"] == "Junk"

            # Loop protection: day-counter accumulates and blocks past the max.
            await session.execute(text("DELETE FROM ticket_loop_protection"))
            await session.commit()
            to = "pg-loop@example.com"
            assert await loop_protection.check(session, to=to, max_emails=1)
            await loop_protection.record(session, to=to)
            await session.commit()
            assert not await loop_protection.check(session, to=to, max_emails=1)

            # Follow-up merge chain.
            main_tn, merge_tn = "20260719201234", "20260719201235"
            main_id = await _insert_ticket(session, main_tn)
            merged_id = await _insert_ticket(session, merge_tn)
            merged_state_row = (
                await session.execute(
                    text(
                        "SELECT ts.id FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE tst.name = 'merged' LIMIT 1"
                    )
                )
            ).first()
            assert merged_state_row is not None
            await session.execute(
                text("UPDATE ticket SET ticket_state_id = :sid WHERE id = :tid"),
                {"sid": int(merged_state_row[0]), "tid": merged_id},
            )
            await add_merged(
                session,
                ticket_id=merged_id,
                main_tn=main_tn,
                main_ticket_id=main_id,
                merge_tn=merge_tn,
                merge_ticket_id=merged_id,
                user_id=1,
            )
            await session.commit()
            resolved = await ticket_check_number(session, merge_tn)
            assert resolved == main_id
    finally:
        await engine.dispose()
