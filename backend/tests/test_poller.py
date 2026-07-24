"""Tests for worker/poller.py — Znuny-write watermark poller.

Meilisearch re-indexing and pub/sub publish are mocked (dedicated coverage
elsewhere); this file focuses on poll_once's own logic: watermark
advancement, which touched tickets get a "new mail" notification vs. a
generic re-index-only ping, and that an exception during re-indexing still
propagates (rather than being silently swallowed) while incrementing the
error counter.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import (
    KEY_ARTICLE_WATERMARK,
    KEY_HISTORY_WATERMARK,
    get_setting_int,
)
from tiqora.worker import poller

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Seeded by initial_insert.*.sql (fixed ids, not test-owned):
# ticket_history_type 1 = 'NewTicket'; article_sender_type 1 = 'agent', 3 = 'customer'.


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


def _seed_queue_and_ticket(sync_url: str, *, ns: int) -> dict[str, int | str]:
    """Seed one isolated group/queue/ticket in a private 93xx id band."""
    group_id = 9330 + ns
    queue_id = 9300 + ns
    ticket_id = 9370 + ns
    tn = f"20240601930{ns:03d}"
    queue_name = f"PollerQueue93{ns}"

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": ticket_id})
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"poller-grp-93{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": queue_name, "gid": group_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1,"
                " 1, 1, 3, 4, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"Poller ticket 93{ns}",
                "qid": queue_id,
                "cid": f"CUST93{ns}",
                "cuid": f"cust93{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {"queue": queue_id, "ticket": ticket_id, "tn": tn, "queue_name": queue_name}


def _insert_history(sync_url: str, *, ticket_id: int, history_type_id: int) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ticket_history (name, history_type_id, ticket_id, article_id,"
                " type_id, queue_id, owner_id, priority_id, state_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES ('poller-test', :htid, :tid, NULL, 1, 1, 1, 3, 4, :t, 1, :t, 1)"
            ),
            {"htid": history_type_id, "tid": ticket_id, "t": NOW},
        )
        hist_id = conn.execute(text("SELECT MAX(id) FROM ticket_history")).scalar()
    engine.dispose()
    return int(hist_id)


def _insert_article(sync_url: str, *, ticket_id: int, sender_type_id: int) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " search_index_needs_rebuild, insert_fingerprint, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:tid, :st, 1, 1, 0, :fp, :t, 1, :t, 1)"
            ),
            {
                "tid": ticket_id,
                "st": sender_type_id,
                "fp": f"poller-fp-{ticket_id}-{sender_type_id}",
                "t": NOW,
            },
        )
        art_id = conn.execute(text("SELECT MAX(id) FROM article")).scalar()
    engine.dispose()
    return int(art_id)


def _reset_watermarks(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            TiqoraSettings.__table__.delete().where(
                TiqoraSettings.key.in_([KEY_HISTORY_WATERMARK, KEY_ARTICLE_WATERMARK])
            )
        )
    engine.dispose()


@pytest.fixture(autouse=True)
def _mock_pubsub() -> object:
    with patch.object(poller, "get_pubsub_redis", return_value=object()) as m:
        yield m


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_no_activity_is_a_noop(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # First run establishes watermarks at current max (whatever pre-existing rows exist).
    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)) as mock_reindex:
        first = await poller.poll_once(settings=get_settings(), session_factory=factory)
    assert mock_reindex.await_count == 0 or first["ticket_ids"] >= 0  # sanity, no crash

    # Second run: nothing changed since first run -> no ticket_ids, reindex not called.
    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)) as mock_reindex:
        second = await poller.poll_once(settings=get_settings(), session_factory=factory)

    assert second["ticket_ids"] == 0
    assert second["indexed"] == 0
    mock_reindex.assert_not_called()

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_new_ticket_history_notifies_and_reindexes(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    ids = _seed_queue_and_ticket(sync_url, ns=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Establish a baseline watermark past any pre-existing rows.
    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    _insert_history(sync_url, ticket_id=int(ids["ticket"]), history_type_id=1)  # NewTicket

    with (
        patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=1)) as mock_reindex,
        patch.object(poller, "publish_ticket_event", new=AsyncMock()) as mock_pub,
        patch.object(poller, "publish_new_ticket_in_queue", new=AsyncMock()) as mock_pub_new,
    ):
        result = await poller.poll_once(settings=get_settings(), session_factory=factory)

    assert result["ticket_ids"] == 1
    assert result["indexed"] == 1
    mock_reindex.assert_awaited_once()
    (reindexed_ids,), _ = mock_reindex.call_args
    assert reindexed_ids == [ids["ticket"]]

    mock_pub.assert_awaited_once()
    _, kwargs = mock_pub.call_args
    assert mock_pub.call_args.args[1] == ids["ticket"]
    assert mock_pub.call_args.args[2] == "poller"

    mock_pub_new.assert_awaited_once()
    _, new_kwargs = mock_pub_new.call_args
    assert new_kwargs["ticket_id"] == ids["ticket"]
    assert new_kwargs["tn"] == ids["tn"]
    assert new_kwargs["queue_id"] == ids["queue"]
    assert new_kwargs["queue_name"] == ids["queue_name"]

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_customer_article_notifies(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    ids = _seed_queue_and_ticket(sync_url, ns=2)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    _insert_article(sync_url, ticket_id=int(ids["ticket"]), sender_type_id=3)  # customer

    with (
        patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=1)),
        patch.object(poller, "publish_ticket_event", new=AsyncMock()),
        patch.object(poller, "publish_new_ticket_in_queue", new=AsyncMock()) as mock_pub_new,
    ):
        result = await poller.poll_once(settings=get_settings(), session_factory=factory)

    assert result["ticket_ids"] == 1
    mock_pub_new.assert_awaited_once()
    assert mock_pub_new.call_args.kwargs["ticket_id"] == ids["ticket"]

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_agent_article_reindexes_without_notify(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """An internal agent article touches the ticket for re-indexing but is not a
    'new mail' event."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    ids = _seed_queue_and_ticket(sync_url, ns=3)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    _insert_article(sync_url, ticket_id=int(ids["ticket"]), sender_type_id=1)  # agent

    with (
        patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=1)),
        patch.object(poller, "publish_ticket_event", new=AsyncMock()) as mock_pub,
        patch.object(poller, "publish_new_ticket_in_queue", new=AsyncMock()) as mock_pub_new,
    ):
        result = await poller.poll_once(settings=get_settings(), session_factory=factory)

    assert result["ticket_ids"] == 1
    mock_pub.assert_awaited_once()  # generic "poller" ping still fires
    mock_pub_new.assert_not_called()  # but no "new mail" notification

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_advances_watermarks(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    ids = _seed_queue_and_ticket(sync_url, ns=4)
    hist_id = _insert_history(sync_url, ticket_id=int(ids["ticket"]), history_type_id=1)
    art_id = _insert_article(sync_url, ticket_id=int(ids["ticket"]), sender_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with (
        patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=1)),
        patch.object(poller, "publish_ticket_event", new=AsyncMock()),
        patch.object(poller, "publish_new_ticket_in_queue", new=AsyncMock()),
    ):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    async with factory() as session:
        hist_wm = await get_setting_int(session, KEY_HISTORY_WATERMARK, 0)
        art_wm = await get_setting_int(session, KEY_ARTICLE_WATERMARK, 0)

    assert hist_wm >= hist_id
    assert art_wm >= art_id

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_poll_once_reindex_failure_propagates_and_counts_error(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A failed re-index must not be swallowed — poll_once re-raises (worker loop logs/retries)."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _reset_watermarks(sync_url)
    ids = _seed_queue_and_ticket(sync_url, ns=5)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    with patch.object(poller, "reindex_ticket_ids", new=AsyncMock(return_value=0)):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    _insert_history(sync_url, ticket_id=int(ids["ticket"]), history_type_id=1)

    before_error_count = poller.POLLER_RUNS.labels(status="error")._value.get()

    with (
        patch.object(
            poller,
            "reindex_ticket_ids",
            new=AsyncMock(side_effect=RuntimeError("meilisearch down")),
        ),
        pytest.raises(RuntimeError, match="meilisearch down"),
    ):
        await poller.poll_once(settings=get_settings(), session_factory=factory)

    after_error_count = poller.POLLER_RUNS.labels(status="error")._value.get()
    assert after_error_count == before_error_count + 1

    await engine.dispose()
