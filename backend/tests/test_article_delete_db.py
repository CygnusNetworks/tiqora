"""DB integration tests: DELETE /tickets/{id}/articles/{id} (internal notes).

Uses the 887xx id range (queue/group/user/ticket) to avoid collisions with
other shared-DB fixtures — see test_article_bcc_reply_to_db.py (88xx, four
digit), test_ai_*.py (96xx-98xx), test_placeholder_variables_admin.py
(884xx).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    ArticleNotDeletable,
    TicketAccessDenied,
    TicketNotFound,
    TicketWriteService,
)
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

QUEUE_ID = 88700
GROUP_ID = 88730
TICKET_ID = 88770
AGENT_RW = 88701  # has rw on the queue's group
AGENT_NO_PERM = 88702  # has no permission at all


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our block (shared session-scoped DB). Child
        # rows referencing our ticket/articles (written by add_article /
        # delete_article in a prior run) must go first to satisfy FKs.
        conn.execute(text(f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}"))
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
        conn.execute(text(f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM ticket WHERE id = {TICKET_ID}"))
        conn.execute(text(f"DELETE FROM queue WHERE id = {QUEUE_ID}"))
        conn.execute(
            text(
                f"DELETE FROM group_user WHERE user_id IN ({AGENT_RW}, {AGENT_NO_PERM})"
                f" OR group_id = {GROUP_ID}"
            ),
        )
        conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP_ID}"))
        conn.execute(text(f"DELETE FROM users WHERE id IN ({AGENT_RW}, {AGENT_NO_PERM})"))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:rw, 'note.rw', :pw, 'Note', 'Rw', 1, :t, 1, :t, 1),"
                " (:noperm, 'note.noperm', :pw, 'Note', 'NoPerm', 1, :t, 1, :t, 1)"
            ),
            {"pw": pw, "t": NOW, "rw": AGENT_RW, "noperm": AGENT_NO_PERM},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'note-delete-grp', 1, :t, 1, :t, 1)"
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
                {"uid": AGENT_RW, "gid": GROUP_ID, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'NoteDeleteQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
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
                " VALUES (:tid, '20240601887001', 'Note delete ticket', :qid, 1, 1,"
                " :rw, 1, 3, 4, 'CUST1', 'alice@example.com',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "qid": QUEUE_ID, "rw": AGENT_RW, "t": NOW},
        )
    engine.dispose()
    return {
        "agent_rw": AGENT_RW,
        "agent_no_perm": AGENT_NO_PERM,
        "queue": QUEUE_ID,
        "ticket": TICKET_ID,
    }


async def _add_note(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    user_id: int,
    visible_to_customer: bool = False,
    channel: str = "note",
) -> int:
    svc = TicketWriteService(session, factory, sysconfig)
    return await svc.add_article(
        user_id,
        ticket_id,
        ArticleIn(
            sender_type="agent",
            is_visible_for_customer=visible_to_customer,
            subject="Test note",
            body="internal note body",
            channel=channel,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_internal_note_removes_dependent_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await _add_note(
            session, factory, sysconfig, ticket_id=ids["ticket"], user_id=ids["agent_rw"]
        )
        # Extra dependent rows to prove the cascade actually deletes them.
        await session.execute(
            text(
                "INSERT INTO article_flag (article_id, article_key, article_value,"
                " create_time, create_by)"
                " VALUES (:aid, 'seen', '1', :t, :uid)"
            ),
            {"aid": article_id, "t": NOW, "uid": ids["agent_rw"]},
        )
        await session.execute(
            text(
                "INSERT INTO time_accounting (ticket_id, article_id, time_unit,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :aid, 5, :t, :uid, :t, :uid)"
            ),
            {"tid": ids["ticket"], "aid": article_id, "t": NOW, "uid": ids["agent_rw"]},
        )

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)
        await svc.delete_article(ids["agent_rw"], ids["ticket"], article_id)

    async with factory() as session:
        assert (
            await session.execute(
                text("SELECT 1 FROM article WHERE id = :aid"), {"aid": article_id}
            )
        ).first() is None
        assert (
            await session.execute(
                text("SELECT 1 FROM article_data_mime WHERE article_id = :aid"), {"aid": article_id}
            )
        ).first() is None
        assert (
            await session.execute(
                text("SELECT 1 FROM article_flag WHERE article_id = :aid"), {"aid": article_id}
            )
        ).first() is None
        # time_accounting row is kept, article_id nulled — booked time isn't lost.
        ta_row = (
            await session.execute(
                text(
                    "SELECT article_id FROM time_accounting"
                    " WHERE ticket_id = :tid AND time_unit = 5"
                ),
                {"tid": ids["ticket"]},
            )
        ).first()
        assert ta_row is not None
        assert ta_row[0] is None
        # Misc "NoteDeleted" history row is present, using the existing Misc type.
        hist_row = (
            await session.execute(
                text(
                    "SELECT h.name FROM ticket_history h"
                    " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                    " WHERE h.ticket_id = :tid AND ht.name = 'Misc'"
                    " AND h.name LIKE '%%NoteDeleted%%'"
                ),
                {"tid": ids["ticket"]},
            )
        ).first()
        assert hist_row is not None
        assert str(article_id) in hist_row[0]
        # Cache invalidation row was written for this ticket.
        cache_row = (
            await session.execute(
                text("SELECT 1 FROM tiqora_cache_invalidation WHERE ticket_id = :tid"),
                {"tid": ids["ticket"]},
            )
        ).first()
        assert cache_row is not None
        # ArticleDelete outbox event was emitted.
        outbox_row = (
            await session.execute(
                text(
                    "SELECT 1 FROM tiqora_event_outbox"
                    " WHERE ticket_id = :tid AND event_type = 'ArticleDelete'"
                ),
                {"tid": ids["ticket"]},
            )
        ).first()
        assert outbox_row is not None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_customer_visible_article_returns_409(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await _add_note(
            session,
            factory,
            sysconfig,
            ticket_id=ids["ticket"],
            user_id=ids["agent_rw"],
            visible_to_customer=True,
        )

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)
        with pytest.raises(ArticleNotDeletable):
            await svc.delete_article(ids["agent_rw"], ids["ticket"], article_id)

    async with factory() as session:
        assert (
            await session.execute(
                text("SELECT 1 FROM article WHERE id = :aid"), {"aid": article_id}
            )
        ).first() is not None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_article_without_rw_permission_denied(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await _add_note(
            session, factory, sysconfig, ticket_id=ids["ticket"], user_id=ids["agent_rw"]
        )

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)
        with pytest.raises(TicketAccessDenied):
            await svc.delete_article(ids["agent_no_perm"], ids["ticket"], article_id)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_article_wrong_id_not_found(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)
        with pytest.raises(TicketNotFound):
            await svc.delete_article(ids["agent_rw"], ids["ticket"], 999_999_999)

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)
        with pytest.raises(TicketNotFound):
            await svc.delete_article(ids["agent_rw"], 999_999_999, 1)

    await engine.dispose()
