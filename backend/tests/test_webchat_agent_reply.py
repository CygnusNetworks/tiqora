"""Agent replies into the chat, ticket chat info and agent typing (band 48670-48699).

The reply goes through the normal article route (``POST /tickets/{id}/articles``,
``channel="webchat"``): an agent's customer-visible webchat article is also a transcript line
the visitor sees; every other article (notes, email, other channels) never reaches the chat.
Both dialects, real DB; Redis is the shared ``FakeRedis``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_auth_service, get_current_user, get_redis
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatSession
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    TicketWriteService,
    create_ticket,
)
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL
from tiqora.webchat import agent as webchat_agent
from tiqora.webchat.agent import accept_session
from tiqora.webchat.availability import (
    ensure_agent_state_row,
    mark_agent_online,
    set_agent_state,
)
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.hub import chat_channel
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import (
    VisitorContext,
    create_session,
    end_by_visitor,
    post_visitor_message,
)
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, AGENT, QUEUE, CONFIG = 48670, 48671, 48672, 48673
AGENT2, NOPERM = 48674, 48675
CONFIG_IDS = range(48670, 48700)
KEY = "wc_" + "7d" * 16
ORIGIN = "https://www.example.org"
SELF = "http://testserver"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
#: The email reply on a chat ticket logs its sent mail.
MARK_TABLES = (*ZNUNY_MARK_TABLES, "tiqora_mail_log")
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef", user_agent=None, page_url=None, language="de"
)


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
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE],
            group_ids=[GROUP],
            user_ids=[AGENT, AGENT2, NOPERM],
        )
    engine.dispose()


def _seed(conn: Any) -> None:
    seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-reply-max")
    conn.execute(
        text("UPDATE users SET first_name = 'Max', last_name = 'Muster' WHERE id = :u"),
        {"u": AGENT},
    )
    for uid, login, first in ((AGENT2, "wc48-reply-nina", "Nina"), (NOPERM, "wc48-none", "No")):
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :l, 'x', :f, 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"u": uid, "l": login, "f": first, "t": NOW},
        )
    for key in ("ro", "rw"):
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": AGENT2, "g": GROUP, "k": key, "t": NOW},
        )
    insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
    enable_webchat(conn)


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
    marks = snapshot_marks(url, MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        _seed(conn)
    engine.dispose()
    clear_config_cache()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        # AGENT has a state row; AGENT2 deliberately has none (the reply path creates it).
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    fake = FakeRedis()
    await mark_agent_online(fake, AGENT)  # chats start only with an eligible agent online
    try:
        yield Env(url, factory, fake)
    finally:
        clear_config_cache()
        await aengine.dispose()
        delete_above_marks(url, marks, first=mark_cleanup_first(marks))
        _cleanup(url)


parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


# -- helpers -------------------------------------------------------------------------------


async def _start(env: Env, n: int, *, email: str | None = None) -> tuple[TiqoraChatSession, str]:
    prechat: dict[str, str | bool] = {"name": f"Erika {n}"}
    if email:
        prechat["email"] = email
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
                visitor_id=f"visitor_wc48_{n:010d}",
                topic="support",
                prechat=prechat,
                message="Mein Router blinkt rot",
            ),
            ctx=CTX,
            now=NOW,
            settings=get_settings(),
        )
        await db.commit()
        return chat, token


async def _accept(env: Env, chat: TiqoraChatSession, user_id: int = AGENT) -> None:
    async with env.factory() as db:
        await ensure_agent_state_row(db, user_id, now=NOW)
        await db.commit()
        await accept_session(db, SysConfig(db), session_id=chat.id, user_id=user_id, now=NOW)
        await db.commit()


async def _active(env: Env, n: int) -> tuple[TiqoraChatSession, str]:
    chat, token = await _start(env, n)
    await _accept(env, chat)
    return chat, token


def _client(env: Env, user_id: int = AGENT) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    user = AuthenticatedUser(
        id=user_id,
        login=f"wc48-{user_id}",
        first_name="Chat",
        last_name="Agent",
        auth_method="session",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


def _reply(body: str = "Bitte Router neu starten", **extra: Any) -> dict[str, Any]:
    return {
        "channel": "webchat",
        "sender_type": "agent",
        "is_visible_for_customer": True,
        "subject": "",
        "body": body,
        **extra,
    }


async def _rows(env: Env, sql: str, **params: Any) -> list[Any]:
    async with env.factory() as db:
        return list((await db.execute(text(sql), params)).all())


async def _messages(env: Env, session_id: str) -> list[dict[str, Any]]:
    rows = await _rows(
        env,
        "SELECT seq, sender, body, meta, attachments, article_id, user_id"
        " FROM tiqora_chat_message WHERE session_id = :s ORDER BY seq",
        s=session_id,
    )
    return [
        {
            "seq": int(r[0]),
            "sender": str(r[1]),
            "body": str(r[2]),
            "meta": json.loads(r[3]) if r[3] else None,
            "attachments": json.loads(r[4]) if r[4] else None,
            "article_id": int(r[5]) if r[5] is not None else None,
            "user_id": int(r[6]) if r[6] is not None else None,
        }
        for r in rows
    ]


async def _article_count(env: Env, ticket_id: int) -> int:
    return int(
        (await _rows(env, "SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id))[0][0]
    )


async def _history(env: Env, ticket_id: int) -> list[str]:
    # ``TicketUnlockTimeoutUpdate`` writes a Misc "Reset of unlock time." row whenever the
    # second-resolution unlock timestamp moved between two writes: wall-clock dependent, so
    # it is not part of what these tests assert (the cause of the intermittent failures).
    rows = await _rows(
        env,
        "SELECT ht.name FROM ticket_history h"
        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
        " WHERE h.ticket_id = :t AND h.name <> 'Reset of unlock time.' ORDER BY h.id",
        t=ticket_id,
    )
    return [str(r[0]) for r in rows]


async def _chat(env: Env, session_id: str) -> TiqoraChatSession:
    async with env.factory() as db:
        row = await get_chat_session(db, session_id)
        assert row is not None
        return row


def _chat_publishes(env: Env, session_id: str) -> list[dict[str, Any]]:
    return [json.loads(p) for c, p in env.redis.published if c == chat_channel(session_id)]


# -- replies ---------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_agent_reply_reaches_visitor_message_log(env: Env) -> None:
    chat, _token = await _active(env, 1)
    env.redis.published.clear()

    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 201, res.text
    article_id = res.json()["article_id"]

    newest = (await _messages(env, chat.id))[-1]
    assert newest["sender"] == "agent"
    assert newest["body"] == "Bitte Router neu starten"
    assert newest["article_id"] == article_id
    assert newest["user_id"] == AGENT
    assert newest["meta"] == {"agent_name": "Max"}
    assert newest["attachments"] is None
    assert (await _history(env, chat.ticket_id))[-1] == "SendAnswer"
    assert {"type": "wake"} in _chat_publishes(env, chat.id)

    # The article: WebChat channel, plain text, To = the visitor's address, subject = title.
    row = (
        await _rows(
            env,
            "SELECT a.communication_channel_id, a.is_visible_for_customer, m.a_content_type,"
            " m.a_to, m.a_subject, m.a_body, m.a_from"
            " FROM article a JOIN article_data_mime m ON m.article_id = a.id WHERE a.id = :a",
            a=article_id,
        )
    )[0]
    channel = (
        await _rows(env, "SELECT name FROM communication_channel WHERE id = :c", c=int(row[0]))
    )[0][0]
    assert channel == "WebChat"
    assert int(row[1]) == 1
    assert row[2] == "text/plain; charset=utf-8"
    assert row[3] == '"Erika 1" <visitor-visitor_wc48@chat.invalid>'
    assert row[4] == "Chat: Mein Router blinkt rot"
    assert row[5] == "Bitte Router neu starten"
    assert row[6] == "Max Muster"


@pytest.mark.asyncio
@parametrize_db
async def test_reply_to_waiting_chat_accepts_it(env: Env) -> None:
    """R-REPLY-ACCEPT-FIRST: AGENT2 has no agent-state row yet; the reply creates it,
    accepts in its own transaction and then writes the reply."""
    chat, _token = await _start(env, 2)
    env.redis.published.clear()

    async with _client(env, AGENT2) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 201, res.text

    row = await _chat(env, chat.id)
    assert row.status == "active"
    assert row.assigned_user_id == AGENT2
    msgs = await _messages(env, chat.id)
    assert [(m["seq"], m["sender"], m["body"]) for m in msgs] == [
        (1, "visitor", "Mein Router blinkt rot"),
        (2, "system", "agent_joined"),
        (3, "agent", "Bitte Router neu starten"),
    ]
    assert msgs[1]["meta"] == {"name": "Nina"}
    assert msgs[2]["article_id"] == res.json()["article_id"]
    owner = (await _rows(env, "SELECT user_id FROM ticket WHERE id = :t", t=chat.ticket_id))[0][0]
    assert int(owner) == AGENT2
    events = [json.loads(p) for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert [e["type"] for e in events if e.get("session_id") == chat.id] == ["chat_assigned"]


@pytest.mark.asyncio
@parametrize_db
async def test_refused_accept_refuses_the_reply(env: Env) -> None:
    """At capacity the implicit accept is refused: 409 ``at_capacity``, nothing written."""
    async with env.factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=1, now=NOW)
        await db.commit()
    # Both chats start while AGENT is still eligible; then AGENT takes the first one.
    first, _t3 = await _start(env, 3)
    waiting, _t4 = await _start(env, 4)
    await _accept(env, first)
    articles = await _article_count(env, waiting.ticket_id)

    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{waiting.ticket_id}/articles", json=_reply())
        assert res.status_code == 409
        assert res.json() == {"detail": "at_capacity"}
        # An agent without rw cannot take (accept) the chat: 403, nothing written.
    async with _client(env, NOPERM) as client:
        res = await client.post(f"/api/v1/tickets/{waiting.ticket_id}/articles", json=_reply())
        assert res.status_code == 403

    assert await _article_count(env, waiting.ticket_id) == articles
    assert [m["sender"] for m in await _messages(env, waiting.id)] == ["visitor"]
    assert (await _chat(env, waiting.id)).status == "waiting"


@pytest.mark.asyncio
@parametrize_db
async def test_reply_to_ended_chat_is_409_and_stores_nothing(env: Env) -> None:
    chat, _token = await _active(env, 5)
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    articles = await _article_count(env, chat.ticket_id)
    messages = await _messages(env, chat.id)

    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 409
    assert res.json() == {"detail": "chat_not_live"}
    assert await _article_count(env, chat.ticket_id) == articles
    assert await _messages(env, chat.id) == messages


@pytest.mark.asyncio
@parametrize_db
async def test_internal_note_not_sent_to_visitor(env: Env) -> None:
    chat, _token = await _active(env, 6)
    before = await _messages(env, chat.id)
    async with _client(env) as client:
        note = await client.post(
            f"/api/v1/tickets/{chat.ticket_id}/articles",
            json={
                "channel": "note",
                "sender_type": "agent",
                "is_visible_for_customer": False,
                "subject": "Intern",
                "body": "Kunde hat Vertrag B",
            },
        )
        assert note.status_code == 201, note.text
        # A webchat article that is NOT visible for the customer is a plain note too.
        hidden = await client.post(
            f"/api/v1/tickets/{chat.ticket_id}/articles",
            json=_reply("Nur intern", is_visible_for_customer=False),
        )
        assert hidden.status_code == 201, hidden.text
    assert await _messages(env, chat.id) == before
    assert (await _history(env, chat.ticket_id))[-2:] == ["AddNote", "AddNote"]


@pytest.mark.asyncio
@parametrize_db
async def test_email_reply_on_chat_ticket_not_sent_to_visitor(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat, _token = await _active(env, 7)
    before = await _messages(env, chat.id)
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    sender = CapturingMailSender()
    try:
        async with env.factory() as db, db.begin():
            svc = TicketWriteService(db, env.factory, SysConfig(db), mail_sender=sender)
            await svc.add_article(
                AGENT,
                int(chat.ticket_id),
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: Chat",
                    body="Antwort per Mail",
                    channel="email",
                    to_address="erika@example.org",
                ),
            )
    finally:
        get_settings.cache_clear()
    assert len(sender.sent) == 1
    assert await _messages(env, chat.id) == before
    assert _chat_publishes(env, chat.id) == []


@pytest.mark.asyncio
@parametrize_db
async def test_html_body_converted_and_attachment_listed(env: Env) -> None:
    chat, token = await _active(env, 8)
    pdf = b"%PDF-1.4 wc48 plan"
    attachment = {
        "filename": "plan.pdf",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(pdf).decode("ascii"),
    }
    async with _client(env) as client:
        res = await client.post(
            f"/api/v1/tickets/{chat.ticket_id}/articles",
            json=_reply(
                "<p>Hallo</p>", content_type="text/html; charset=utf-8", attachments=[attachment]
            ),
        )
        assert res.status_code == 201, res.text
        newest = (await _messages(env, chat.id))[-1]
        assert newest["body"] == "Hallo"
        assert newest["attachments"] == [
            {
                "index": 0,
                "filename": "plan.pdf",
                "content_type": "application/pdf",
                "size": len(pdf),
            }
        ]
        stored = (
            await _rows(
                env,
                "SELECT a_content_type, a_body FROM article_data_mime WHERE article_id = :a",
                a=res.json()["article_id"],
            )
        )[0]
        assert tuple(stored) == ("text/plain; charset=utf-8", "Hallo")

        download = await client.get(
            f"/api/chat/v1/sessions/{chat.id}/messages/{newest['seq']}/attachments/0",
            headers={"X-Chat-Token": token},
        )
        assert download.status_code == 200, download.text
        assert download.content == pdf

        # Telegram-only extras stay refused on webchat.
        res = await client.post(
            f"/api/v1/tickets/{chat.ticket_id}/articles",
            json=_reply("Mit Knopf", telegram_buttons=[{"label": "Ja"}]),
        )
        assert res.status_code == 422


@pytest.mark.asyncio
@parametrize_db
async def test_reply_too_long_422(env: Env) -> None:
    active, _t1 = await _active(env, 9)
    waiting, _t2 = await _start(env, 10)
    async with _client(env) as client:
        for chat in (active, waiting):
            articles = await _article_count(env, chat.ticket_id)
            messages = await _messages(env, chat.id)
            for body in ("x" * 4001, "   "):
                res = await client.post(
                    f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply(body)
                )
                assert res.status_code == 422, res.text
            assert await _article_count(env, chat.ticket_id) == articles
            assert await _messages(env, chat.id) == messages
    # Refused before the implicit accept: the waiting chat was not taken either.
    assert (await _chat(env, waiting.id)).status == "waiting"


@pytest.mark.asyncio
@parametrize_db
async def test_reply_and_visitor_message_concurrently(env: Env) -> None:
    """R-LOCK-ORDER: an agent reply and visitor lines on the same chat at once."""
    chat, _token = await _active(env, 11)

    async def visitor(n: int) -> None:
        async with env.factory() as db:
            await post_visitor_message(
                db, SysConfig(db), chat=chat, body=f"Zeile {n}", client_id=None, now=NOW
            )
            await db.commit()

    async with _client(env) as client:
        results = await asyncio.gather(
            client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply("A1")),
            visitor(1),
            client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply("A2")),
            visitor(2),
        )
    assert results[0].status_code == 201 and results[2].status_code == 201
    msgs = await _messages(env, chat.id)
    assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
    assert sorted(m["body"] for m in msgs[-4:]) == ["A1", "A2", "Zeile 1", "Zeile 2"]


# -- ticket chat info ------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_ticket_chat_info(env: Env) -> None:
    chat, token = await _start(env, 12, email="erika12@example.org")
    async with _client(env) as client:
        res = await client.get(f"/api/v1/tickets/{chat.ticket_id}/chat")
        assert res.status_code == 200, res.text
        info = res.json()
        assert info["session_id"] == chat.id
        assert info["status"] == "waiting"
        assert info["end_reason"] is None
        assert info["visitor_name"] == "Erika 12"
        assert info["visitor_email"] == "erika12@example.org"
        assert info["visitor_email_verified"] is False
        assert info["topic_label"] == "Support"
        assert info["language"] == "de"
        assert info["waiting_since"].startswith("2026-10-10T12:00:00")
        assert info["accepted_at"] is None and info["assigned_user_id"] is None
        assert info["assigned_name"] is None
        assert info["visitor_online"] is False
        assert info["visitor_typing"] is False

        await env.redis.set(f"tiqora:chat:seen:{chat.id}", "1", ex=90)
        typing = await client.post(
            f"/api/chat/v1/sessions/{chat.id}/typing", headers={"X-Chat-Token": token}
        )
        assert typing.status_code == 204, typing.text
        await _accept(env, chat)

        info = (await client.get(f"/api/v1/tickets/{chat.ticket_id}/chat")).json()
        assert info["visitor_online"] is True
        assert info["visitor_typing"] is True
        assert info["status"] == "active"
        assert info["assigned_user_id"] == AGENT
        assert info["assigned_name"] == "Max Muster"

    # A ticket without a chat → 404; an agent without ro → 403; unknown ticket → 404.
    async with env.factory() as db:
        plain = await create_ticket(
            db,
            env.factory,
            SysConfig(db),
            params=TicketIn(
                title="wc48 ohne Chat", queue_id=QUEUE, state_id=1, priority_id=3, owner_id=1
            ),
            user_id=1,
        )
        await db.commit()
    async with _client(env) as client:
        res = await client.get(f"/api/v1/tickets/{plain}/chat")
        assert res.status_code == 404
        assert (await client.get("/api/v1/tickets/999999999/chat")).status_code == 404
    async with _client(env, NOPERM) as client:
        assert (await client.get(f"/api/v1/tickets/{chat.ticket_id}/chat")).status_code == 403


@pytest.mark.asyncio
@parametrize_db
async def test_agent_typing_rate_limited(env: Env) -> None:
    chat, _token = await _active(env, 13)
    env.redis.published.clear()
    async with _client(env) as client:
        first = await client.post(f"/api/v1/tickets/{chat.ticket_id}/chat/typing")
        second = await client.post(f"/api/v1/tickets/{chat.ticket_id}/chat/typing")
    assert first.status_code == 204 and second.status_code == 204
    assert _chat_publishes(env, chat.id) == [{"type": "typing", "who": "agent"}]

    env.redis.advance(3)
    async with _client(env, NOPERM) as client:
        assert (
            await client.post(f"/api/v1/tickets/{chat.ticket_id}/chat/typing")
        ).status_code == 403
    assert len(_chat_publishes(env, chat.id)) == 1

    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=NOW)
        await db.commit()
    env.redis.published.clear()
    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/chat/typing")
    assert res.status_code == 409
    assert res.json() == {"detail": "chat_not_live"}
    assert _chat_publishes(env, chat.id) == []


# -- fix round 1: races, other agent, state_id, API-key scopes --------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_reply_into_chat_active_with_another_agent(env: Env) -> None:
    """Any agent with ``note`` may answer an ACTIVE chat (R-REPLY-ERRORS); no re-accept."""
    chat, _token = await _active(env, 20)  # accepted by AGENT
    async with _client(env, AGENT2) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 201, res.text
    newest = (await _messages(env, chat.id))[-1]
    assert (newest["sender"], newest["user_id"], newest["meta"]) == (
        "agent",
        AGENT2,
        {"agent_name": "Nina"},
    )
    row = await _chat(env, chat.id)
    assert (row.status, row.assigned_user_id) == ("active", AGENT)


@pytest.mark.asyncio
@parametrize_db
async def test_reply_loses_the_accept_race(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENT takes the chat between AGENT2's pre-step read and its accept → 409
    ``already_taken``, AGENT2 writes nothing (R-REPLY-ACCEPT-FIRST)."""
    chat, _token = await _start(env, 21)
    original = webchat_agent.ensure_agent_state_row

    async def ensure_then_lose(db: AsyncSession, user_id: int, *, now: Any) -> None:
        await _accept(env, chat, AGENT)
        await original(db, user_id, now=now)

    monkeypatch.setattr(webchat_agent, "ensure_agent_state_row", ensure_then_lose)
    articles = await _article_count(env, chat.ticket_id)
    async with _client(env, AGENT2) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 409
    assert res.json() == {"detail": "already_taken"}
    assert await _article_count(env, chat.ticket_id) == articles
    assert [m["sender"] for m in await _messages(env, chat.id)] == ["visitor", "system"]
    assert (await _chat(env, chat.id)).assigned_user_id == AGENT


@pytest.mark.asyncio
@parametrize_db
async def test_chat_ended_before_the_implicit_accept(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ended between the pre-step read and the accept → the same ``chat_not_live``."""
    chat, _token = await _start(env, 22)
    original = webchat_agent.ensure_agent_state_row

    async def ensure_after_end(db: AsyncSession, user_id: int, *, now: Any) -> None:
        async with env.factory() as other:
            await end_by_visitor(other, chat=chat, now=NOW)
            await other.commit()
        await original(db, user_id, now=now)

    monkeypatch.setattr(webchat_agent, "ensure_agent_state_row", ensure_after_end)
    articles = await _article_count(env, chat.ticket_id)
    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 409
    assert res.json() == {"detail": "chat_not_live"}
    assert await _article_count(env, chat.ticket_id) == articles


@pytest.mark.asyncio
@parametrize_db
async def test_invalid_state_refused_before_the_implicit_accept(env: Env) -> None:
    chat, _token = await _start(env, 23)
    async with _client(env) as client:
        res = await client.post(
            f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply(state_id=999999)
        )
    assert res.status_code == 422, res.text
    assert (await _chat(env, chat.id)).status == "waiting"
    assert [m["sender"] for m in await _messages(env, chat.id)] == ["visitor"]


@pytest.mark.asyncio
@parametrize_db
async def test_refused_caller_gets_no_agent_state_row(env: Env) -> None:
    chat, _token = await _start(env, 24)
    async with _client(env, NOPERM) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/articles", json=_reply())
    assert res.status_code == 403
    rows = await _rows(env, "SELECT 1 FROM tiqora_chat_agent_state WHERE user_id = :u", u=NOPERM)
    assert rows == []


@pytest.mark.asyncio
@parametrize_db
async def test_no_typing_signal_for_a_waiting_chat(env: Env) -> None:
    chat, _token = await _start(env, 25)
    env.redis.published.clear()
    async with _client(env) as client:
        res = await client.post(f"/api/v1/tickets/{chat.ticket_id}/chat/typing")
    assert res.status_code == 204
    assert _chat_publishes(env, chat.id) == []


class _ScopedKeyAuth:
    """``AuthService`` stand-in: every ``tiqora_*`` bearer resolves to AGENT with *scopes*."""

    def __init__(self, scopes: frozenset[str]) -> None:
        self._user = AuthenticatedUser(
            id=AGENT,
            login="wc48-reply-max",
            first_name="Max",
            last_name="Muster",
            auth_method="api_key",
            api_key_scopes=scopes,
        )

    async def resolve_session(self, token: str) -> AuthenticatedUser | None:
        return None

    async def resolve_api_key(self, raw: str) -> AuthenticatedUser | None:
        return self._user if raw.startswith("tiqora_") else None


def _key_client(env: Env, scopes: str) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    auth = _ScopedKeyAuth(frozenset(scopes.split(",")))
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url=SELF,
        headers={"Authorization": "Bearer tiqora_wc48testkey"},
    )


@pytest.mark.asyncio
@parametrize_db
async def test_agent_chat_api_with_scoped_api_keys(env: Env) -> None:
    """R-CHAT-SCOPE through the real auth dependency: ``/api/v1/chat`` is ``tickets``."""
    chat, _token = await _start(env, 26)
    accept = f"/api/v1/chat/sessions/{chat.id}/accept"
    async with _key_client(env, "tickets:ro") as client:
        assert (await client.get("/api/v1/chat/inbox")).status_code == 200
        res = await client.post(accept)
        assert res.status_code == 403
        assert res.json() == {"detail": "API key lacks tickets:rw scope"}
    async with _key_client(env, "kb:rw") as client:
        assert (await client.get("/api/v1/chat/inbox")).status_code == 403
        assert (await client.post(accept)).status_code == 403
    assert (await _chat(env, chat.id)).status == "waiting"
    async with _key_client(env, "tickets:rw") as client:
        res = await client.post(accept)
        assert res.status_code == 200, res.text
    assert (await _chat(env, chat.id)).assigned_user_id == AGENT


# -- MCP ticket_reply into a chat (B-M1; docs/api/mcp.md, deployment.md "MCP and chats") -------


def _mcp_state(env: Env) -> Any:
    from unittest.mock import MagicMock

    from tiqora.mcp_server.server import McpState

    settings = MagicMock(spec=Settings)
    settings.database_url = _to_async_url(env.url)
    settings.meili_url = "http://localhost:9999"
    settings.meili_master_key = "key"
    settings.meili_tickets_index = "tickets"
    return McpState(settings)


async def _mcp_reply(ticket_id: int, body: str) -> Any:
    from fastmcp import Client

    from tiqora.mcp_server.server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(
            "ticket_reply", {"ticket_id": ticket_id, "body": body, "channel": "webchat"}
        )
    data = result.data
    return json.loads(data) if isinstance(data, str) else data


@pytest.mark.asyncio
@pytest.mark.parametrize("env", ["mariadb_znuny_url"], indirect=True)
async def test_mcp_ticket_reply_webchat_waiting_active_ended(env: Env) -> None:
    """MCP ``ticket_reply`` with ``channel="webchat"``: a waiting chat answers
    ``chat_not_accepted`` (no MCP accept, R-MCP-CHAT-WAITING), an ended one ``chat_not_live``
    (both store nothing), an active one stores the article AND the agent transcript line —
    without a Redis wake (the visitor gets it with the next stream heartbeat)."""
    from unittest.mock import patch

    import tiqora.mcp_server.server as srv

    waiting, _t1 = await _start(env, 27)
    active, _t2 = await _active(env, 28)
    ended, _t3 = await _active(env, 29)
    async with env.factory() as db:
        await end_by_visitor(db, chat=ended, now=NOW)
        await db.commit()
    env.redis.published.clear()
    before = {
        c.id: (await _article_count(env, c.ticket_id), await _messages(env, c.id))
        for c in (waiting, ended)
    }

    state = _mcp_state(env)
    srv._mcp_state = state
    try:
        with patch("tiqora.mcp_server.server._get_user_id", return_value=AGENT):
            assert await _mcp_reply(waiting.ticket_id, "Hallo?") == {"error": "chat_not_accepted"}
            assert await _mcp_reply(ended.ticket_id, "Hallo?") == {"error": "chat_not_live"}
            ok = await _mcp_reply(active.ticket_id, "Per MCP: bitte neu starten")
    finally:
        await state.aclose()
        srv._mcp_state = None

    for c in (waiting, ended):
        assert (await _article_count(env, c.ticket_id), await _messages(env, c.id)) == before[c.id]
    assert (await _chat(env, waiting.id)).status == "waiting"

    assert set(ok) == {"article_id", "ticket_id"}, ok
    newest = (await _messages(env, active.id))[-1]
    assert newest["sender"] == "agent"
    assert newest["body"] == "Per MCP: bitte neu starten"
    assert newest["article_id"] == ok["article_id"]
    assert newest["user_id"] == AGENT
    channel = (
        await _rows(
            env,
            "SELECT c.name FROM article a JOIN communication_channel c"
            " ON c.id = a.communication_channel_id WHERE a.id = :a",
            a=ok["article_id"],
        )
    )[0][0]
    assert channel == "WebChat"
    assert env.redis.published == []  # no wake: MCP has no Redis publisher
