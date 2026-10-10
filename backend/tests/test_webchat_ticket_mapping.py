"""WebChat article channel in the ticket write path (band 48420-48449, both dialects).

A chat line is a plain Znuny article on the ``WebChat`` channel (module
``…::Internal``, registered on first use), written as a web request / follow-up
/ answer; merge and article delete take the Tiqora-side chat rows along.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.channels.common import EMPTY_TABLES_CHANNEL_DATA, ensure_webchat_channel_row
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatMessage, TiqoraChatSession
from tiqora.domain.ticket_service import LIST_CHANNELS, TicketService
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    add_article,
    create_ticket,
    delete_article,
    merge_tickets,
)
from tiqora.reporting.facts import FACT_VERSION, channel_key
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.store import config_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

QUEUE, GROUP, AGENT = 48421, 48422, 48423
CONFIG_IDS = range(48420, 48450)
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


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


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_band(
            conn, config_ids=CONFIG_IDS, queue_ids=[QUEUE], group_ids=[GROUP], user_ids=[AGENT]
        )
    engine.dispose()


def _stash_webchat_row(url: str) -> dict[str, object] | None:
    """Remove a WebChat row another module left (so the tests see a fresh install);
    returns it for ``_restore_webchat_row``. A row that articles use is kept: skip."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            row = (
                conn.execute(text("SELECT * FROM communication_channel WHERE name = 'WebChat'"))
                .mappings()
                .first()
            )
            if row is None:
                return None
            used = conn.execute(
                text("SELECT COUNT(*) FROM article WHERE communication_channel_id = :i"),
                {"i": row["id"]},
            ).scalar_one()
            if used:
                pytest.skip("a WebChat channel row with articles already exists")
            conn.execute(text("DELETE FROM communication_channel WHERE id = :i"), {"i": row["id"]})
            return dict(row)
    finally:
        engine.dispose()


def _restore_webchat_row(url: str, saved: dict[str, object] | None) -> None:
    if saved is None:
        return
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM communication_channel WHERE name = 'WebChat'"))
            cols = ", ".join(saved)
            params = ", ".join(f":{c}" for c in saved)
            conn.execute(
                text(f"INSERT INTO communication_channel ({cols}) VALUES ({params})"), saved
            )
    finally:
        engine.dispose()


@pytest.fixture
def env(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    """(sync url, async session factory); everything created is removed afterwards."""
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-map")
    engine.dispose()
    saved = _stash_webchat_row(url)
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    yield url, factory
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _restore_webchat_row(url, saved)
    _cleanup(url)


async def _ticket(factory: async_sessionmaker[AsyncSession], title: str) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            SysConfig(session),
            params=TicketIn(
                title=title,
                queue_id=QUEUE,
                state_id=1,
                priority_id=3,
                owner_id=1,
                customer_id="wc48-cust",
                customer_user_id="wc48@example.org",
            ),
            user_id=1,
        )


def _article(sender: str, body: str = "hello") -> ArticleIn:
    return ArticleIn(
        sender_type=sender,
        is_visible_for_customer=True,
        subject="Chat",
        body=body,
        channel="webchat",
    )


async def _add(
    factory: async_sessionmaker[AsyncSession], ticket_id: int, article: ArticleIn
) -> int:
    async with factory() as session, session.begin():
        return await add_article(
            session, ticket_id=ticket_id, article=article, user_id=1, sysconfig=SysConfig(session)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_webchat_article_uses_webchat_channel_and_web_request_history(env) -> None:  # type: ignore[no-untyped-def]
    _, factory = env
    ticket_id = await _ticket(factory, "wc48 mapping")
    async with factory() as session, session.begin():
        await ensure_webchat_channel_row(session)
    a1 = await _add(factory, ticket_id, _article("customer", "one"))
    a2 = await _add(factory, ticket_id, _article("customer", "two"))
    a3 = await _add(factory, ticket_id, _article("agent", "three"))

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT name, module, package_name, channel_data, valid_id"
                    " FROM communication_channel WHERE name = 'WebChat'"
                )
            )
        ).one()
        internal = (
            await session.execute(
                text("SELECT channel_data FROM communication_channel WHERE name = 'Internal'")
            )
        ).scalar_one()
        assert row.module == "Kernel::System::CommunicationChannel::Internal"
        assert row.package_name == "Tiqora" and int(row.valid_id) == 1
        stored = row.channel_data
        stored_text = stored.decode() if isinstance(stored, (bytes, memoryview)) else stored
        assert stored_text == EMPTY_TABLES_CHANNEL_DATA  # YAML, no shared MIME tables
        assert internal is not None
        channel_ids = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT communication_channel_id FROM article WHERE ticket_id=:t"
                    ),
                    {"t": ticket_id},
                )
            )
            .scalars()
            .all()
        )
        wc_id = (
            await session.execute(text("SELECT id FROM communication_channel WHERE name='WebChat'"))
        ).scalar_one()
        assert [int(c) for c in channel_ids] == [int(wc_id)]
        types = (
            (
                await session.execute(
                    text(
                        "SELECT ht.name FROM ticket_history h"
                        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                        " WHERE h.article_id IN (:a, :b, :c)"
                        " ORDER BY h.id"
                    ),
                    {"a": a1, "b": a2, "c": a3},
                )
            )
            .scalars()
            .all()
        )
        assert list(types) == ["WebRequestCustomer", "FollowUp", "SendAnswer"]
        channels = await TicketService(session)._channel_by_ticket([ticket_id])
        assert channels == {ticket_id: "webchat"}


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_add_article_registers_missing_channel_row(env) -> None:  # type: ignore[no-untyped-def]
    """No pre-existing row: the article must not silently land on Internal (id 1)."""
    _, factory = env  # the fixture starts without a WebChat row
    ticket_id = await _ticket(factory, "wc48 no row")
    article_id = await _add(factory, ticket_id, _article("customer"))
    async with factory() as session:
        name = (
            await session.execute(
                text(
                    "SELECT c.name FROM communication_channel c WHERE c.id ="
                    " (SELECT communication_channel_id FROM article WHERE id = :a)"
                ),
                {"a": article_id},
            )
        ).scalar_one()
        assert name == "WebChat"
        cache = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tiqora_cache_invalidation"
                    " WHERE cache_type = 'CommunicationChannel'"
                )
            )
        ).scalar_one()
        assert int(cache) >= 1


def _insert_chat_rows(url: str, *, ticket_id: int, sid: str, article_ids: list[int | None]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            insert(TiqoraChatSession).values(
                id=sid,
                config_id=48425,
                topic_key="support",
                queue_id=QUEUE,
                ticket_id=ticket_id,
                status="active",
                token_hash="0" * 64,
                visitor_id="wc48-visitor",
                visitor_email="visitor@example.org",
                last_seq=len(article_ids),
                visitor_last_seen=NOW,
                created=NOW,
            )
        )
        for seq, article_id in enumerate(article_ids, start=1):
            conn.execute(
                insert(TiqoraChatMessage).values(
                    session_id=sid,
                    ticket_id=ticket_id,
                    seq=seq,
                    sender="visitor",
                    body=f"line {seq}",
                    article_id=article_id,
                    created=NOW,
                )
            )
    engine.dispose()


def _chat_rows(url: str, sid: str) -> tuple[int | None, list[int]]:
    engine = create_engine(url)
    with engine.begin() as conn:
        sess = conn.execute(
            text("SELECT ticket_id FROM tiqora_chat_session WHERE id = :s"), {"s": sid}
        ).first()
        msgs = conn.execute(
            text("SELECT ticket_id FROM tiqora_chat_message WHERE session_id = :s ORDER BY seq"),
            {"s": sid},
        ).all()
    engine.dispose()
    return (int(sess[0]) if sess else None), [int(m[0]) for m in msgs]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_merge_moves_chat_rows(env) -> None:  # type: ignore[no-untyped-def]
    url, factory = env
    main_id = await _ticket(factory, "wc48 main")
    merge_id = await _ticket(factory, "wc48 merge")
    a1 = await _add(factory, merge_id, _article("customer", "x"))
    _insert_chat_rows(url, ticket_id=merge_id, sid="wc48" + "0" * 28, article_ids=[a1, None])
    async with factory() as session, session.begin():
        await merge_tickets(
            session,
            main_ticket_id=main_id,
            merge_ticket_id=merge_id,
            user_id=1,
            sysconfig=SysConfig(session),
        )
    ticket_of_session, msg_tickets = _chat_rows(url, "wc48" + "0" * 28)
    assert ticket_of_session == main_id
    assert msg_tickets == [main_id, main_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_delete_article_removes_chat_message(env) -> None:  # type: ignore[no-untyped-def]
    url, factory = env
    ticket_id = await _ticket(factory, "wc48 delete")
    # delete_article only accepts internal notes; chat rows may reference one.
    note = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=False,
        subject="n",
        body="n",
        channel="note",
    )
    a1 = await _add(factory, ticket_id, note)
    a2 = await _add(factory, ticket_id, note)
    sid = "wc48" + "1" * 28
    _insert_chat_rows(url, ticket_id=ticket_id, sid=sid, article_ids=[a1, a2])
    async with factory() as session, session.begin():
        await delete_article(session, ticket_id=ticket_id, article_id=a1, user_id=1)
    engine = create_engine(url)
    with engine.begin() as conn:
        remaining = conn.execute(
            text("SELECT article_id FROM tiqora_chat_message WHERE session_id = :s"), {"s": sid}
        ).all()
    engine.dispose()
    assert [int(r[0]) for r in remaining] == [a2]


def _row(**over: object) -> TiqoraChatConfig:
    base: dict[str, object] = dict(
        id=1,
        name="x",
        public_key="wc_x",
        valid=True,
        allowed_origins="not json",
        topics='[{"key": "s", "label": "S", "queue_id": 3}]',
        prechat_fields="[]",
        upload_policy='{"max_bytes": 1}',
        theme="{}",
        rate_limits="{}",
        offline_mode="form",
        wait_timeout_seconds=180,
        show_agent_names=True,
        retention_days=30,
    )
    base.update(over)
    return TiqoraChatConfig(**base)


def test_config_values_fall_back_per_column() -> None:
    with capture_logs() as logs:
        v = config_values(_row())
    assert v.allowed_origins == [] and v.topics[0].queue_id == 3
    assert v.upload_policy.max_bytes == 10 * 1024 * 1024  # invalid value -> default
    assert v.prechat_fields == []  # a valid explicit empty list is kept
    columns = {e["column"] for e in logs if e["event"] == "webchat_config_column_invalid"}
    assert columns == {"allowed_origins", "upload_policy"}


def test_config_values_broken_topics_yield_empty_list_not_an_error() -> None:
    """R-TOPICS-EMPTY: min_length=1 binds admin input only; the fallback is offline."""
    for broken in ("garbage", "[]", '[{"key": "A B"}]'):
        v = config_values(_row(topics=broken))
        assert v.topics == []
        assert v.offline_mode == "form"
    with pytest.raises(ValidationError):
        ChatConfigValues(topics=[])


def test_chat_channel_reports_as_webchat() -> None:
    assert channel_key("WebChat") == "webchat"
    assert FACT_VERSION == 2  # no bump: no WebChat rows exist before this deploy
    assert LIST_CHANNELS["WebChat"] == "webchat"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_ensure_webchat_channel_row_is_race_safe(env) -> None:  # type: ignore[no-untyped-def]
    """Two first-ever writers: B's INSERT waits for A's commit, hits the unique
    name, and must return A's id without an error (PG: no aborted transaction)."""
    import asyncio

    _, factory = env
    async with factory() as a, a.begin():
        id_a = await ensure_webchat_channel_row(a)

        async def second() -> int:
            async with factory() as b, b.begin():
                got = await ensure_webchat_channel_row(b)
                # the transaction must still be usable afterwards
                await b.execute(text("SELECT 1"))
                return got

        task = asyncio.create_task(second())
        await asyncio.sleep(1.0)  # B is now blocked on A's uncommitted row
        assert not task.done()
    id_b = await asyncio.wait_for(task, timeout=15)
    assert id_a == id_b
    async with factory() as session:
        n = (
            await session.execute(
                text("SELECT COUNT(*) FROM communication_channel WHERE name = 'WebChat'")
            )
        ).scalar_one()
    assert int(n) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_load_config_and_by_key(env) -> None:  # type: ignore[no-untyped-def]
    from tests._webchat_fixtures import insert_config
    from tiqora.webchat.store import load_config, load_config_by_key

    url, factory = env
    engine = create_engine(url)
    with engine.begin() as conn:
        insert_config(
            conn, config_id=48426, queue_id=QUEUE, public_key="wc_48426", origins=["https://a.test"]
        )
        insert_config(
            conn,
            config_id=48427,
            queue_id=QUEUE,
            public_key="wc_48427",
            origins=["https://b.test"],
            valid=False,
        )
    engine.dispose()
    async with factory() as session:
        row = await load_config(session, 48426)
        assert row is not None and row.public_key == "wc_48426"
        assert await load_config(session, 48499) is None
        by_key = await load_config_by_key(session, "wc_48426")
        assert by_key is not None and by_key.id == 48426
        assert await load_config_by_key(session, "wc_48427") is None  # invalid config
        assert await load_config_by_key(session, "wc_unknown") is None
        assert config_values(row).allowed_origins == ["https://a.test"]
