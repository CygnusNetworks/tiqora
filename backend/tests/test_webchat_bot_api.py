"""Visitor handoff, agent takeover, inbox, sweep and visitor outputs of an AI-first web chat
(band 54660-54709, both dialects, real HTTP routes)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._ai_scripted_llm import ScriptedLlm, escalate_response, propose_response
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
from tiqora.ai.runtime import resolve_auto_state_id
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_redis
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatMessage, TiqoraChatSession, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import KEY_AI_GLOBAL_REPLIES_PER_HOUR
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.bot import BOT_ALIVE_KEY, run_chat_bot_turn
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.hub import ChatHub
from tiqora.webchat.models import ChatTopic
from tiqora.webchat.notify import flush_chat_notifications
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import VisitorContext, create_session
from tiqora.webchat.store import config_values, load_config, load_messages_since
from tiqora.webchat.stream import visitor_event_stream
from tiqora.webchat.sweep import run_webchat_sweep_tick
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, SERVICE, AGENT = 54660, 54661, 54662, 54663
AGENT_RO, AGENT2 = 54664, 54665
CONFIG = 54666
CONFIG_IDS = range(54660, 54710)
KEY = "wc_" + "e" * 32
SELF = "http://testserver"
API = "/api/chat/v1"
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
            user_ids=[SERVICE, AGENT, AGENT_RO, AGENT2],
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
    clear_config_cache()
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
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=SERVICE, login="wc54-bot3")
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, 'wc54-human3', 'x', 'Chat', 'Human', 1, :t, 1, :t, 1)"
            ),
            {"uid": AGENT, "t": NOW},
        )
        for uid, login in ((AGENT_RO, "wc54-ro3"), (AGENT2, "wc54-human3b")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :login, 'x', 'Chat', 'Other', 1, :t, 1, :t, 1)"
                ),
                {"uid": uid, "login": login, "t": NOW},
            )
        grants = [(AGENT, "ro"), (AGENT, "rw"), (AGENT2, "ro"), (AGENT2, "rw"), (AGENT_RO, "ro")]
        for uid, key in grants:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": uid, "gid": GROUP, "k": key, "t": NOW},
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
    clear_config_cache()
    yield Env(url, factory, redis)
    clear_config_cache()
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


async def _start(env: Env, n: int = 1) -> tuple[TiqoraChatSession, str]:
    async with env.factory() as db:
        cfg = await load_config(db, CONFIG)
        assert cfg is not None
        chat, token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=config_values(cfg),
            payload=ChatSessionCreateIn(
                key=KEY,
                origin=ORIGIN,
                visitor_id=f"visitor_wc54c_{n:010d}",
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
    return chat, token


async def _turn(env: Env, chat: TiqoraChatSession) -> str:
    return await run_chat_bot_turn(
        env.factory, get_settings(), env.redis, session_id=chat.id, owner="t:1"
    )


async def _humans_online(env: Env) -> None:
    for uid in (AGENT, AGENT2):
        async with env.factory() as db:
            await set_agent_state(db, uid, available=True, topics=[], max_concurrent=3, now=NOW)
            await db.commit()
        await mark_agent_online(env.redis, uid)


def _visitor(env: Env) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


def _agent(env: Env, user_id: int = AGENT) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    user = AuthenticatedUser(
        id=user_id,
        login=f"wc54-{user_id}",
        first_name="Chat",
        last_name="Agent",
        auth_method="session",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


def _h(token: str) -> dict[str, str]:
    return {"X-Chat-Token": token}


def _published(env: Env) -> list[dict[str, Any]]:
    return [json.loads(p) for _, p in env.redis.published]


async def _codes(env: Env, chat: TiqoraChatSession) -> list[tuple[str, Any]]:
    async with env.factory() as db:
        return [
            (m.body, json.loads(m.meta) if m.meta else None)
            for m in await load_messages_since(db, chat.id, 0)
            if m.sender == "system"
        ]


async def _lines(env: Env, chat: TiqoraChatSession) -> list[TiqoraChatMessage]:
    async with env.factory() as db:
        return list(await load_messages_since(db, chat.id, 0))


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


def _ticket_state_id(env: Env, chat: TiqoraChatSession) -> int:
    return int(
        env.sql("SELECT ticket_state_id FROM ticket WHERE id = :t", t=int(chat.ticket_id))[0][0]
    )


def _reply(body: str = "Ich übernehme") -> dict[str, Any]:
    return {
        "channel": "webchat",
        "sender_type": "agent",
        "is_visible_for_customer": True,
        "subject": "",
        "body": body,
    }


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


async def _stream_until_session(env: Env, sid: str) -> str:
    """The SSE text of the replay up to and including the first ``session`` event."""
    hub = ChatHub(env.redis, max_streams=10)
    gen: AsyncGenerator[bytes, None] = visitor_event_stream(
        _FakeRequest(),  # type: ignore[arg-type]
        hub=hub,
        session_factory=env.factory,
        redis_client=env.redis,
        session_id=sid,
        after_seq=0,
    )
    text_seen = ""
    try:
        while "event: session" not in text_seen:
            text_seen += (await asyncio.wait_for(anext(gen), timeout=5.0)).decode()
    finally:
        await gen.aclose()
        await hub.aclose()
    return text_seen


# -- visitor handoff -----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_handoff_button(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    # A turn is running right now: its claim must not block the visitor.
    env.sql(
        "UPDATE tiqora_chat_session SET bot_claim_owner = 'other:1:abc', bot_claimed_at = :n"
        " WHERE id = :i",
        n=utc_now().replace(tzinfo=None),
        i=chat.id,
    )
    env.redis.published.clear()
    async with _visitor(env) as client:
        res = await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    assert res.status_code == 204, res.text
    row = env.row(chat)
    assert (row[0], row[8], row[5]) == ("waiting", "visitor", "handoff")
    assert row[9] is not None
    assert "Der Besucher möchte mit einem Menschen sprechen." in _notes(env, chat)
    codes = await _codes(env, chat)
    assert codes[-1] == ("handoff", {"reason": "visitor"})
    rings = [p for p in _published(env) if p.get("type") == "chat_waiting"]
    assert [r["session_id"] for r in rings] == [chat.id]
    assert rings[0]["topic_ref"] == f"{CONFIG}:support"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_without_humans_ends_with_no_agent(env: Env) -> None:
    chat, token = await _start(env)
    async with _visitor(env) as client:
        res = await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    assert res.status_code == 204, res.text
    row = env.row(chat)
    assert (row[0], row[1], row[8]) == ("ended", "no_agent", "visitor")
    assert [c for c, _ in await _codes(env, chat)][-2:] == ["handoff", "no_agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_not_bot_409(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    env.sql("UPDATE tiqora_chat_session SET status = 'waiting' WHERE id = :i", i=chat.id)
    lines_before = len(await _lines(env, chat))
    async with _visitor(env) as client:
        res = await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    assert res.status_code == 409
    assert res.json() == {"detail": "not_bot"}
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", None)  # nothing written
    assert len(await _lines(env, chat)) == lines_before
    assert _notes(env, chat) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_rate_limit(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    async with _visitor(env) as client:
        codes = [
            (await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))).status_code
            for _ in range(4)
        ]
        limited = await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    assert codes == [204, 409, 409, 429]  # the 4th within the minute
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert env.row(chat)[0] == "waiting"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_needs_the_session_token(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _visitor(env) as client:
        res = await client.post(
            f"{API}/sessions/{chat.id}/handoff", headers=_h(chat.id + "." + "A" * 43)
        )
    assert res.status_code == 404
    assert env.row(chat)[0] == "bot"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_session_out_bot_fields(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    async with _visitor(env) as client:
        before = (await client.get(f"{API}/sessions/{chat.id}", headers=_h(token))).json()
        assert (
            await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
        ).status_code == 204
        after = (await client.get(f"{API}/sessions/{chat.id}", headers=_h(token))).json()
    assert (before["status"], before["bot_name"], before["can_handoff"]) == (
        "bot",
        "KI-Assistent",
        True,
    )
    assert before["can_send"] is True
    assert (after["status"], after["bot_name"], after["can_handoff"]) == ("waiting", None, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_stream_session_event_carries_bot_name_only_while_bot(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    first = await _stream_until_session(env, chat.id)
    data = json.loads(first.split("event: session\ndata: ")[1].split("\n")[0])
    assert data == {"status": "bot", "agent_name": None, "bot_name": "KI-Assistent"}
    async with _visitor(env) as client:
        await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    second = await _stream_until_session(env, chat.id)
    data = json.loads(second.split("event: session\ndata: ")[1].split("\n")[0])
    assert data == {"status": "waiting", "agent_name": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_handoff_full_post_real_session(env: Env) -> None:
    """R-TX: reads earlier in the request must not pin the handoff's snapshot; both the
    session change and the ticket writes are committed when the response arrives."""
    await _humans_online(env)
    chat, token = await _start(env)
    notes_before = len(_notes(env, chat))
    async with _visitor(env) as client:
        assert (await client.get(f"{API}/sessions/{chat.id}", headers=_h(token))).status_code == 200
        assert (
            await client.get(f"{API}/sessions/{chat.id}/messages", headers=_h(token))
        ).status_code == 200
        res = await client.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    assert res.status_code == 204, res.text
    # read through a separate connection: everything is committed
    row = env.row(chat)
    assert (row[0], row[8]) == ("waiting", "visitor")
    assert len(_notes(env, chat)) == notes_before + 1
    flagged = env.sql(
        "SELECT ai_escalated_at FROM tiqora_ai_ticket_state WHERE ticket_id = :t",
        t=int(chat.ticket_id),
    )
    assert flagged and flagged[0][0] is not None
    assert [c for c, _ in await _codes(env, chat)][-1] == "handoff"


# -- agent takeover ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_agent_takeover_from_bot(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _agent(env) as client:
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")
    assert res.status_code == 200, res.text
    assert res.json() == {"ticket_id": int(chat.ticket_id)}
    row = env.row(chat)
    assert (row[0], row[8]) == ("active", "takeover")
    assert row[9] is not None
    assigned = env.sql("SELECT assigned_user_id FROM tiqora_chat_session WHERE id = :i", i=chat.id)
    assert int(assigned[0][0]) == AGENT
    assert env.sql("SELECT user_id FROM ticket WHERE id = :t", t=int(chat.ticket_id))[0][0] == AGENT
    assert (await _codes(env, chat))[-1][0] == "agent_joined"
    # the AI is out of the conversation, and no escalation mark was written
    assert await _turn(env, chat) == "skipped"
    flagged = env.sql(
        "SELECT ai_escalated_at FROM tiqora_ai_ticket_state WHERE ticket_id = :t",
        t=int(chat.ticket_id),
    )
    assert not flagged or flagged[0][0] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_takeover_needs_rw_and_a_free_slot(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _agent(env, AGENT_RO) as client:
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")
    assert res.status_code == 403
    assert env.row(chat)[0] == "bot"
    # capacity: AGENT holds max_concurrent chats already
    async with env.factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=1, now=NOW)
        await db.commit()
    other, _t = await _start(env, 2)
    async with _agent(env) as client:
        assert (await client.post(f"/api/v1/chat/sessions/{other.id}/accept")).status_code == 200
        full = await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")
    assert full.status_code == 409
    assert full.json()["detail"] == "at_capacity"
    assert env.row(chat)[0] == "bot"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_second_takeover_is_already_taken(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _agent(env, AGENT) as first, _agent(env, AGENT2) as second:
        assert (await first.post(f"/api/v1/chat/sessions/{chat.id}/accept")).status_code == 200
        late = await second.post(f"/api/v1/chat/sessions/{chat.id}/accept")
    assert late.status_code == 409
    assert late.json()["detail"] == "already_taken"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_agent_reply_takes_over_bot_chat(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _agent(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code in (200, 201), res.text
    row = env.row(chat)
    assert (row[0], row[8]) == ("active", "takeover")
    lines = await _lines(env, chat)
    assert [m.sender for m in lines][-2:] == ["system", "agent"]
    assert lines[-2].body == "agent_joined"
    assert lines[-1].body == "Ich übernehme"  # delivered into the chat


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_agent_end_of_a_bot_chat(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    async with _agent(env) as client:
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end", json={})
    assert res.status_code == 204, res.text
    row = env.row(chat)
    assert (row[0], row[1]) == ("ended", "agent")


# -- inbox and ticket header ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_inbox_lists_bot_section_only_for_rw(env: Env) -> None:
    await _humans_online(env)
    older, _t = await _start(env, 1)
    newer, _t2 = await _start(env, 2)
    env.sql(  # both were created at the fixed test time: make "older" really older
        "UPDATE tiqora_chat_session SET created = :c WHERE id = :i",
        c=(NOW - timedelta(minutes=5)).replace(tzinfo=None),
        i=older.id,
    )
    async with _agent(env, AGENT) as rw, _agent(env, AGENT_RO) as ro:
        inbox = (await rw.get("/api/v1/chat/inbox")).json()
        ro_inbox = (await ro.get("/api/v1/chat/inbox")).json()
    assert inbox["waiting"] == [] and inbox["mine"] == []
    assert [i["session_id"] for i in inbox["bot"]] == [newer.id, older.id]  # newest first
    first = inbox["bot"][0]
    assert first["status"] == "bot"
    assert first["waiting_since"] is None and first["accepted_at"] is None
    assert first["ticket_id"] == int(newer.ticket_id)
    assert ro_inbox == {"waiting": [], "mine": [], "bot": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_chat_leaves_the_bot_section_after_handoff(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    async with _visitor(env) as visitor:
        await visitor.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
    async with _agent(env) as agent:
        inbox = (await agent.get("/api/v1/chat/inbox")).json()
    assert inbox["bot"] == []
    assert [i["session_id"] for i in inbox["waiting"]] == [chat.id]
    assert inbox["waiting"][0]["status"] == "waiting"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_ticket_chat_info_handoff_fields(env: Env) -> None:
    await _humans_online(env)
    chat, token = await _start(env)
    async with _agent(env) as agent:
        before = (await agent.get(f"/api/v1/tickets/{chat.ticket_id}/chat")).json()
        async with _visitor(env) as visitor:
            await visitor.post(f"{API}/sessions/{chat.id}/handoff", headers=_h(token))
        after = (await agent.get(f"/api/v1/tickets/{chat.ticket_id}/chat")).json()
    assert (before["status"], before["handoff_reason"], before["handoff_at"]) == ("bot", None, None)
    assert before["bot_name"] == "KI-Assistent"
    assert (after["status"], after["handoff_reason"]) == ("waiting", "visitor")
    assert after["handoff_at"] is not None
    assert after["bot_name"] == "KI-Assistent"  # the AI took part in this chat


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_ticket_chat_info_of_a_human_only_chat_has_no_bot_name(env: Env) -> None:
    await _humans_online(env)
    chat, _token = await _start(env)
    env.sql(
        "UPDATE tiqora_chat_session SET status = 'waiting', bot_outcome = NULL WHERE id = :i",
        i=chat.id,
    )
    async with _agent(env) as agent:
        info = (await agent.get(f"/api/v1/tickets/{chat.ticket_id}/chat")).json()
    assert info["bot_name"] is None
    assert info["handoff_reason"] is None


# -- sweep ---------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_bot_session_idle_abandoned_and_ticket_closed(env: Env) -> None:
    await _humans_online(env)
    answered, _t = await _start(env, 1)
    silent, _t2 = await _start(env, 2)
    env.llm = ScriptedLlm([propose_response("reply", "Neu starten.")])
    assert await _turn(env, answered) == "replied"
    assert env.row(answered)[5] == "replied"
    before_answered = _ticket_state_id(env, answered)
    before_silent = _ticket_state_id(env, silent)
    async with env.factory() as db:
        expected = await resolve_auto_state_id(
            db, kind="reply", allowed_state_types=["open", "closed"]
        )
    assert expected is not None and expected != before_answered
    stale = NOW - timedelta(seconds=901)
    for chat in (answered, silent):
        env.sql(
            "UPDATE tiqora_chat_session SET visitor_last_seen = :s WHERE id = :i",
            s=stale.replace(tzinfo=None),
            i=chat.id,
        )

    res = await run_webchat_sweep_tick(session_factory=env.factory, redis_client=env.redis, now=NOW)

    assert res["abandoned"] == 2
    for chat in (answered, silent):
        row = env.row(chat)
        assert (row[0], row[1]) == ("abandoned", "idle")
        assert (await _codes(env, chat))[-1] == ("idle_closed", {})
    assert _ticket_state_id(env, answered) == expected  # the bot's answer resolved it
    assert _ticket_state_id(env, silent) == before_silent  # nothing answered: unchanged


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_fresh_bot_session_is_not_swept(env: Env) -> None:
    chat, _token = await _start(env)
    env.sql(
        "UPDATE tiqora_chat_session SET visitor_last_seen = :s WHERE id = :i",
        s=(NOW - timedelta(seconds=300)).replace(tzinfo=None),
        i=chat.id,
    )
    res = await run_webchat_sweep_tick(session_factory=env.factory, redis_client=env.redis, now=NOW)
    assert res["abandoned"] == 0
    assert env.row(chat)[0] == "bot"


# -- what the visitor never sees -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_response_never_contains_handoff_note(env: Env) -> None:
    await _humans_online(env)
    ai_chat, ai_token = await _start(env, 1)
    env.llm = ScriptedLlm([escalate_response("Geheimer Grund 4711")])
    assert await _turn(env, ai_chat) == "handoff"
    pressed, pressed_token = await _start(env, 2)
    async with _visitor(env) as client:
        assert (
            await client.post(f"{API}/sessions/{pressed.id}/handoff", headers=_h(pressed_token))
        ).status_code == 204
        seen: dict[str, str] = {}
        for chat, token in ((ai_chat, ai_token), (pressed, pressed_token)):
            messages = await client.get(f"{API}/sessions/{chat.id}/messages", headers=_h(token))
            state = await client.get(f"{API}/sessions/{chat.id}", headers=_h(token))
            assert messages.status_code == 200 and state.status_code == 200
            seen[chat.id] = messages.text + state.text + await _stream_until_session(env, chat.id)
    # the internal notes exist for the agents ...
    assert any("Geheimer Grund 4711" in n for n in _notes(env, ai_chat))
    assert "Der Besucher möchte mit einem Menschen sprechen." in _notes(env, pressed)
    # ... and reach no visitor output (REST or stream)
    for output in seen.values():
        for secret in ("Geheimer Grund", "4711", "Der Besucher möchte", "KI-Übergabe"):
            assert secret not in output
    ai_lines = json.loads(await _visitor_messages(env, ai_chat, ai_token))
    handoff = [m for m in ai_lines if m["code"] == "handoff"]
    assert [m["params"] for m in handoff] == [{"reason": "ai"}]  # only the wording selector


async def _visitor_messages(env: Env, chat: TiqoraChatSession, token: str) -> str:
    async with _visitor(env) as client:
        return (await client.get(f"{API}/sessions/{chat.id}/messages", headers=_h(token))).text
