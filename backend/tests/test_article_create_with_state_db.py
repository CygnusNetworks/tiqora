"""DB integration tests: POST /tickets/{id}/articles with ``state_id`` ("send & set state").

The reply composer can move the ticket to its next state (close successful,
pending until a date, ...) in the same request that stores the article. The
state change is validated BEFORE the article is created, so a bad state never
leaves a sent/stored reply behind with a failed state change.

Uses the 889xx id range (queue/group/user/ticket) — see
test_article_delete_db.py (887xx) for the neighbouring block.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

QUEUE_ID = 88900
GROUP_ID = 88930
TICKET_ID = 88970
AGENT_RW = 88901  # rw on the queue's group
AGENT_NOTE = 88902  # only ro + note: may add articles, may NOT change state

STATE_OPEN = 4
STATE_CLOSED_SUCCESSFUL = 2
STATE_PENDING_REMINDER = 6


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup(conn: Any) -> None:
    arts = f"(SELECT id FROM article WHERE ticket_id = {TICKET_ID})"
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime_plain WHERE article_id IN {arts}",
        f"DELETE FROM article_flag WHERE article_id IN {arts}",
        f"DELETE FROM article WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket_flag WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket_index WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket_lock_index WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM time_accounting WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket WHERE id = {TICKET_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM group_user WHERE user_id IN ({AGENT_RW}, {AGENT_NOTE})"
        f" OR group_id = {GROUP_ID}",
        f"DELETE FROM permission_groups WHERE id = {GROUP_ID}",
        f"DELETE FROM users WHERE id IN ({AGENT_RW}, {AGENT_NOTE})",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:rw, 'replystate.rw', 'x', 'Reply', 'Rw', 1, :t, 1, :t, 1),"
                " (:note, 'replystate.note', 'x', 'Reply', 'Note', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW, "rw": AGENT_RW, "note": AGENT_NOTE},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:gid, 'reply-state-grp', 1, :t, 1, :t, 1)"
            ),
            {"gid": GROUP_ID, "t": NOW},
        )
        grants = [(AGENT_RW, k) for k in ("ro", "rw", "create", "note")]
        grants += [(AGENT_NOTE, k) for k in ("ro", "note")]
        for uid, key in grants:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": uid, "gid": GROUP_ID, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, 'ReplyStateQueue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
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
                " VALUES (:tid, '20240601889701', 'Reply state ticket', :qid, 1, 1,"
                " :rw, 1, 3, :open, 'CUST1', 'alice@example.com',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "qid": QUEUE_ID, "rw": AGENT_RW, "open": STATE_OPEN, "t": NOW},
        )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


def _ticket_row(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT ticket_state_id, until_time FROM ticket WHERE id = :t"),
            {"t": TICKET_ID},
        ).one()
        n_articles = conn.execute(
            text("SELECT COUNT(*) FROM article WHERE ticket_id = :t"), {"t": TICKET_ID}
        ).scalar_one()
        state_updates = conn.execute(
            text(
                "SELECT COUNT(*) FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :t AND ht.name = 'StateUpdate'"
            ),
            {"t": TICKET_ID},
        ).scalar_one()
    engine.dispose()
    return {
        "state_id": int(row[0]),
        "until_time": int(row[1]),
        "articles": int(n_articles),
        "state_updates": int(state_updates),
    }


@pytest.fixture
async def seeded(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # _write_service() asks the (unconfigured) default engine for a factory.
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    try:
        yield mariadb_znuny_url, factory
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=f"agent{user_id}",
        first_name="Reply",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _note(**extra: Any) -> dict[str, Any]:
    return {
        "subject": "Re: test",
        "body": "reply body",
        "channel": "note",
        "is_visible_for_customer": False,
        **extra,
    }


@pytest.mark.asyncio
async def test_no_state_id_leaves_state_unchanged(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = seeded
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(f"/api/v1/tickets/{TICKET_ID}/articles", json=_note())
    assert resp.status_code == 201, resp.text
    assert set(resp.json()) == {"article_id"}
    row = _ticket_row(url)
    assert row["articles"] == 1
    assert row["state_id"] == STATE_OPEN
    assert row["state_updates"] == 0


@pytest.mark.asyncio
async def test_reply_and_close_successful(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = seeded
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_note(state_id=STATE_CLOSED_SUCCESSFUL),
        )
    assert resp.status_code == 201, resp.text
    row = _ticket_row(url)
    assert row["articles"] == 1
    assert row["state_id"] == STATE_CLOSED_SUCCESSFUL
    assert row["until_time"] == 0
    assert row["state_updates"] == 1


@pytest.mark.asyncio
async def test_pending_without_pending_time_is_422_and_no_article(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = seeded
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_note(state_id=STATE_PENDING_REMINDER),
        )
    assert resp.status_code == 422, resp.text
    assert "pending_time" in resp.json()["detail"]
    row = _ticket_row(url)
    assert row["articles"] == 0
    assert row["state_id"] == STATE_OPEN
    assert row["state_updates"] == 0


@pytest.mark.asyncio
async def test_pending_with_pending_time_sets_until_time(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = seeded
    pending = datetime(2030, 1, 2, 9, 30, 0)
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_note(state_id=STATE_PENDING_REMINDER, pending_time=pending.isoformat()),
        )
    assert resp.status_code == 201, resp.text
    row = _ticket_row(url)
    assert row["articles"] == 1
    assert row["state_id"] == STATE_PENDING_REMINDER
    assert row["until_time"] == int(pending.timestamp())
    assert row["state_updates"] == 1


@pytest.mark.asyncio
async def test_unknown_state_id_is_422_and_no_article(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = seeded
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles", json=_note(state_id=987654)
        )
    assert resp.status_code == 422, resp.text
    row = _ticket_row(url)
    assert row["articles"] == 0
    assert row["state_id"] == STATE_OPEN


@pytest.mark.asyncio
async def test_note_only_agent_cannot_change_state_and_no_article(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """``note`` suffices for the article but a state change needs ``rw`` (as
    in PATCH) — the request is refused up-front, nothing is stored."""
    url, factory = seeded
    async with _client(factory, AGENT_NOTE) as client:
        denied = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json=_note(state_id=STATE_CLOSED_SUCCESSFUL),
        )
        assert denied.status_code == 403, denied.text
        assert _ticket_row(url)["articles"] == 0
        plain = await client.post(f"/api/v1/tickets/{TICKET_ID}/articles", json=_note())
        assert plain.status_code == 201, plain.text
    row = _ticket_row(url)
    assert row["articles"] == 1
    assert row["state_id"] == STATE_OPEN


@pytest.mark.asyncio
async def test_invalid_state_rejected_before_email_is_sent(
    seeded: tuple[str, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Email replies are send-then-store (no rollback possible), so the state
    change must be validated before delivery: nothing may reach the mailer."""
    from tiqora.api.v1 import tickets as tickets_api
    from tiqora.channels.email.smtp import CapturingMailSender
    from tiqora.domain.ticket_write_service import TicketWriteService
    from tiqora.znuny.sysconfig import SysConfig

    url, factory = seeded
    sender = CapturingMailSender()
    monkeypatch.setattr(
        tickets_api,
        "_write_service",
        lambda session, settings: TicketWriteService(
            session, factory, SysConfig(session), mail_sender=sender
        ),
    )
    async with _client(factory, AGENT_RW) as client:
        resp = await client.post(
            f"/api/v1/tickets/{TICKET_ID}/articles",
            json={
                "subject": "Re: test",
                "body": "reply body",
                "channel": "email",
                "sender_type": "agent",
                "from_address": "support@example.com",
                "to_address": "alice@example.com",
                "state_id": STATE_PENDING_REMINDER,
            },
        )
    assert resp.status_code == 422, resp.text
    assert sender.sent == []
    assert _ticket_row(url)["articles"] == 0
