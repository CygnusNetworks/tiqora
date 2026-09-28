"""Tests for tiqora.channels.telegram.messages: the article<->Telegram map table.

Own id range: 97_500_00x for direct ``tiqora_telegram_message`` rows (no real
article needed -- ``article_id`` has no FK, mirrors ``tiqora_ai_article_origin``),
97_590-97_593 for the seeded ticket/queue/group/user used by the
``delete_article`` cleanup test. Distinct from other DB test modules' ranges
(see the comment in test_channels_telegram.py and 887xx in
test_article_delete_db.py). New module -- cleans up its own rows so it never
needs tests/db_leak_baseline.txt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.telegram.messages import (
    ButtonSpec,
    buttons_from_json,
    get_by_article,
    get_by_message,
    keyboard_for,
    list_for_ticket,
    record_message,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import ArticleIn, TicketWriteService, delete_article
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 1, 1, 12, 0, 0)

ARTICLE_ID = 97_500_001
CHAT_ID = 9701
MESSAGE_ID = 55

QUEUE_ID = 97_590
GROUP_ID = 97_591
TICKET_ID = 97_592
AGENT_ID = 97_593


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _cleanup_direct_rows(sync_url: str) -> None:
    """Remove ``tiqora_telegram_message`` rows keyed by the direct ARTICLE_ID range."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM tiqora_telegram_message WHERE article_id >= :lo AND article_id < :hi"
            ),
            {"lo": 97_500_000, "hi": 97_500_100},
        )
    engine.dispose()


def _seed_ticket(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our fixed id block (shared session-scoped DB).
        conn.execute(text(f"DELETE FROM tiqora_telegram_message WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}"))
        conn.execute(
            text(
                "DELETE FROM article_data_mime WHERE article_id IN"
                f" (SELECT id FROM article WHERE ticket_id = {TICKET_ID})"
            )
        )
        conn.execute(
            text(
                "DELETE FROM article_flag WHERE article_id IN"
                f" (SELECT id FROM article WHERE ticket_id = {TICKET_ID})"
            )
        )
        conn.execute(text(f"DELETE FROM article WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM time_accounting WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM ticket WHERE id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM queue WHERE id = {QUEUE_ID}"))
        conn.execute(
            text(f"DELETE FROM group_user WHERE user_id = {AGENT_ID} OR group_id = {GROUP_ID}")
        )
        conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP_ID}"))
        conn.execute(text(f"DELETE FROM users WHERE id = {AGENT_ID}"))

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'tg.map.agent', :pw, 'Tg', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"pw": pw, "t": NOW, "uid": AGENT_ID},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'tg-map-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        for key in ("ro", "rw", "create", "note"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": AGENT_ID, "gid": GROUP_ID, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'TgMapQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"qid": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20260101975920', 'Tg map ticket', :qid, 1, 1,"
                " :uid, 1, 3, 4, 'CUST1', 'alice@example.com',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "qid": QUEUE_ID, "uid": AGENT_ID, "t": NOW},
        )
    engine.dispose()
    return {"agent": AGENT_ID, "queue": QUEUE_ID, "group": GROUP_ID, "ticket": TICKET_ID}


def _cleanup_ticket(sync_url: str, ids: dict[str, Any]) -> None:
    engine = create_engine(sync_url)
    tid = ids["ticket"]
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM tiqora_telegram_message WHERE ticket_id = {tid}"))
        conn.execute(text(f"DELETE FROM ticket_history WHERE ticket_id = {tid}"))
        conn.execute(text(f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {tid}"))
        conn.execute(text(f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {tid}"))
        conn.execute(
            text(
                "DELETE FROM article_data_mime WHERE article_id IN"
                f" (SELECT id FROM article WHERE ticket_id = {tid})"
            )
        )
        conn.execute(
            text(
                "DELETE FROM article_flag WHERE article_id IN"
                f" (SELECT id FROM article WHERE ticket_id = {tid})"
            )
        )
        conn.execute(text(f"DELETE FROM article WHERE ticket_id = {tid}"))
        conn.execute(text(f"DELETE FROM time_accounting WHERE ticket_id = {tid}"))
        conn.execute(text(f"DELETE FROM ticket WHERE id = {tid}"))
        conn.execute(text(f"DELETE FROM queue WHERE id = {ids['queue']}"))
        conn.execute(
            text(
                f"DELETE FROM group_user WHERE user_id = {ids['agent']}"
                f" OR group_id = {ids['group']}"
            )
        )
        conn.execute(text(f"DELETE FROM permission_groups WHERE id = {ids['group']}"))
        conn.execute(text(f"DELETE FROM users WHERE id = {ids['agent']}"))
    engine.dispose()


@pytest.mark.asyncio
async def test_record_and_lookup_by_article_and_message(mariadb_znuny_url: str) -> None:
    sync_url = mariadb_znuny_url
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()

    async_engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    buttons = [ButtonSpec("Ja", "resolve_yes"), ButtonSpec("Nein", "resolve_no")]
    try:
        async with factory() as session, session.begin():
            await record_message(
                session,
                article_id=ARTICLE_ID,
                ticket_id=TICKET_ID,
                chat_id=CHAT_ID,
                message_id=MESSAGE_ID,
                direction="out",
                buttons=buttons,
            )

        async with factory() as session:
            by_article = await get_by_article(session, ARTICLE_ID)
            assert by_article is not None
            assert by_article.ticket_id == TICKET_ID
            assert by_article.chat_id == CHAT_ID
            assert by_article.message_id == MESSAGE_ID
            assert by_article.direction == "out"

            by_message = await get_by_message(session, CHAT_ID, MESSAGE_ID)
            assert by_message is not None
            assert by_message.article_id == ARTICLE_ID

            assert buttons_from_json(by_article.buttons_json) == buttons

            for_ticket = await list_for_ticket(session, TICKET_ID)
            assert [row.article_id for row in for_ticket] == [ARTICLE_ID]
    finally:
        await async_engine.dispose()
        _cleanup_direct_rows(sync_url)


def test_keyboard_for_uses_index_callback_data() -> None:
    buttons = [ButtonSpec("Ja", "resolve_yes"), ButtonSpec("Nein", "resolve_no")]
    assert keyboard_for(buttons) == {
        "inline_keyboard": [
            [{"text": "Ja", "callback_data": "tqb:0"}],
            [{"text": "Nein", "callback_data": "tqb:1"}],
        ]
    }


def test_buttons_from_json_tolerates_garbage() -> None:
    assert buttons_from_json("not json") == []
    assert buttons_from_json(None) == []
    assert buttons_from_json('[{"label": "Huh", "action": "unknown_action"}]') == [
        ButtonSpec("Huh", "reply")
    ]


@pytest.mark.asyncio
async def test_delete_article_removes_map_row(mariadb_znuny_url: str) -> None:
    sync_url = mariadb_znuny_url
    ids = _seed_ticket(sync_url)
    async_engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    try:
        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=False,
                    subject="Test note",
                    body="internal note body",
                    channel="note",
                ),
            )

        async with factory() as session, session.begin():
            await record_message(
                session,
                article_id=article_id,
                ticket_id=ids["ticket"],
                chat_id=CHAT_ID,
                message_id=MESSAGE_ID + 1,
                direction="out",
            )

        async with factory() as session:
            assert await get_by_article(session, article_id) is not None

        async with factory() as session, session.begin():
            await delete_article(
                session, ticket_id=ids["ticket"], article_id=article_id, user_id=ids["agent"]
            )

        async with factory() as session:
            assert await get_by_article(session, article_id) is None
    finally:
        await async_engine.dispose()
        _cleanup_ticket(sync_url, ids)
