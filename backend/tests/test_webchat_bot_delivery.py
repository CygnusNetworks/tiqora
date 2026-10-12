"""AI answers in a web chat: runtime dispatch, takeover, tone, intro (band 54540-54579)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._ai_scripted_llm import ScriptedLlm, propose_response
from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_bot_fixtures import cleanup_bot_queue, seed_bot_queue
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage, ToolCall
from tiqora.ai.runtime import TRIGGER_AUTO, AgentRunResult, run_ticket_agent
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession
from tiqora.webchat.bot import BOT_ALIVE_KEY
from tiqora.webchat.bot_delivery import (
    DEFAULT_WEBCHAT_TONE_PROMPT,
    deliver_bot_chat_reply,
    post_bot_intro,
    webchat_tone_prompt,
)
from tiqora.webchat.hub import ChatHub, publish_chat_typing
from tiqora.webchat.models import ChatConfigValues, ChatTopic
from tiqora.webchat.notify import flush_chat_notifications
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import (
    VisitorContext,
    create_session,
    lock_session,
    post_visitor_message,
)
from tiqora.webchat.store import config_values, load_config, load_messages_since
from tiqora.webchat.stream import visitor_event_stream
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, SERVICE = 54540, 54541, 54542
CONFIG, CONFIG_TONE = 54545, 54546
CONFIG_IDS = range(54540, 54580)
KEY, KEY_TONE = "wc_" + "d" * 32, "wc_" + "e" * 32
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
DISCLOSURE = "Dies ist eine KI-Antwort des Support-Teams."
# Tables the AI runtime writes next to the Znuny ones (children before parents).
AI_MARK_TABLES = ("tiqora_ai_audit_log", "tiqora_ai_usage", "tiqora_ai_draft")


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
        cleanup_bot_queue(conn, queue_ids=[QUEUE])
        cleanup_band(
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE],
            group_ids=[GROUP],
            user_ids=[SERVICE],
        )
    engine.dispose()


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis

    def sql(self, statement: str, **params: Any) -> list[Any]:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, (*AI_MARK_TABLES, *ZNUNY_MARK_TABLES))
    ai_first = {
        "SELECT COALESCE(MAX(ticket_id), 0) FROM tiqora_ai_ticket_state",
        "SELECT COALESCE(MAX(article_id), 0) FROM tiqora_ai_article_origin",
    }
    engine = create_engine(url)
    with engine.begin() as conn:
        state_mark, origin_mark = (
            int(conn.execute(text(q)).scalar_one()) for q in sorted(ai_first)
        )
        # The queue's group is the service user's too (it writes the articles as that user).
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=SERVICE, login="wc54-bot")
        insert_config(
            conn,
            config_id=CONFIG,
            queue_id=QUEUE,
            public_key=KEY,
            origins=[ORIGIN],
            topics=[FIRST_LINE],
            values_override={"default_customer_user": None, "offline_mode": "ai"},
        )
        insert_config(
            conn,
            config_id=CONFIG_TONE,
            queue_id=QUEUE,
            public_key=KEY_TONE,
            origins=[ORIGIN],
            topics=[FIRST_LINE],
            values_override={
                "default_customer_user": None,
                "offline_mode": "ai",
                "bot_tone_prompt": "Antworten Sie knapp und im Stil eines Piraten.",
            },
        )
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE)
        conn.execute(
            text(
                "UPDATE tiqora_ai_queue_policy SET allowed_state_types = :t,"
                " ai_disclosure_enabled = :on, ai_disclosure_text = :d WHERE queue_id = :q"
            ),
            {"t": '["open", "closed"]', "on": True, "d": DISCLOSURE, "q": QUEUE},
        )
        enable_webchat(conn)
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    redis = FakeRedis()
    await redis.set(BOT_ALIVE_KEY, "1", ex=30)
    yield Env(url, factory, redis)
    await aengine.dispose()
    cleanup = create_engine(url)
    with cleanup.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_ai_article_origin WHERE article_id > :m"), {"m": origin_mark}
        )
        conn.execute(
            text("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id > :m"), {"m": state_mark}
        )
    cleanup.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


@pytest.fixture(autouse=True)
def llm_available(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=object())
    monkeypatch.setattr("tiqora.webchat.bot.build_task_llm", mock)
    return mock


# -- helpers -------------------------------------------------------------------------------


async def _start(env: Env, config_id: int = CONFIG, key: str = KEY) -> TiqoraChatSession:
    """A chat that starts with the bot (``offline_mode == "ai"``: no human needed)."""
    async with env.factory() as db:
        cfg = await load_config(db, config_id)
        assert cfg is not None
        chat, _token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=config_values(cfg),
            payload=ChatSessionCreateIn(
                key=key,
                origin=ORIGIN,
                visitor_id=VISITOR,
                topic="support",
                prechat={"name": "Erika", "email": "erika@example.org"},
                message="Mein Router blinkt rot",
                client_id="c-1",
            ),
            ctx=CTX,
            now=NOW,
            settings=get_settings(),
        )
        await db.commit()
        await flush_chat_notifications(db, env.redis)
    assert chat.status == "bot"
    return chat


async def _run(
    env: Env, chat: TiqoraChatSession, llm: Any, *, run_id: str = "r1"
) -> AgentRunResult:
    async with env.factory() as db:
        result = await run_ticket_agent(
            db,
            settings=get_settings(),
            llm=llm,
            ticket_id=int(chat.ticket_id),
            trigger=TRIGGER_AUTO,
            acting_user_id=None,
            run_id=run_id,
            worker_instance="chat-bot",
            source_channel="webchat",
        )
        await db.commit()
        await flush_chat_notifications(db, env.redis)
    return result


async def _lines(env: Env, chat: TiqoraChatSession) -> list[TiqoraChatMessage]:
    async with env.factory() as db:
        return list(await load_messages_since(db, chat.id, 0))


def _ticket_state_id(env: Env, chat: TiqoraChatSession) -> int:
    return int(
        env.sql("SELECT ticket_state_id FROM ticket WHERE id = :t", t=int(chat.ticket_id))[0][0]
    )


def _system_prompt(llm: ScriptedLlm) -> str:
    return next(
        m.content for m in llm.last_messages if isinstance(m, LlmMessage) and m.role == "system"
    )


# -- runtime dispatch ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_answer_becomes_bot_line_and_article(env: Env) -> None:
    chat = await _start(env)
    state_before = _ticket_state_id(env, chat)
    llm = ScriptedLlm([propose_response("reply", "Starten Sie den Router bitte neu.")])
    env.redis.published.clear()
    result = await _run(env, chat, llm)
    assert result.status == "sent"
    line = (await _lines(env, chat))[-1]
    assert (line.sender, line.body) == ("bot", "Starten Sie den Router bitte neu.")
    assert json.loads(line.meta or "{}")["agent_name"] == "KI-Assistent"
    assert line.article_id == result.article_id
    article = env.sql(
        "SELECT st.name, a.is_visible_for_customer, cc.name, a.create_by, m.a_body, m.a_to"
        " FROM article a JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
        a=result.article_id,
    )[0]
    assert article[0] == "agent"
    assert bool(article[1]) is True
    assert article[2] == "WebChat"
    assert article[3] == SERVICE
    # no disclosure footer although the policy has one: the bot_intro line carries the notice
    assert article[4] == "Starten Sie den Router bitte neu."
    assert "erika@example.org" in article[5]
    history = env.sql(
        "SELECT ht.name FROM ticket_history h JOIN ticket_history_type ht"
        " ON ht.id = h.history_type_id WHERE h.article_id = :a",
        a=result.article_id,
    )
    assert "SendAnswer" in {row[0] for row in history}
    origin = env.sql(
        "SELECT source, service_user_id FROM tiqora_ai_article_origin WHERE article_id = :a",
        a=result.article_id,
    )
    assert origin == [("auto", SERVICE)]
    # the policy would allow parking the ticket, but a live chat is never parked
    assert _ticket_state_id(env, chat) == state_before
    payloads = [json.loads(p) for _, p in env.redis.published]
    assert {"type": "wake"} in payloads
    assert any(p.get("type") == "chat_message" and p.get("session_id") == chat.id for p in payloads)


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_webchat_tone_prompt_in_system_prompt(env: Env) -> None:
    chat = await _start(env)
    llm = ScriptedLlm([propose_response("reply", "Neu starten bitte.")])
    await _run(env, chat, llm)
    assert DEFAULT_WEBCHAT_TONE_PROMPT in _system_prompt(llm)

    tone_chat = await _start(env, CONFIG_TONE, KEY_TONE)
    tone_llm = ScriptedLlm([propose_response("reply", "Arr, neu starten.")])
    await _run(env, tone_chat, tone_llm, run_id="r2")
    prompt = _system_prompt(tone_llm)
    assert "Antworten Sie knapp und im Stil eines Piraten." in prompt
    assert DEFAULT_WEBCHAT_TONE_PROMPT not in prompt
    async with env.factory() as db:
        assert await webchat_tone_prompt(db, ticket_id=int(chat.ticket_id)) == (
            DEFAULT_WEBCHAT_TONE_PROMPT
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_takeover_during_turn_stores_draft_not_message(env: Env) -> None:
    chat = await _start(env)
    before = await _lines(env, chat)

    async def agent_accepts() -> None:
        env.sql(
            "UPDATE tiqora_chat_session SET status = 'active', accepted_at = :t WHERE id = :s",
            t=NOW,
            s=chat.id,
        )

    llm = ScriptedLlm([propose_response("reply", "Zu spät.")], on_call=agent_accepts)
    result = await _run(env, chat, llm)
    assert result.status == "drafted"
    assert result.notes == "chat_taken_over"
    assert [m.id for m in await _lines(env, chat)] == [m.id for m in before]
    drafts = env.sql(
        "SELECT body, source FROM tiqora_ai_draft WHERE ticket_id = :t", t=int(chat.ticket_id)
    )
    assert drafts == [("Zu spät.", "auto")]
    assert env.sql("SELECT status FROM tiqora_chat_session WHERE id = :s", s=chat.id) == [
        ("active",)
    ]
    assert not env.sql(
        "SELECT 1 FROM tiqora_ai_article_origin WHERE queue_id = :q AND article_id IN"
        " (SELECT id FROM article WHERE ticket_id = :t)",
        q=QUEUE,
        t=int(chat.ticket_id),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_unsendable_chat_message_becomes_draft(env: Env) -> None:
    chat = await _start(env)
    llm = ScriptedLlm([propose_response("reply", "x" * 20000)])
    result = await _run(env, chat, llm)
    assert result.status == "drafted"
    assert result.notes == "chat_message_rejected"
    assert [m.sender for m in await _lines(env, chat)] == ["visitor", "system"]


# -- intro -----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_intro_uses_policy_disclosure_text(env: Env) -> None:
    chat = await _start(env)
    intro = (await _lines(env, chat))[1]
    assert intro.body == "bot_intro"
    assert json.loads(intro.meta or "{}") == {"name": "KI-Assistent", "notice": DISCLOSURE}
    env.sql(
        "UPDATE tiqora_ai_queue_policy SET ai_disclosure_text = '  ' WHERE queue_id = :q", q=QUEUE
    )
    async with env.factory() as db:
        again = await post_bot_intro(
            db,
            chat=chat,
            values=ChatConfigValues(topics=[FIRST_LINE], bot_name="Hilfe-Bot"),
            notice="  ",
            now=NOW,
        )
        await db.commit()
    assert json.loads(again.meta or "{}") == {"name": "Hilfe-Bot", "notice": None}


# -- locking ---------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_tool_writes_committed_before_session_lock(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = await _start(env)

    async def no_usage_commit(*_args: Any, **_kwargs: Any) -> None:
        # ``record_usage`` commits as a side effect and would hide a missing commit before
        # the delivery; the commit under test must be the only one between tool and lock.
        return None

    monkeypatch.setattr("tiqora.ai.usage.record_usage", no_usage_commit)
    follow_up: list[asyncio.Task[Any]] = []

    async def visitor_writes_meanwhile() -> None:
        # A visitor line takes the chat row first and then writes the ticket (R-LOCK-ORDER).
        # It is started before the run's tools run and holds the chat row for a while. The
        # tool writes the ticket in the run's transaction; if the run then waited for the
        # chat row without committing first, the two would wait for each other (a deadlock
        # error on both dialects).
        async def post() -> None:
            async with env.factory() as db:
                await lock_session(db, chat.id)
                await asyncio.sleep(1.5)
                await post_visitor_message(
                    db,
                    SysConfig(db),
                    chat=chat,
                    body="Noch eine Frage",
                    client_id="c-2",
                    now=NOW,
                )
                await db.commit()

        follow_up.append(asyncio.create_task(post()))

    # One response: the ticket write and the answer (the tools run after the model call, so no
    # model-call bookkeeping commit happens between the write and the delivery).
    both = LlmResponse(
        content=None,
        tool_calls=[
            ToolCall(id="call_s", name="update_ticket_fields", arguments={"state": "open"}),
            ToolCall(
                id="call_p",
                name="propose_customer_message",
                arguments={"kind": "reply", "subject": "Re: Hilfe", "body": "Bitte neu starten."},
            ),
        ],
        usage=LlmUsage(prompt_tokens=6, completion_tokens=3),
    )
    llm = ScriptedLlm([both], on_call=visitor_writes_meanwhile)
    async with asyncio.timeout(20):
        result = await _run(env, chat, llm)
        await asyncio.gather(*follow_up)
    assert result.status == "sent"
    assert _ticket_state_id(env, chat) == 4  # the tool's write is committed ("open")
    lines = await _lines(env, chat)
    assert [m.sender for m in lines].count("visitor") == 2  # the visitor's line is committed too
    assert [m.sender for m in lines].count("bot") == 1


# -- typing ----------------------------------------------------------------------------------


class _Request:
    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_typing_published_with_who_bot(env: Env) -> None:
    chat = await _start(env)
    await publish_chat_typing(env.redis, chat.id, who="bot")
    assert json.loads(env.redis.published[-1][1]) == {"type": "typing", "who": "bot"}
    await publish_chat_typing(env.redis, chat.id)
    assert json.loads(env.redis.published[-1][1]) == {"type": "typing", "who": "agent"}

    hub = ChatHub(env.redis, max_streams=10)
    gen = visitor_event_stream(
        cast(Any, _Request()),
        hub=hub,
        session_factory=env.factory,
        redis_client=env.redis,
        session_id=chat.id,
        after_seq=99,
    )
    try:
        first = await asyncio.wait_for(anext(gen), timeout=5)
        assert first.startswith(b"event: session\n")
        await publish_chat_typing(env.redis, chat.id, who="bot")
        assert await asyncio.wait_for(anext(gen), timeout=5) == (
            b'event: typing\ndata: {"who": "bot"}\n\n'
        )
        await publish_chat_typing(env.redis, chat.id)
        assert await asyncio.wait_for(anext(gen), timeout=5) == (
            b'event: typing\ndata: {"who": "agent"}\n\n'
        )
    finally:
        await gen.aclose()
        await hub.aclose()


# -- delivery refusal ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_deliver_refuses_a_chat_that_is_not_with_the_bot(env: Env) -> None:
    from tiqora.webchat.bot_delivery import BotChatTakenOver

    chat = await _start(env)
    env.sql("UPDATE tiqora_chat_session SET status = 'waiting' WHERE id = :s", s=chat.id)
    async with env.factory() as db:
        with pytest.raises(BotChatTakenOver):
            await deliver_bot_chat_reply(
                db,
                SysConfig(db),
                ticket_id=int(chat.ticket_id),
                service_user_id=SERVICE,
                subject="Re: x",
                body="Hallo",
            )
        await db.rollback()
    assert [m.sender for m in await _lines(env, chat)] == ["visitor", "system"]
