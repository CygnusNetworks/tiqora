"""DB tests for the ticket list's channel: ``TicketListItem.channel`` and the
Telegram contact fields, the ``channel`` filter and the ``channels`` facet.

Seed ids use the 873xx range; the module deletes its rows afterwards (and a
``Telegram`` communication_channel row only if it created one).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketService

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

AGENT_ID = 87300
GROUP_ID = 87320
QUEUE_ID = 87300
TICKET_MAIL = 87310  # e-mail only
TICKET_TG = 87311  # Telegram inbound, later an e-mail reply
TICKET_WEBCHAT = 87312  # Znuny "Chat" channel
TICKET_NOTE = 87313  # only an internal note — still "email"
TICKET_PHONE = 87314  # opened by a phone call, later an e-mail reply
TICKET_MAIL_PHONE = 87315  # e-mail first, a phone call later — still "email"
TICKET_PHONE_TG = 87316  # phone first, then Telegram — the chat channel wins
CHAT_ID = 873000001
TICKETS = (
    TICKET_MAIL,
    TICKET_TG,
    TICKET_WEBCHAT,
    TICKET_NOTE,
    TICKET_PHONE,
    TICKET_MAIL_PHONE,
    TICKET_PHONE_TG,
)
# article id -> (ticket id, channel name)
ARTICLES: dict[int, tuple[int, str]] = {
    87310: (TICKET_MAIL, "Email"),
    87311: (TICKET_TG, "Telegram"),
    87314: (TICKET_TG, "Email"),
    87312: (TICKET_WEBCHAT, "Chat"),
    87313: (TICKET_NOTE, "Internal"),
    87315: (TICKET_PHONE, "Phone"),
    87316: (TICKET_PHONE, "Email"),
    87317: (TICKET_MAIL_PHONE, "Email"),
    87318: (TICKET_MAIL_PHONE, "Phone"),
    87319: (TICKET_PHONE_TG, "Phone"),
    87320: (TICKET_PHONE_TG, "Telegram"),
}

_SEEDED: list[tuple[str, bool]] = []


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup_statements(*, drop_telegram_channel: bool) -> list[tuple[str, dict[str, Any]]]:
    stmts: list[tuple[str, dict[str, Any]]] = [
        ("DELETE FROM tiqora_telegram_message WHERE ticket_id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM tiqora_telegram_contact WHERE chat_id = :c", {"c": CHAT_ID}),
        ("DELETE FROM article WHERE id IN :ids", {"ids": tuple(ARTICLES)}),
        ("DELETE FROM ticket WHERE id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM queue WHERE id = :id", {"id": QUEUE_ID}),
        (
            "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
            {"uid": AGENT_ID, "gid": GROUP_ID},
        ),
        ("DELETE FROM permission_groups WHERE id = :id", {"id": GROUP_ID}),
        ("DELETE FROM users WHERE id = :id", {"id": AGENT_ID}),
    ]
    if drop_telegram_channel:
        stmts.append(("DELETE FROM communication_channel WHERE name = 'Telegram'", {}))
    return stmts


def _run(conn: Any, stmts: list[tuple[str, dict[str, Any]]]) -> None:
    from sqlalchemy import bindparam

    for sql, params in stmts:
        stmt = text(sql)
        if "ids" in params:
            stmt = stmt.bindparams(bindparam("ids", expanding=True))
        conn.execute(stmt, params)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, created_channel in _SEEDED:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _run(conn, _cleanup_statements(drop_telegram_channel=created_channel))
        engine.dispose()
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _run(conn, _cleanup_statements(drop_telegram_channel=False))

        channel_ids = dict(conn.execute(text("SELECT name, id FROM communication_channel")).all())
        created_channel = "Telegram" not in channel_ids
        if created_channel:
            new_id = max(channel_ids.values()) + 1
            conn.execute(
                text(
                    "INSERT INTO communication_channel (id, name, module, package_name,"
                    " channel_data, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 'Telegram', 'Kernel::System::CommunicationChannel::Telegram',"
                    " 'Tiqora', :data, 1, :t, 1, :t, 1)"
                ),
                {"id": new_id, "data": b"---\n", "t": NOW},
            )
            channel_ids["Telegram"] = new_id
        _SEEDED.append((sync_url, created_channel))

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'reader.channel873', 'x', 'Channel', 'Reader', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'channel873-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_ID, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Channel873Queue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
        )
        for i, ticket_id in enumerate(TICKETS):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Channel test', :qid, 1, 1, :uid, 1, 3, 4,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": ticket_id,
                    "tn": f"20240601873{i}",
                    "qid": QUEUE_ID,
                    "uid": AGENT_ID,
                    "t": NOW,
                },
            )
        for article_id, (ticket_id, channel) in sorted(ARTICLES.items()):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, :tid, 3, :cc, 1, 0, :t, 1, :t, 1)"
                ),
                {"aid": article_id, "tid": ticket_id, "cc": channel_ids[channel], "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_contact (chat_id, username, display_name,"
                " create_time, change_time) VALUES (:c, 'kim_example', 'Kim', :t, :t)"
            ),
            {"c": CHAT_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_telegram_message (article_id, ticket_id, chat_id,"
                " direction, created) VALUES (87311, :tid, :c, 'in', :t)"
            ),
            {"tid": TICKET_TG, "c": CHAT_ID, "t": NOW},
        )
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_channel_filter_and_facets(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        ts = TicketService(session)
        listed = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, limit=50)
        by_id = {i.id: i for i in listed.items}
        assert by_id[TICKET_MAIL].channel == "email"
        # Origin wins: the later e-mail reply does not make it an e-mail ticket.
        assert by_id[TICKET_TG].channel == "telegram"
        assert by_id[TICKET_WEBCHAT].channel == "webchat"
        assert by_id[TICKET_NOTE].channel == "email"
        assert by_id[TICKET_PHONE].channel == "phone"
        assert by_id[TICKET_MAIL_PHONE].channel == "email"
        assert by_id[TICKET_PHONE_TG].channel == "telegram"
        assert (by_id[TICKET_TG].chat_display_name, by_id[TICKET_TG].chat_username) == (
            "Kim",
            "kim_example",
        )
        assert by_id[TICKET_MAIL].chat_display_name is None

        # A chat linked to a real customer shows that customer instead.
        await session.execute(
            text(
                "UPDATE tiqora_telegram_contact SET customer_user_login = 'kim.linked'"
                " WHERE chat_id = :c"
            ),
            {"c": CHAT_ID},
        )
        await session.commit()
        relisted = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, limit=50)
        tg = next(i for i in relisted.items if i.id == TICKET_TG)
        assert (tg.channel, tg.chat_display_name, tg.chat_username) == ("telegram", None, None)

        async def ids_for(channel: list[str]) -> set[int]:
            page = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, channel=channel, limit=50)
            return {i.id for i in page.items}

        assert await ids_for(["telegram"]) == {TICKET_TG, TICKET_PHONE_TG}
        assert await ids_for(["email"]) == {TICKET_MAIL, TICKET_NOTE, TICKET_MAIL_PHONE}
        assert await ids_for(["phone"]) == {TICKET_PHONE}
        assert await ids_for(["telegram", "webchat"]) == {
            TICKET_TG,
            TICKET_PHONE_TG,
            TICKET_WEBCHAT,
        }

        expected = {"email": 3, "telegram": 2, "webchat": 1, "phone": 1}
        facets = await ts.facet_counts(AGENT_ID, queue_id=QUEUE_ID)
        assert facets["channels"] == expected
        # The channel filter narrows states/flags, but not its own chips.
        narrowed = await ts.facet_counts(AGENT_ID, queue_id=QUEUE_ID, channel=["telegram"])
        assert narrowed["channels"] == expected
        assert narrowed["states"]["all"] == 2

    await engine.dispose()
