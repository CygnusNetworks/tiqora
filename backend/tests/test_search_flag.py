"""Tests for search_flag helpers (md5 + rebuild flag)."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.znuny.search_flag import mark_search_rebuild, message_id_md5


def test_message_id_md5_known_vector() -> None:
    msg_id = "<test@example.com>"
    expected = hashlib.md5(msg_id.encode("utf-8")).hexdigest()  # noqa: S324
    assert message_id_md5(msg_id) == expected
    assert len(message_id_md5(msg_id)) == 32


def test_message_id_md5_utf8_octets() -> None:
    # Znuny MD5sum encodes to UTF-8 octets before digesting
    msg_id = "<gruß@beispiel.de>"
    assert message_id_md5(msg_id) == hashlib.md5(msg_id.encode("utf-8")).hexdigest()  # noqa: S324


def test_message_id_md5_empty_string() -> None:
    assert message_id_md5("") == hashlib.md5(b"").hexdigest()  # noqa: S324


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


@pytest.mark.db
async def test_mark_search_rebuild(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id,"
                    " responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES ('SF_TEST_1', 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
                    " current_timestamp, 1, current_timestamp, 1)"
                )
            )
            t_row = (
                await session.execute(text("SELECT id FROM ticket WHERE tn = 'SF_TEST_1'"))
            ).first()
            assert t_row is not None
            ticket_id = int(t_row[0])

            st_row = (
                await session.execute(
                    text("SELECT id FROM article_sender_type WHERE name = 'agent' LIMIT 1")
                )
            ).first()
            cc_row = (
                await session.execute(text("SELECT id FROM communication_channel LIMIT 1"))
            ).first()
            st_id = int(st_row[0]) if st_row else 1
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
                {"tid": ticket_id, "stid": st_id, "ccid": cc_id},
            )
            a_row = (
                await session.execute(
                    text(
                        "SELECT id, search_index_needs_rebuild FROM article"
                        " WHERE ticket_id = :tid ORDER BY id DESC LIMIT 1"
                    ),
                    {"tid": ticket_id},
                )
            ).first()
            assert a_row is not None
            article_id = int(a_row[0])
            assert int(a_row[1]) == 0
            await session.commit()

            await mark_search_rebuild(session, article_id)
            await session.commit()

            flag_row = (
                await session.execute(
                    text("SELECT search_index_needs_rebuild FROM article WHERE id = :aid"),
                    {"aid": article_id},
                )
            ).first()
            assert flag_row is not None
            assert int(flag_row[0]) == 1
    finally:
        await engine.dispose()
