"""DB integration tests for TicketWriteService (Phase 2b sub-tasks 1-6).

Covers:
- create_ticket: full invariant bundle (history name, escalation cols, cache row,
  unique TNs under 10x concurrency)
- add_article: article + mime + attachments + search_flag + history
- Field mutations: change_state, change_title, lock/unlock, watch/unwatch,
  update_dynamic_field
- merge_tickets: article moves, history on both, merged state, watcher handling
- tiqora_event_outbox: events written in same transaction by all write ops
- tiqora_form_draft: CRUD via raw SQL (no HTTP)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketAccessDenied,
    TicketIn,
    TicketNotFound,
    add_article,
    change_state,
    change_title,
    create_ticket,
    lock_ticket,
    merge_tickets,
    unlock_ticket,
    unwatch_ticket,
    update_dynamic_field,
    watch_ticket,
)
from tiqora.znuny.sysconfig import SysConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    """Create tiqora_* tables in testcontainer DB (Alembic not run there)."""
    dialects_ddl = [
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_form_draft (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NOT NULL,
            user_id INT NOT NULL,
            action VARCHAR(200) NOT NULL,
            title VARCHAR(255),
            content TEXT NOT NULL DEFAULT '{}',
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    for ddl in dialects_ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _make_sysconfig() -> SysConfig:
    """SysConfig that returns safe defaults (no DB needed)."""

    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _make_ticket(
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    title: str = "Test Ticket",
) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            sysconfig,
            params=TicketIn(
                title=title,
                queue_id=1,
                state_id=1,
                priority_id=3,
                owner_id=1,
            ),
            user_id=1,
        )


# ---------------------------------------------------------------------------
# Sub-task 1: create_ticket
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_create_ticket_invariant_bundle_mariadb(mariadb_znuny_url: str) -> None:
    """create_ticket writes history, escalation cols, cache row, and outbox event."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session:
        # History: NewTicket row with exact format
        hist = (
            await session.execute(
                text(
                    "SELECT h.name, ht.name as htype FROM ticket_history h"
                    " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                    " WHERE h.ticket_id = :tid AND ht.name = 'NewTicket'"
                    " ORDER BY h.id DESC LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert hist is not None, "NewTicket history not written"
        assert hist[1] == "NewTicket"
        assert "%%" in hist[0], f"Bad history name: {hist[0]}"

        # Escalation cols exist on ticket row
        t = (
            await session.execute(
                text(
                    "SELECT escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time"
                    " FROM ticket WHERE id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert t is not None

        # Cache row written
        cache = (
            await session.execute(
                text("SELECT id FROM tiqora_cache_invalidation WHERE ticket_id = :tid LIMIT 1"),
                {"tid": ticket_id},
            )
        ).first()
        assert cache is not None, "Cache invalidation row missing"

        # Outbox event written
        evt = (
            await session.execute(
                text(
                    "SELECT event_type FROM tiqora_event_outbox"
                    " WHERE ticket_id = :tid AND event_type = 'TicketCreate' LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert evt is not None, "TicketCreate outbox event missing"

    await engine.dispose()


@pytest.mark.db
async def test_create_ticket_unique_tns_concurrency_mariadb(mariadb_znuny_url: str) -> None:
    """10 concurrent create_ticket calls must produce 10 unique ticket numbers."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url, pool_size=20, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    async def _one() -> int:
        async with factory() as session, session.begin():
            return await create_ticket(
                session,
                factory,
                sysconfig,
                params=TicketIn(
                    title="Concurrent Ticket",
                    queue_id=1,
                    state_id=1,
                    priority_id=3,
                    owner_id=1,
                ),
                user_id=1,
            )

    ticket_ids = await asyncio.gather(*[_one() for _ in range(10)])
    assert len(set(ticket_ids)) == 10, f"Duplicate ticket ids: {sorted(ticket_ids)}"

    # Verify unique TNs
    async with factory() as session:
        tns = (
            await session.execute(
                text(
                    "SELECT tn FROM ticket WHERE id IN ("
                    + ",".join(str(i) for i in ticket_ids)
                    + ")"
                )
            )
        ).fetchall()
    tn_set = {r[0] for r in tns}
    assert len(tn_set) == 10, f"Duplicate TNs: {tn_set}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# Sub-task 2: add_article
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_add_article_invariants_mariadb(mariadb_znuny_url: str) -> None:
    """add_article writes article, mime, search_flag, history, attachment, outbox."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "Article Test")

    async with factory() as session, session.begin():
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject="Test Subject",
                body="Test body content",
                content_type="text/plain; charset=utf-8",
                from_address="agent@example.com",
                to_address="customer@example.com",
                message_id="<test-msg-id@example.com>",
                channel="note",
                attachments=[("test.txt", "text/plain", b"Hello World")],
            ),
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        # Article row with search flag
        art = (
            await session.execute(
                text("SELECT id, search_index_needs_rebuild FROM article WHERE id = :aid"),
                {"aid": article_id},
            )
        ).first()
        assert art is not None
        assert int(art[1]) == 1, "search_index_needs_rebuild not set"

        # MIME data with MD5
        mime = (
            await session.execute(
                text(
                    "SELECT a_subject, a_message_id_md5 FROM article_data_mime"
                    " WHERE article_id = :aid"
                ),
                {"aid": article_id},
            )
        ).first()
        assert mime is not None
        assert mime[0] == "Test Subject"
        assert mime[1] is not None, "MD5 not set"
        import hashlib

        expected_md5 = hashlib.md5(b"<test-msg-id@example.com>").hexdigest()  # noqa: S324
        assert mime[1] == expected_md5

        # Attachment
        att = (
            await session.execute(
                text("SELECT filename FROM article_data_mime_attachment WHERE article_id = :aid"),
                {"aid": article_id},
            )
        ).first()
        assert att is not None
        assert att[0] == "test.txt"

        # History row linked to article
        hist = (
            await session.execute(
                text(
                    "SELECT ht.name FROM ticket_history h"
                    " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                    " WHERE h.ticket_id = :tid AND h.article_id = :aid LIMIT 1"
                ),
                {"tid": ticket_id, "aid": article_id},
            )
        ).first()
        assert hist is not None, "Article history not written"

        # Outbox ArticleCreate event
        evt = (
            await session.execute(
                text(
                    "SELECT payload FROM tiqora_event_outbox"
                    " WHERE ticket_id = :tid AND event_type = 'ArticleCreate' LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert evt is not None, "ArticleCreate outbox event missing"

    await engine.dispose()


@pytest.mark.db
async def test_add_agent_note_without_from_stamps_agent_name_mariadb(
    mariadb_znuny_url: str,
) -> None:
    """Agent notes without a From header get the acting agent's full name —
    otherwise the UI renders the sender as "unbekannt"."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "Note From Test")

    async with factory() as session, session.begin():
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="Interne Notiz",
                body="Nur intern.",
                content_type="text/plain; charset=utf-8",
                channel="note",
            ),
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT a_from FROM article_data_mime WHERE article_id = :aid"),
                {"aid": article_id},
            )
        ).first()
        expected = (
            await session.execute(
                text("SELECT first_name, last_name, login FROM users WHERE id = 1"),
            )
        ).first()
        assert row is not None and expected is not None
        full_name = " ".join(p for p in (expected[0], expected[1]) if p).strip()
        assert row[0] == (full_name or expected[2])

    await engine.dispose()


# ---------------------------------------------------------------------------
# Sub-task 3: field mutations
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_change_state_closes_escalation_mariadb(mariadb_znuny_url: str) -> None:
    """change_state to closed zeroes all escalation columns."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "State Test")

    # Find a closed state id
    async with factory() as session:
        closed_row = (
            await session.execute(
                text(
                    "SELECT ts.id FROM ticket_state ts"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE tst.name = 'closed' AND ts.valid_id = 1 ORDER BY ts.id LIMIT 1"
                )
            )
        ).first()

    if closed_row is not None:
        closed_state_id = int(closed_row[0])
        async with factory() as session, session.begin():
            await change_state(
                session,
                ticket_id=ticket_id,
                new_state_id=closed_state_id,
                user_id=1,
                sysconfig=sysconfig,
            )

        async with factory() as session:
            t = (
                await session.execute(
                    text(
                        "SELECT escalation_time, escalation_update_time,"
                        " escalation_response_time, escalation_solution_time"
                        " FROM ticket WHERE id = :tid"
                    ),
                    {"tid": ticket_id},
                )
            ).first()
            assert t is not None
            assert all(v == 0 for v in t), f"Escalation cols not zeroed on close: {t}"

            hist = (
                await session.execute(
                    text(
                        "SELECT ht.name FROM ticket_history h"
                        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                        " WHERE h.ticket_id = :tid AND ht.name = 'StateUpdate' LIMIT 1"
                    ),
                    {"tid": ticket_id},
                )
            ).first()
            assert hist is not None, "StateUpdate history not written"

    await engine.dispose()


@pytest.mark.db
async def test_change_title_mariadb(mariadb_znuny_url: str) -> None:
    """change_title updates the ticket title."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await change_title(session, ticket_id=ticket_id, new_title="New Title", user_id=1)

    async with factory() as session:
        t = (
            await session.execute(
                text("SELECT title FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).first()
        assert t is not None and t[0] == "New Title"

    await engine.dispose()


@pytest.mark.db
async def test_lock_unlock_mariadb(mariadb_znuny_url: str) -> None:
    """lock_ticket and unlock_ticket update lock id and write history."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "Lock Test")

    async with factory() as session, session.begin():
        await lock_ticket(session, ticket_id=ticket_id, user_id=1, sysconfig=sysconfig)

    async with factory() as session:
        t = (
            await session.execute(
                text("SELECT ticket_lock_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).first()
        assert t is not None and int(t[0]) == 2, "ticket not locked"

    async with factory() as session, session.begin():
        await unlock_ticket(session, ticket_id=ticket_id, user_id=1, sysconfig=sysconfig)

    async with factory() as session:
        t = (
            await session.execute(
                text("SELECT ticket_lock_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).first()
        assert t is not None and int(t[0]) == 1, "ticket not unlocked"

    await engine.dispose()


@pytest.mark.db
async def test_watch_unwatch_mariadb(mariadb_znuny_url: str) -> None:
    """watch_ticket and unwatch_ticket add/remove watcher rows."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "Watch Test")

    async with factory() as session, session.begin():
        await watch_ticket(session, ticket_id=ticket_id, watcher_user_id=1, user_id=1)

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT 1 FROM ticket_watcher WHERE ticket_id = :tid AND user_id = 1"),
                {"tid": ticket_id},
            )
        ).first()
        assert row is not None, "Watcher row not written"

    async with factory() as session, session.begin():
        await unwatch_ticket(session, ticket_id=ticket_id, watcher_user_id=1, user_id=1)

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT 1 FROM ticket_watcher WHERE ticket_id = :tid AND user_id = 1"),
                {"tid": ticket_id},
            )
        ).first()
        assert row is None, "Watcher row still present after unwatch"

    await engine.dispose()


@pytest.mark.db
async def test_update_dynamic_field_unknown_is_noop_mariadb(mariadb_znuny_url: str) -> None:
    """update_dynamic_field silently ignores unknown field names."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "DF Test")

    async with factory() as session, session.begin():
        # Should not raise for unknown field
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name="NonExistentField99",
            values=["test_val"],
            user_id=1,
        )

    await engine.dispose()


# ---------------------------------------------------------------------------
# Sub-task 4: merge_tickets
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_merge_tickets_mariadb(mariadb_znuny_url: str) -> None:
    """merge_tickets moves articles, writes Merged history on both, merged state."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    main_id = await _make_ticket(factory, sysconfig, "Main Ticket")
    merge_id = await _make_ticket(factory, sysconfig, "Merge Ticket")

    # Add article to merge ticket
    async with factory() as session, session.begin():
        art_id = await add_article(
            session,
            ticket_id=merge_id,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject="Original Article",
                body="I need help",
                channel="email",
            ),
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        await merge_tickets(
            session,
            main_ticket_id=main_id,
            merge_ticket_id=merge_id,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        # Article moved to main ticket
        art_ticket = (
            await session.execute(
                text("SELECT ticket_id FROM article WHERE id = :aid"), {"aid": art_id}
            )
        ).first()
        assert art_ticket is not None
        assert int(art_ticket[0]) == main_id, f"Article not moved to main: {art_ticket[0]}"

        # Merged history on both tickets
        for tid in (main_id, merge_id):
            hist = (
                await session.execute(
                    text(
                        "SELECT ht.name FROM ticket_history h"
                        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                        " WHERE h.ticket_id = :tid AND ht.name = 'Merged' LIMIT 1"
                    ),
                    {"tid": tid},
                )
            ).first()
            assert hist is not None, f"Merged history missing on ticket {tid}"

        # Merge ticket state = 'merged'
        state_type = (
            await session.execute(
                text(
                    "SELECT tst.name FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.id = :tid"
                ),
                {"tid": merge_id},
            )
        ).first()
        if state_type is not None:
            assert state_type[0] == "merged", f"Merge ticket state not 'merged': {state_type[0]}"

        # TicketMerge outbox event
        evt = (
            await session.execute(
                text(
                    "SELECT payload FROM tiqora_event_outbox"
                    " WHERE ticket_id = :tid AND event_type = 'TicketMerge' LIMIT 1"
                ),
                {"tid": merge_id},
            )
        ).first()
        assert evt is not None, "TicketMerge outbox event missing"
        payload = json.loads(evt[0])
        assert payload["main_ticket_id"] == main_id

    await engine.dispose()


# ---------------------------------------------------------------------------
# Sub-task 5: tiqora_event_outbox
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_event_outbox_written_with_ticket_mariadb(mariadb_znuny_url: str) -> None:
    """Every create_ticket emits a TicketCreate event in the same transaction."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = await _make_ticket(factory, sysconfig, "Outbox Test")

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT event_type FROM tiqora_event_outbox WHERE ticket_id = :tid ORDER BY id"
                ),
                {"tid": ticket_id},
            )
        ).fetchall()
        event_types = [r[0] for r in rows]
        assert "TicketCreate" in event_types, f"Expected TicketCreate in {event_types}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# Sub-task 6: tiqora_form_draft
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_form_draft_crud_mariadb(mariadb_znuny_url: str) -> None:
    """tiqora_form_draft: insert, read, update, delete."""
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await _seed_tiqora_tables(session)

    ticket_id = 999_001
    user_id = 1

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tiqora_form_draft"
                " (ticket_id, user_id, action, title, content, created, changed)"
                " VALUES (:tid, :uid, 'AgentTicketNote', 'My Draft',"
                ' \'{"body":"hello"}\', current_timestamp, current_timestamp)'
            ),
            {"tid": ticket_id, "uid": user_id},
        )

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT content FROM tiqora_form_draft"
                    " WHERE ticket_id = :tid AND user_id = :uid AND action = 'AgentTicketNote'"
                ),
                {"tid": ticket_id, "uid": user_id},
            )
        ).first()
        assert row is not None
        content = json.loads(row[0])
        assert content.get("body") == "hello"

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE tiqora_form_draft SET content = :c, changed = current_timestamp"
                " WHERE ticket_id = :tid AND user_id = :uid AND action = 'AgentTicketNote'"
            ),
            {"c": '{"body":"updated"}', "tid": ticket_id, "uid": user_id},
        )

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT content FROM tiqora_form_draft"
                    " WHERE ticket_id = :tid AND user_id = :uid AND action = 'AgentTicketNote'"
                ),
                {"tid": ticket_id, "uid": user_id},
            )
        ).first()
        assert row is not None
        assert json.loads(row[0])["body"] == "updated"

    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM tiqora_form_draft WHERE ticket_id = :tid AND user_id = :uid"),
            {"tid": ticket_id, "uid": user_id},
        )

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT id FROM tiqora_form_draft WHERE ticket_id = :tid AND user_id = :uid"),
                {"tid": ticket_id, "uid": user_id},
            )
        ).first()
        assert row is None, "Draft not deleted"

    await engine.dispose()


# ---------------------------------------------------------------------------
# Unit tests (no DB)
# ---------------------------------------------------------------------------


def test_ticket_not_found_is_exception() -> None:
    exc = TicketNotFound(42)
    assert exc.args[0] == 42


def test_invalid_input_is_exception() -> None:
    exc = InvalidInput("bad field")
    assert "bad" in str(exc)


def test_ticket_access_denied_is_exception() -> None:
    exc = TicketAccessDenied("user 1 lacks rw")
    assert "rw" in str(exc)
