"""AI first line: availability, routing at chat start, notifications (band 54500-54539)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_bot_fixtures import (
    cleanup_bot_queue,
    seed_ai_usage,
    seed_bot_queue,
    set_auto_reply_paused_sync,
)
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.ai.auto_run import auto_reply_queue_cap_reason
from tiqora.ai.models import TiqoraAiQueuePolicy
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.webchat.availability import config_availability, mark_agent_online, set_agent_state
from tiqora.webchat.bot import BOT_ALIVE_KEY, bot_availability
from tiqora.webchat.models import ChatConfigValues, ChatTopic
from tiqora.webchat.notify import (
    AGENT_EVENTS_KEY,
    BOT_QUEUE_KEY,
    flush_chat_notifications,
    queue_bot_turn,
)
from tiqora.webchat.public_api import _ai_first_topics, _public_config
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import ChatError, VisitorContext, create_session
from tiqora.webchat.store import config_values, load_config, load_messages_since
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, AGENT, SERVICE = 54500, 54501, 54503, 54502
QUEUE_PLAIN = 54504
CONFIG, CONFIG_AI_OFFLINE, CONFIG_FORM = 54510, 54511, 54512
CONFIG_IDS = range(54500, 54540)
KEY_AI, KEY_AI_OFFLINE, KEY_FORM = "wc_" + "a" * 32, "wc_" + "b" * 32, "wc_" + "c" * 32
ORIGIN = "https://www.example.org"
VISITOR = "v" * 22
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef",
    user_agent="Mozilla/5.0 (Test)",
    page_url="https://www.example.org/kontakt",
    language="de",
)
FIRST_LINE = ChatTopic(key="support", label="Support", queue_id=QUEUE, ai_mode="first_line")
HUMAN_ONLY = ChatTopic(key="sales", label="Vertrieb", queue_id=QUEUE_PLAIN)


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
        cleanup_bot_queue(conn, queue_ids=[QUEUE, QUEUE_PLAIN])
        cleanup_band(
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE, QUEUE_PLAIN],
            group_ids=[GROUP],
            user_ids=[AGENT, SERVICE],
        )
    engine.dispose()


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc54-human")
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'wc54-queue-plain', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_PLAIN, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'wc54-service', 'x', 'Chat', 'Bot', 1, :t, 1, :t, 1)"
            ),
            {"uid": SERVICE, "t": NOW},
        )
        insert_config(
            conn,
            config_id=CONFIG,
            queue_id=QUEUE,
            public_key=KEY_AI,
            origins=[ORIGIN],
            topics=[FIRST_LINE, HUMAN_ONLY],
            values_override={"default_customer_user": None, "bot_name": "Hilfe-Bot"},
        )
        insert_config(
            conn,
            config_id=CONFIG_AI_OFFLINE,
            queue_id=QUEUE,
            public_key=KEY_AI_OFFLINE,
            origins=[ORIGIN],
            topics=[FIRST_LINE, HUMAN_ONLY],
            values_override={"default_customer_user": None, "offline_mode": "ai"},
        )
        insert_config(
            conn,
            config_id=CONFIG_FORM,
            queue_id=QUEUE,
            public_key=KEY_FORM,
            origins=[ORIGIN],
            topics=[FIRST_LINE],
            values_override={"default_customer_user": None, "offline_mode": "form"},
        )
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE)
        enable_webchat(conn)
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    redis = FakeRedis()
    await redis.set(BOT_ALIVE_KEY, "1", ex=30)
    yield Env(url, factory, redis)
    await aengine.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


@pytest.fixture(autouse=True)
def llm(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """``build_task_llm`` resolves to a dummy client; the call count is what tests read."""
    mock = AsyncMock(return_value=object())
    monkeypatch.setattr("tiqora.webchat.bot.build_task_llm", mock)
    return mock


# -- helpers -------------------------------------------------------------------------------


async def _humans_online(env: Env) -> None:
    async with env.factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(env.redis, AGENT)


async def _config(env: Env, config_id: int) -> tuple[TiqoraChatConfig, ChatConfigValues]:
    async with env.factory() as db:
        cfg = await load_config(db, config_id)
        assert cfg is not None
        return cfg, config_values(cfg)


def _payload(key: str, topic: str = "support") -> ChatSessionCreateIn:
    return ChatSessionCreateIn(
        key=key,
        origin=ORIGIN,
        visitor_id=VISITOR,
        topic=topic,
        prechat={"name": "Erika", "email": "erika@example.org"},
        message="Mein Router blinkt rot",
        client_id="c-1",
    )


async def _start(
    env: Env, config_id: int = CONFIG, key: str = KEY_AI, topic: str = "support"
) -> tuple[TiqoraChatSession, list[dict[str, Any]], list[str]]:
    """Create a chat; returns (session, queued agent events, ids queued for the bot)."""
    cfg, values = await _config(env, config_id)
    async with env.factory() as db:
        chat, _token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=values,
            payload=_payload(key, topic),
            ctx=CTX,
            now=NOW,
            settings=get_settings(),
        )
        events = list(db.info[AGENT_EVENTS_KEY])
        await db.commit()
        await flush_chat_notifications(db, env.redis)
        return chat, events, list(env.redis._lists.get(BOT_QUEUE_KEY, []))


async def _messages(env: Env, session_id: str) -> list[Any]:
    async with env.factory() as db:
        return list(await load_messages_since(db, session_id, 0))


def _sync(env: Env) -> Any:
    return create_engine(env.url)


# -- routing at chat start ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_first_line_topic_starts_in_bot(env: Env) -> None:
    await _humans_online(env)
    chat, events, queued = await _start(env)
    assert chat.status == "bot"
    assert chat.waiting_since is None
    assert chat.last_visitor_seq == 1
    lines = await _messages(env, chat.id)
    assert [m.sender for m in lines] == ["visitor", "system"]
    assert lines[1].body == "bot_intro"
    assert json.loads(lines[1].meta) == {"name": "Hilfe-Bot", "notice": None}
    assert [e["type"] for e in events] == ["chat_message"]  # no ring
    assert queued == [chat.id]  # the bot loop is asked for a turn
    async with env.factory() as db:
        stored = (
            await db.execute(
                text("SELECT status, last_visitor_seq FROM tiqora_chat_session WHERE id = :i"),
                {"i": chat.id},
            )
        ).one()
        assert (stored[0], stored[1]) == ("bot", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_human_only_topic_stays_waiting_and_marks_pending_line(env: Env) -> None:
    await _humans_online(env)
    chat, events, queued = await _start(env, topic="sales")
    assert chat.status == "waiting"
    assert chat.waiting_since is not None
    assert chat.last_visitor_seq == 1
    assert [e["type"] for e in events] == ["chat_waiting"]
    assert queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.parametrize(
    "reason", ["auto_reply_off", "no_service_user", "worker_down", "budget", "paused", "llm_none"]
)
async def test_bot_unavailable_routes_to_humans(env: Env, reason: str, llm: AsyncMock) -> None:
    await _humans_online(env)
    engine = _sync(env)
    with engine.begin() as conn:
        if reason == "auto_reply_off":
            seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE, auto_reply=False)
        elif reason == "no_service_user":
            seed_bot_queue(conn, queue_id=QUEUE, service_user_id=None)
        elif reason == "budget":
            seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE, budget_tokens_day=100)
            seed_ai_usage(conn, queue_id=QUEUE, tokens=150)
        elif reason == "paused":
            set_auto_reply_paused_sync(conn, True)
    engine.dispose()
    if reason == "worker_down":
        await env.redis.delete(BOT_ALIVE_KEY)
    if reason == "llm_none":
        llm.return_value = None
    chat, events, queued = await _start(env)
    assert chat.status == "waiting"
    assert chat.waiting_since is not None
    assert [e["type"] for e in events] == ["chat_waiting"]
    assert events[0]["topic_ref"] == f"{CONFIG}:support"
    assert queued == []
    assert [m.body for m in await _messages(env, chat.id)] == ["Mein Router blinkt rot"]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_offline_mode_ai_keeps_bot_without_humans(env: Env) -> None:
    # no agent online at all
    chat, events, queued = await _start(env, CONFIG_AI_OFFLINE, KEY_AI_OFFLINE)
    assert chat.status == "bot"
    assert [e["type"] for e in events] == ["chat_message"]
    assert queued == [chat.id]
    # a topic without the AI behaves like "form": nobody there -> refused
    with pytest.raises(ChatError) as human_only:
        await _start(env, CONFIG_AI_OFFLINE, KEY_AI_OFFLINE, topic="sales")
    assert (human_only.value.code, human_only.value.http_status) == ("offline", 409)


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_offline_mode_form_refuses_without_humans(env: Env) -> None:
    with pytest.raises(ChatError) as refused:
        await _start(env, CONFIG_FORM, KEY_FORM)
    assert (refused.value.code, refused.value.http_status) == ("offline", 409)
    async with env.factory() as db:
        count = (
            await db.execute(
                text("SELECT COUNT(*) FROM tiqora_chat_session WHERE config_id = :c"),
                {"c": CONFIG_FORM},
            )
        ).scalar_one()
    assert count == 0


# -- availability and public config -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_config_availability_reports_ai_only_topic_online(env: Env) -> None:
    for config_id, expected in (
        (CONFIG_AI_OFFLINE, {"support": True, "sales": False}),
        (CONFIG, {"support": False, "sales": False}),
    ):
        cfg, values = await _config(env, config_id)
        async with env.factory() as db:
            got = await config_availability(
                db,
                env.redis,
                SysConfig(db),
                config=cfg,
                values=values,
                now=NOW,
                settings=get_settings(),
            )
        assert got == expected
    # with the bot down the AI-only topic is offline again (fresh redis state, no cache)
    await env.redis.delete(BOT_ALIVE_KEY)
    env.redis.advance(11)
    cfg, values = await _config(env, CONFIG_AI_OFFLINE)
    async with env.factory() as db:
        down = await config_availability(
            db,
            env.redis,
            SysConfig(db),
            config=cfg,
            values=values,
            now=NOW,
            settings=get_settings(),
        )
    assert down == {"support": False, "sales": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_public_config_ai_first_and_bot_name(env: Env) -> None:
    await _humans_online(env)
    cfg, values = await _config(env, CONFIG)
    async with env.factory() as db:
        ai_first = await _ai_first_topics(db, env.redis, get_settings(), cfg, values)
        out = _public_config(values, {"support": True, "sales": True}, ai_first=ai_first)
    assert out.bot_name == "Hilfe-Bot"
    assert {t.key: t.ai_first for t in out.topics} == {"support": True, "sales": False}
    cfg2, values2 = await _config(env, CONFIG_AI_OFFLINE)
    assert _public_config(values2, {}).offline_mode == "ai"
    # bot down: ai_first goes away (the cache is keyed per queue, so wait it out)
    await env.redis.delete(BOT_ALIVE_KEY)
    env.redis.advance(11)
    async with env.factory() as db:
        down = await _ai_first_topics(db, env.redis, get_settings(), cfg2, values2)
    assert down == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_availability_cached_ten_seconds(env: Env, llm: AsyncMock) -> None:
    async def ask(**kw: Any) -> Any:
        async with env.factory() as db:
            return await bot_availability(
                db,
                env.redis,
                get_settings(),
                config_id=CONFIG,
                topic=FIRST_LINE,
                queue_id=QUEUE,
                **kw,
            )

    assert (await ask()).ok is True
    assert llm.await_count == 1
    assert (await ask()).ok is True
    assert llm.await_count == 1  # second answer from the cache
    assert (await ask(use_cache=False)).ok is True
    assert llm.await_count == 2  # the turn bypasses the cache
    env.redis.advance(11)
    assert (await ask()).ok is True
    assert llm.await_count == 3  # expired
    # the cache key carries the queue: another queue is a fresh question
    async with env.factory() as db:
        other = await bot_availability(
            db, env.redis, get_settings(), config_id=CONFIG, topic=FIRST_LINE, queue_id=QUEUE_PLAIN
        )
    assert (other.ok, other.reason) == (False, "no_policy")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_availability_reasons(env: Env, llm: AsyncMock) -> None:
    async def ask(topic: ChatTopic = FIRST_LINE, queue_id: int = QUEUE) -> tuple[bool, str | None]:
        async with env.factory() as db:
            got = await bot_availability(
                db,
                env.redis,
                get_settings(),
                config_id=CONFIG,
                topic=topic,
                queue_id=queue_id,
                use_cache=False,
            )
        return got.ok, got.reason

    assert await ask(HUMAN_ONLY, QUEUE_PLAIN) == (False, "ai_mode_off")
    assert await ask() == (True, None)
    engine = _sync(env)
    with engine.begin() as conn:
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE, autonomy="off")
    assert await ask() == (False, "autonomy_off")
    with engine.begin() as conn:
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE, max_replies_per_hour=1)
        seed_ai_usage(conn, queue_id=QUEUE, tokens=1)
    assert await ask() == (False, "queue_rate_limit")
    engine.dispose()
    llm.side_effect = RuntimeError("boom")  # never raises: the class name is the reason
    engine = _sync(env)
    with engine.begin() as conn:
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE)
    engine.dispose()
    assert await ask() == (False, "RuntimeError")
    llm.side_effect = None
    await env.redis.delete(BOT_ALIVE_KEY)
    assert await ask() == (False, "worker_down")
    env.redis.fail = True
    assert await ask() == (False, "worker_down")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_queue_cap_reason_orders_hourly_global_budget(env: Env) -> None:
    engine = _sync(env)
    with engine.begin() as conn:
        seed_bot_queue(
            conn,
            queue_id=QUEUE,
            service_user_id=SERVICE,
            budget_tokens_day=100,
            max_replies_per_hour=5,
        )
    async with env.factory() as db:
        policy = (
            await db.execute(
                text("SELECT id FROM tiqora_ai_queue_policy WHERE queue_id = :q"), {"q": QUEUE}
            )
        ).scalar_one()
        row = await db.get(TiqoraAiQueuePolicy, policy)
        assert row is not None
        assert await auto_reply_queue_cap_reason(db, queue_id=QUEUE, policy=row) is None
    with engine.begin() as conn:
        seed_ai_usage(conn, queue_id=QUEUE, tokens=200)
    engine.dispose()
    async with env.factory() as db:
        row = await db.get(TiqoraAiQueuePolicy, policy)
        assert row is not None
        assert (
            await auto_reply_queue_cap_reason(db, queue_id=QUEUE, policy=row) == "budget_tokens_day"
        )


# -- notifications ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_queue_bot_turn_publishes_after_commit_and_dedupes(env: Env) -> None:
    async with env.factory() as db:
        queue_bot_turn(db, "s" * 32)
        queue_bot_turn(db, "s" * 32)
        await flush_chat_notifications(db, env.redis)
        assert BOT_QUEUE_KEY not in env.redis._lists  # nothing before the commit
        await db.commit()
        await flush_chat_notifications(db, env.redis)
        assert env.redis._lists[BOT_QUEUE_KEY] == ["s" * 32]
        # a second turn inside the dedupe window is not queued twice
        queue_bot_turn(db, "s" * 32)
        await db.commit()
        await flush_chat_notifications(db, env.redis)
        assert env.redis._lists[BOT_QUEUE_KEY] == ["s" * 32]
        # a rolled-back turn is never published (a transaction must exist, as in real writes)
        await db.execute(text("SELECT 1"))
        queue_bot_turn(db, "t" * 32)
        await db.rollback()
        await flush_chat_notifications(db, env.redis)
        assert env.redis._lists[BOT_QUEUE_KEY] == ["s" * 32]
        # after the window it is queued again
        env.redis.advance(121)
        queue_bot_turn(db, "s" * 32)
        await db.commit()
        await flush_chat_notifications(db, env.redis)
        assert env.redis._lists[BOT_QUEUE_KEY] == ["s" * 32, "s" * 32]
