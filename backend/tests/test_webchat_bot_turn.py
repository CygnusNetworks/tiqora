"""Bot turn, claim and handoff of a web chat (band 54590-54629, both dialects)."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._ai_scripted_llm import (
    ScriptedLlm,
    escalate_response,
    no_reply_response,
    propose_response,
)
from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_bot_fixtures import cleanup_bot_queue, seed_ai_usage, seed_bot_queue
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.ai.llm import LlmHttpError
from tiqora.ai.models import TiqoraAiQueuePolicy, TiqoraAiTicketState
from tiqora.ai.runtime import resolve_auto_state_id
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession, TiqoraSettings
from tiqora.domain.settings_store import KEY_AI_GLOBAL_REPLIES_PER_HOUR
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.bot import (
    BOT_ALIVE_KEY,
    BOT_CLAIM_STALE_SECONDS,
    claim_bot_turn,
    hand_off_chat,
    make_claim_owner,
    run_chat_bot_turn,
)
from tiqora.webchat.models import ChatTopic
from tiqora.webchat.notify import BOT_QUEUE_KEY, flush_chat_notifications
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import (
    VisitorContext,
    create_session,
    end_by_visitor,
    post_visitor_message,
    set_contact,
)
from tiqora.webchat.store import config_values, load_config, load_messages_since
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, SERVICE, AGENT = 54590, 54591, 54592, 54593
CONFIG = 54595
CONFIG_IDS = range(54590, 54630)
KEY = "wc_" + "f" * 32
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
AI_MARK_TABLES = ("tiqora_ai_audit_log", "tiqora_ai_usage", "tiqora_ai_draft")
OWNER = "t:1"


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
        conn.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key == KEY_AI_GLOBAL_REPLIES_PER_HOUR)
        )
        # the daily-budget limit alert remembers the day it fired for the queue
        conn.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key.like(f"ai.limit_alert.%.{QUEUE}.%"))
        )
        cleanup_band(
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE],
            group_ids=[GROUP],
            user_ids=[SERVICE, AGENT],
        )
    engine.dispose()


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis
    llm: ScriptedLlm = field(default_factory=lambda: ScriptedLlm([]))

    def sql(self, statement: str, **params: Any) -> list[Any]:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()

    def row(self, chat: TiqoraChatSession) -> Any:
        return self.sql(
            "SELECT status, end_reason, bot_answered_seq, last_visitor_seq, bot_attempts,"
            " bot_outcome, bot_claimed_at, bot_claim_owner, handoff_reason, handoff_at"
            " FROM tiqora_chat_session WHERE id = :i",
            i=chat.id,
        )[0]


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, (*AI_MARK_TABLES, *ZNUNY_MARK_TABLES))
    engine = create_engine(url)
    with engine.begin() as conn:
        state_mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(ticket_id), 0) FROM tiqora_ai_ticket_state")
            ).scalar_one()
        )
        origin_mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(article_id), 0) FROM tiqora_ai_article_origin")
            ).scalar_one()
        )
        # The queue's group is the service user's too (it writes the articles as that user).
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=SERVICE, login="wc54-bot2")
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'wc54-human2', 'x', 'Chat', 'Human', 1, :t, 1, :t, 1)"
            ),
            {"uid": AGENT, "t": NOW},
        )
        for key in ("ro", "rw"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": AGENT, "gid": GROUP, "k": key, "t": NOW},
            )
        insert_config(
            conn,
            config_id=CONFIG,
            queue_id=QUEUE,
            public_key=KEY,
            origins=[ORIGIN],
            topics=[FIRST_LINE],
            values_override={"default_customer_user": None, "offline_mode": "ai"},
        )
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE)
        conn.execute(
            text("UPDATE tiqora_ai_queue_policy SET allowed_state_types = :t WHERE queue_id = :q"),
            {"t": '["open", "closed"]', "q": QUEUE},
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
def llm_patches(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Availability sees a dummy chain; the turn's ``prepare_auto_run`` gets the test's LLM."""
    monkeypatch.setattr("tiqora.webchat.bot.build_task_llm", AsyncMock(return_value=object()))

    async def fake_build(*_args: Any, **_kwargs: Any) -> Any:
        env = request.getfixturevalue("env")
        return SimpleNamespace(client=env.llm, models=[], profile_id=1, profile_name="t")

    monkeypatch.setattr("tiqora.ai.auto_run.build_task_llm", fake_build)


# -- helpers -------------------------------------------------------------------------------


async def _start(env: Env) -> TiqoraChatSession:
    async with env.factory() as db:
        cfg = await load_config(db, CONFIG)
        assert cfg is not None
        chat, _token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=config_values(cfg),
            payload=ChatSessionCreateIn(
                key=KEY,
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


async def _visitor_line(env: Env, chat: TiqoraChatSession, body: str, cid: str) -> None:
    async with env.factory() as db:
        await post_visitor_message(db, SysConfig(db), chat=chat, body=body, client_id=cid, now=NOW)
        await db.commit()
        await flush_chat_notifications(db, env.redis)


async def _turn(env: Env, chat: TiqoraChatSession, owner: str = OWNER) -> str:
    return await run_chat_bot_turn(
        env.factory, get_settings(), env.redis, session_id=chat.id, owner=owner
    )


async def _humans_online(env: Env) -> None:
    async with env.factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(env.redis, AGENT)


async def _codes(env: Env, chat: TiqoraChatSession) -> list[str]:
    async with env.factory() as db:
        return [m.body for m in await load_messages_since(db, chat.id, 0) if m.sender == "system"]


async def _lines(env: Env, chat: TiqoraChatSession) -> list[TiqoraChatMessage]:
    async with env.factory() as db:
        return list(await load_messages_since(db, chat.id, 0))


def _bot_lines(lines: list[TiqoraChatMessage]) -> list[str]:
    return [m.body for m in lines if m.sender == "bot"]


def _notes(env: Env, chat: TiqoraChatSession) -> list[str]:
    return [
        r[0]
        for r in env.sql(
            "SELECT m.a_body FROM article a JOIN article_data_mime m ON m.article_id = a.id"
            " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
            " WHERE a.ticket_id = :t AND a.is_visible_for_customer = 0 AND st.name = 'agent'"
            " ORDER BY a.id",
            t=int(chat.ticket_id),
        )
    ]


def _published(env: Env) -> list[dict[str, Any]]:
    return [json.loads(p) for _, p in env.redis.published]


def _set_ticket_state_row(env: Env, chat: TiqoraChatSession, **values: Any) -> None:
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(insert(TiqoraAiTicketState).values(ticket_id=int(chat.ticket_id), **values))
    engine.dispose()


# -- the answer ----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_turn_replies_and_advances_seq(env: Env) -> None:
    chat = await _start(env)
    env.llm = ScriptedLlm([propose_response("reply", "Starten Sie den Router bitte neu.")])
    assert await _turn(env, chat) == "replied"
    row = env.row(chat)
    # status, end_reason, answered, last_visitor, attempts, outcome, claimed_at, owner
    assert row[0] == "bot"
    assert (row[2], row[3], row[4], row[5]) == (1, 1, 0, "replied")
    assert row[6] is None and row[7] is None
    assert _bot_lines(await _lines(env, chat)) == ["Starten Sie den Router bitte neu."]
    usage = env.sql("SELECT feature, queue_id FROM tiqora_ai_usage WHERE queue_id = :q", q=QUEUE)
    assert usage == [("auto_reply", QUEUE)]
    assert env.llm.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_reply_publishes_wake(env: Env) -> None:
    chat = await _start(env)
    env.llm = ScriptedLlm([propose_response("reply", "Bitte neu starten.")])
    env.redis.published.clear()
    assert await _turn(env, chat) == "replied"
    payloads = _published(env)
    assert {"type": "wake"} in payloads
    assert any(p.get("type") == "chat_message" and p.get("session_id") == chat.id for p in payloads)
    # the visitor stream was told the bot is typing
    assert {"type": "typing", "who": "bot"} in payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_usage_counts_against_queue_budget(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_ai_queue_policy SET budget_tokens_day = 15 WHERE queue_id = :q"),
            {"q": QUEUE},
        )
    engine.dispose()
    env.llm = ScriptedLlm([propose_response("reply", "Bitte neu starten.")])  # 10 + 5 tokens
    assert await _turn(env, chat) == "replied"
    await _visitor_line(env, chat, "Es blinkt immer noch", "c-2")
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "limit")
    assert env.llm.calls == 1  # the second turn never reached the model


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.parametrize(
    "reason",
    [
        "queue_rate_limit",
        "global_rate_limit",
        "budget_tokens_day",
        "max_auto_replies",
        "max_clarifications",
        "grant_used",
    ],
)
async def test_cap_reason_hands_off(reason: str, env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        if reason == "queue_rate_limit":
            conn.execute(
                text(
                    "UPDATE tiqora_ai_queue_policy SET max_replies_per_hour = 1 WHERE queue_id = :q"
                ),
                {"q": QUEUE},
            )
            seed_ai_usage(conn, queue_id=QUEUE, tokens=1)
        elif reason == "global_rate_limit":
            conn.execute(
                insert(TiqoraSettings).values(key=KEY_AI_GLOBAL_REPLIES_PER_HOUR, value="1")
            )
            seed_ai_usage(conn, queue_id=QUEUE, tokens=1)
        elif reason == "budget_tokens_day":
            conn.execute(
                text(
                    "UPDATE tiqora_ai_queue_policy SET budget_tokens_day = 10 WHERE queue_id = :q"
                ),
                {"q": QUEUE},
            )
            seed_ai_usage(conn, queue_id=QUEUE, tokens=10)
    engine.dispose()
    if reason == "max_auto_replies":
        _set_ticket_state_row(env, chat, auto_reply_count=99)
    elif reason == "max_clarifications":
        _set_ticket_state_row(env, chat, clarification_count=99)
    elif reason == "grant_used":
        _set_ticket_state_row(env, chat, ai_grant_remaining=0, ai_grant_total=3)
    env.llm = ScriptedLlm([propose_response("reply", "never sent")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8], row[5]) == ("waiting", "limit", "handoff")
    assert env.llm.calls == 0
    assert _bot_lines(await _lines(env, chat)) == []


# -- handoffs ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_ai_escalation_hands_off_with_ai_note(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    env.llm = ScriptedLlm([escalate_response("Kunde braucht eine Vertragsänderung")])
    env.redis.published.clear()
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8], row[5]) == ("waiting", "ai", "handoff")
    assert row[9] is not None
    assert _notes(env, chat) == ["Escalated to human: Kunde braucht eine Vertragsänderung"]
    codes = await _codes(env, chat)
    assert codes[-1] == "handoff" and "no_agent" not in codes
    flagged = env.sql(
        "SELECT ai_escalated_at FROM tiqora_ai_ticket_state WHERE ticket_id = :t",
        t=int(chat.ticket_id),
    )
    assert flagged[0][0] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_ai_handoff_publishes_ring(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    env.llm = ScriptedLlm([escalate_response("Kunde braucht eine Vertragsänderung")])
    env.redis.published.clear()
    assert await _turn(env, chat) == "handoff"
    rings = [p for p in _published(env) if p.get("type") == "chat_waiting"]
    assert len(rings) == 1
    assert rings[0]["session_id"] == chat.id
    assert rings[0]["queue_id"] == QUEUE
    assert rings[0]["topic_ref"] == f"{CONFIG}:support"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_without_humans_ends_no_agent(env: Env) -> None:
    chat = await _start(env)  # nobody is online
    env.llm = ScriptedLlm([escalate_response("Kunde braucht eine Vertragsänderung")])
    env.redis.published.clear()
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[1], row[8]) == ("ended", "no_agent", "ai")
    lines = await _lines(env, chat)
    system = [(m.body, json.loads(m.meta or "{}")) for m in lines if m.sender == "system"]
    assert [c for c, _ in system][-2:] == ["handoff", "no_agent"]
    assert system[-1][1] == {"has_email": True}
    assert any(p.get("type") == "chat_ended" for p in _published(env))
    assert not any(p.get("type") == "chat_waiting" for p in _published(env))


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_no_agent_handoff_lets_an_anonymous_visitor_leave_an_address(env: Env) -> None:
    # An anonymous visitor (no typed address) gets the phase-1 contact path after the handoff.
    async with env.factory() as db:
        cfg = await load_config(db, CONFIG)
        assert cfg is not None
        chat, _t, _f = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=config_values(cfg),
            payload=ChatSessionCreateIn(
                key=KEY,
                origin=ORIGIN,
                visitor_id=VISITOR,
                topic="support",
                prechat={},
                message="Mein Router blinkt rot",
            ),
            ctx=CTX,
            now=NOW,
            settings=get_settings(),
        )
        await db.commit()
    env.llm = ScriptedLlm([no_reply_response("nothing to say")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[1]) == ("ended", "no_agent")
    lines = await _lines(env, chat)
    last = [m for m in lines if m.sender == "system"][-1]
    assert (last.body, json.loads(last.meta or "{}")) == ("no_agent", {"has_email": False})
    async with env.factory() as db:
        await set_contact(db, SysConfig(db), chat=chat, name=None, email="neu@example.org", now=NOW)
        await db.commit()
    stored = env.sql("SELECT visitor_email FROM tiqora_chat_session WHERE id = :i", i=chat.id)
    assert stored == [("neu@example.org",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_draft_destination_hands_off(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_ai_queue_policy SET autonomy = 'off' WHERE queue_id = :q"),
            {"q": QUEUE},
        )
    engine.dispose()
    env.llm = ScriptedLlm([propose_response("reply", "Entwurf")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "unavailable")  # autonomy off: the bot cannot answer
    assert env.llm.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_clarify_only_draft_hands_off_with_ai_note(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_ai_queue_policy SET autonomy = 'clarify_only' WHERE queue_id = :q"),
            {"q": QUEUE},
        )
    engine.dispose()
    env.llm = ScriptedLlm([propose_response("reply", "Starten Sie neu.")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "ai")
    assert "Die KI hat einen Entwurf vorbereitet" in _notes(env, chat)[-1]
    assert _bot_lines(await _lines(env, chat)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_policy_switched_off_mid_chat_hands_off_unavailable(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            update(TiqoraAiQueuePolicy)
            .where(TiqoraAiQueuePolicy.queue_id == QUEUE)
            .values(enabled_auto_reply=False)
        )
    engine.dispose()
    env.llm = ScriptedLlm([propose_response("reply", "never sent")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "unavailable")
    assert env.llm.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_no_reply_hands_off(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)
    env.llm = ScriptedLlm([no_reply_response("nothing to say")])
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "ai")
    # the no-reply tool writes its own note first; the handoff adds the visitor-facing summary
    assert _notes(env, chat) == [
        "No reply sent: nothing to say",
        "Die KI hatte keine Antwort für den Besucher.",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_llm_error_three_times_hands_off(env: Env) -> None:
    await _humans_online(env)
    chat = await _start(env)

    async def boom() -> None:
        raise LlmHttpError(503, "upstream down")

    env.llm = ScriptedLlm([], on_call=boom)
    assert await _turn(env, chat) == "retry"
    assert env.row(chat)[4] == 1
    assert await _turn(env, chat) == "retry"
    assert env.row(chat)[4] == 2
    assert await _turn(env, chat) == "handoff"
    row = env.row(chat)
    assert (row[0], row[8], row[5]) == ("waiting", "error", "handoff")
    assert _notes(env, chat) == ["KI nicht erreichbar: LlmHttpError"]
    assert row[6] is None and row[7] is None  # claim cleared after every exit


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_lock_held_by_agent_draft_retries_without_counting(env: Env) -> None:
    chat = await _start(env)
    _set_ticket_state_row(env, chat, run_lock_owner="manual:agent-draft", run_lock_at=utc_now())
    env.llm = ScriptedLlm([propose_response("reply", "never sent")])
    for _ in range(4):
        assert await _turn(env, chat) == "retry"
    row = env.row(chat)
    assert (row[0], row[4]) == ("bot", 0)
    assert env.llm.calls == 0


# -- claim, supersede, requeue ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_claim_is_exclusive_both_dialects(env: Env) -> None:
    chat = await _start(env)

    async def claim(owner: str) -> bool:
        async with env.factory() as db:
            return await claim_bot_turn(db, session_id=chat.id, owner=owner, now=utc_now())

    results = await asyncio.gather(claim("a:1"), claim("b:2"))
    assert sorted(results) == [False, True]
    owner = env.row(chat)[7]
    assert owner in {"a:1", "b:2"}
    # a fresh claim is respected, a stale one is taken over
    assert await claim("c:3") is False
    async with env.factory() as db:
        later = utc_now() + timedelta(seconds=BOT_CLAIM_STALE_SECONDS + 1)
        assert await claim_bot_turn(db, session_id=chat.id, owner="c:3", now=later) is True
    assert env.row(chat)[7] == "c:3"


def test_claim_owner_fits_the_column_with_a_long_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "h" * 120 + ".example.org")
    owner = make_claim_owner()
    assert len(owner) <= 64
    assert owner.startswith("h" * 40 + ":")


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_turn_skips_when_not_bot(env: Env) -> None:
    chat = await _start(env)
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    lines_before = await _lines(env, chat)
    env.llm = ScriptedLlm([propose_response("reply", "never sent")])
    assert await _turn(env, chat) == "skipped"
    assert env.llm.calls == 0
    assert len(await _lines(env, chat)) == len(lines_before)
    assert env.row(chat)[6] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_turn_skips_when_nothing_is_pending(env: Env) -> None:
    chat = await _start(env)
    env.llm = ScriptedLlm([propose_response("reply", "Bitte neu starten.")])
    assert await _turn(env, chat) == "replied"
    assert await _turn(env, chat) == "skipped"
    assert env.llm.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_burst_supersedes_then_one_answer(env: Env) -> None:
    chat = await _start(env)

    async def burst() -> None:
        await _visitor_line(env, chat, "Und das Licht ist rot", "c-2")
        await _visitor_line(env, chat, "Hallo?", "c-3")

    env.llm = ScriptedLlm(
        [propose_response("reply", "Veraltete Antwort"), propose_response("reply", "Neu starten.")],
        on_call=burst,
    )
    env.redis.published.clear()
    assert await _turn(env, chat) == "superseded"
    assert _bot_lines(await _lines(env, chat)) == []
    row = env.row(chat)
    assert row[2] == 0 and row[3] > row[2]  # nothing answered, the later lines are pending
    # the pending lines queued the next turn at once
    assert env.redis._lists.get(BOT_QUEUE_KEY) and chat.id in env.redis._lists[BOT_QUEUE_KEY]

    async def quiet() -> None:
        return None

    env.llm._on_call = quiet
    assert await _turn(env, chat) == "replied"
    assert _bot_lines(await _lines(env, chat)) == ["Neu starten."]
    row = env.row(chat)
    assert row[2] == row[3]  # every visitor line is covered by the one answer
    assert row[3] == 4  # visitor 1, intro 2, visitor 3, visitor 4


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_pending_line_after_a_reply_queues_the_next_turn(env: Env) -> None:
    chat = await _start(env)
    late: list[bool] = []

    async def line_after_answer_was_planned() -> None:
        # stored while the turn is running, but only after the model was asked: the turn's
        # freshness check passes before this runs, so this line is NOT covered by the answer
        if not late:
            late.append(True)

    env.llm = ScriptedLlm(
        [propose_response("reply", "Neu starten.")], on_call=line_after_answer_was_planned
    )
    assert await _turn(env, chat) == "replied"
    env.redis._lists.pop(BOT_QUEUE_KEY, None)
    # a line that is stored after the answer leaves the session pending and queued
    await _visitor_line(env, chat, "Noch eine Frage", "c-2")
    assert chat.id in env.redis._lists.get(BOT_QUEUE_KEY, [])
    row = env.row(chat)
    assert row[3] > row[2]
    env.llm = ScriptedLlm([propose_response("reply", "Zweite Antwort.")])
    assert await _turn(env, chat) == "replied"
    assert _bot_lines(await _lines(env, chat)) == ["Neu starten.", "Zweite Antwort."]
    row = env.row(chat)
    assert row[2] == row[3]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_retried_visitor_line_does_not_lower_the_pending_marker(env: Env) -> None:
    chat = await _start(env)
    await _visitor_line(env, chat, "Zweite Zeile", "c-2")
    after_second = env.row(chat)[3]
    await _visitor_line(env, chat, "Erste Zeile nochmal", "c-1")  # retried client_id
    assert env.row(chat)[3] == after_second


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_stale_bot_run_lock_is_released_on_retake(env: Env) -> None:
    chat = await _start(env)
    # a dead worker left its claim and the AI run lock behind
    old = utc_now() - timedelta(seconds=BOT_CLAIM_STALE_SECONDS + 60)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tiqora_chat_session SET bot_claimed_at = :t, bot_claim_owner = 'dead:9'"
                " WHERE id = :i"
            ),
            {"t": old, "i": chat.id},
        )
    engine.dispose()
    _set_ticket_state_row(env, chat, run_lock_owner="chat-bot:dead:9", run_lock_at=utc_now())
    env.llm = ScriptedLlm([propose_response("reply", "Neu starten.")])
    assert await _turn(env, chat) == "replied"
    assert _bot_lines(await _lines(env, chat)) == ["Neu starten."]
    lock = env.sql(
        "SELECT run_lock_owner FROM tiqora_ai_ticket_state WHERE ticket_id = :t",
        t=int(chat.ticket_id),
    )
    assert lock == [(None,)]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_agent_lock_is_not_released_by_a_claim(env: Env) -> None:
    chat = await _start(env)
    _set_ticket_state_row(env, chat, run_lock_owner="manual:7", run_lock_at=utc_now())
    async with env.factory() as db:
        assert await claim_bot_turn(db, session_id=chat.id, owner=OWNER, now=utc_now()) is True
    lock = env.sql(
        "SELECT run_lock_owner FROM tiqora_ai_ticket_state WHERE ticket_id = :t",
        t=int(chat.ticket_id),
    )
    assert lock == [("manual:7",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_hand_off_is_noop_when_an_agent_was_faster(env: Env) -> None:
    chat = await _start(env)
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_chat_session SET status = 'active' WHERE id = :i"), {"i": chat.id}
        )
    engine.dispose()
    async with env.factory() as db:
        result = await hand_off_chat(
            db,
            SysConfig(db),
            env.redis,
            session_id=chat.id,
            reason="visitor",
            note="Der Besucher möchte mit einem Menschen sprechen.",
            now=NOW,
        )
        await db.commit()
    assert result == "noop"
    row = env.row(chat)
    assert (row[0], row[8], row[5]) == ("active", None, None)
    assert _notes(env, chat) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_turn_without_session_row_is_skipped(env: Env) -> None:
    assert (
        await run_chat_bot_turn(
            env.factory, get_settings(), env.redis, session_id="f" * 32, owner=OWNER
        )
        == "skipped"
    )


# -- closing the ticket of a solved chat --------------------------------------------------------


def _ticket_state_id(env: Env, chat: TiqoraChatSession) -> int:
    return int(
        env.sql("SELECT ticket_state_id FROM ticket WHERE id = :t", t=int(chat.ticket_id))[0][0]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_close_bot_resolved_ticket_on_visitor_end(env: Env) -> None:
    chat = await _start(env)
    before = _ticket_state_id(env, chat)
    env.llm = ScriptedLlm([propose_response("reply", "Neu starten.")])
    assert await _turn(env, chat) == "replied"
    assert _ticket_state_id(env, chat) == before  # a live chat's ticket is not parked
    async with env.factory() as db:
        expected = await resolve_auto_state_id(
            db, kind="reply", allowed_state_types=["open", "closed"]
        )
    assert expected is not None and expected != before
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    assert _ticket_state_id(env, chat) == expected
    assert env.row(chat)[0] == "ended"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_end_after_handoff_leaves_ticket_state(env: Env) -> None:
    chat = await _start(env)
    before = _ticket_state_id(env, chat)
    env.llm = ScriptedLlm([propose_response("reply", "Neu starten."), escalate_response("Vertrag")])
    assert await _turn(env, chat) == "replied"
    await _visitor_line(env, chat, "Reicht nicht", "c-2")
    assert await _turn(env, chat) == "handoff"
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    assert _ticket_state_id(env, chat) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_end_without_any_bot_answer_leaves_ticket_state(env: Env) -> None:
    chat = await _start(env)
    before = _ticket_state_id(env, chat)
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    assert _ticket_state_id(env, chat) == before
