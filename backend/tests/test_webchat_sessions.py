"""Chat session service: tickets, articles, gapless message order (band 48490-48549)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
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
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.webchat import sessions as sessions_mod
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.hub import chat_channel
from tiqora.webchat.models import MIB, ChatConfigValues, PrechatField, PrechatOption, UploadPolicy
from tiqora.webchat.notify import (
    AGENT_EVENTS_KEY,
    WAKEUPS_KEY,
    flush_chat_notifications,
    queue_agent_event,
    queue_wakeup,
)
from tiqora.webchat.prechat import resolve_chat_customer
from tiqora.webchat.schemas import ChatOfflineIn, ChatSessionCreateIn
from tiqora.webchat.sessions import (
    PRECHAT_BLOCK_HEADER,
    ChatError,
    VisitorContext,
    create_session,
    end_by_visitor,
    post_visitor_attachment,
    post_visitor_message,
    set_contact,
    submit_offline,
)
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, AGENT, QUEUE_MOVED = 48490, 48491, 48492, 48493
CONFIG, CONFIG_ANON = 48495, 48497
CONFIG_IDS = range(48490, 48550)
DF_ID = 48496
# A band-specific name so a "Contract" field of the test database cannot collide.
DF_NAME = "Wc48Contract"
KEY, KEY_ANON = "wc_" + "e" * 32, "wc_" + "f" * 32
ORIGIN = "https://www.example.org"
VISITOR = "v" * 22
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef",
    user_agent="Mozilla/5.0 (Test)",
    page_url="https://www.example.org/kontakt?x=1",
    language="de",
)
FIELDS = [
    PrechatField(key="name", kind="text", label="Name", maps_to="name"),
    PrechatField(key="email", kind="email", label="E-Mail", required=True, maps_to="email"),
    PrechatField(
        key="contract",
        kind="select",
        label="Vertrag",
        options=[PrechatOption(value="a", label="A"), PrechatOption(value="b", label="B")],
        maps_to="dynamic_field",
        dynamic_field=DF_NAME,
    ),
    PrechatField(key="newsletter", kind="checkbox", label="Newsletter"),
]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


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
            queue_ids=[QUEUE, QUEUE_MOVED],
            group_ids=[GROUP],
            user_ids=[AGENT],
        )
        conn.execute(text("DELETE FROM dynamic_field WHERE id = :i"), {"i": DF_ID})
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
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-sess")
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'wc48-queue-moved', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_MOVED, "g": GROUP, "t": NOW},
        )
        insert_config(
            conn,
            config_id=CONFIG,
            queue_id=QUEUE,
            public_key=KEY,
            origins=[ORIGIN],
            values_override={"prechat_fields": FIELDS, "default_customer_user": None},
        )
        insert_config(
            conn, config_id=CONFIG_ANON, queue_id=QUEUE, public_key=KEY_ANON, origins=[ORIGIN]
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 0, :n, 'Vertrag', 1, 'Dropdown',"
                " 'Ticket', '---\nPossibleValues:\n  a: A\n', 1, :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "n": DF_NAME, "t": NOW},
        )
        enable_webchat(conn)
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    yield Env(url, factory, FakeRedis())
    await aengine.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


# -- helpers -------------------------------------------------------------------------------


async def _agent_online(env: Env, *, available: bool = True) -> None:
    async with env.factory() as db:
        await set_agent_state(db, AGENT, available=available, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(env.redis, AGENT)


async def _config(env: Env, config_id: int = CONFIG) -> tuple[TiqoraChatConfig, ChatConfigValues]:
    async with env.factory() as db:
        cfg = await load_config(db, config_id)
        assert cfg is not None
        return cfg, config_values(cfg)


def _payload(**overrides: Any) -> ChatSessionCreateIn:
    data: dict[str, Any] = {
        "key": KEY,
        "origin": ORIGIN,
        "visitor_id": VISITOR,
        "prechat": {"name": "Erika", "email": "erika@example.org", "contract": "a"},
        "message": "Mein Router blinkt rot",
        "client_id": "c-1",
    }
    data.update(overrides)
    return ChatSessionCreateIn(**data)


async def _start(env: Env, config_id: int = CONFIG, **overrides: Any) -> TiqoraChatSession:
    cfg, values = await _config(env, config_id)
    async with env.factory() as db:
        chat, _token, _first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=values,
            payload=_payload(**overrides),
            ctx=CTX,
            now=NOW,
        )
        await db.commit()
        return chat


async def _scalar(env: Env, sql: str, **params: Any) -> Any:
    async with env.factory() as db:
        return (await db.execute(text(sql), params)).scalar()


async def _ticket_count(env: Env) -> int:
    return int(await _scalar(env, "SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE) or 0)


async def _article_ids(env: Env, ticket_id: int) -> list[int]:
    async with env.factory() as db:
        rows = await db.execute(
            text("SELECT id FROM article WHERE ticket_id = :t ORDER BY id"), {"t": ticket_id}
        )
        return [int(r[0]) for r in rows]


async def _history_types(env: Env, ticket_id: int) -> list[str]:
    async with env.factory() as db:
        rows = await db.execute(
            text(
                "SELECT ht.name FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :t ORDER BY h.id"
            ),
            {"t": ticket_id},
        )
        return [str(r[0]) for r in rows]


async def _outbox_count(env: Env, ticket_id: int, event_type: str) -> int:
    return int(
        await _scalar(
            env,
            "SELECT COUNT(*) FROM tiqora_event_outbox WHERE ticket_id = :t AND event_type = :e",
            t=ticket_id,
            e=event_type,
        )
        or 0
    )


async def _customer_recipients(env: Env, ticket_id: int) -> list[str]:
    from tiqora.worker.notifications import _NotificationRow, _resolve_recipients, _ticket_row

    async with env.factory() as db:
        ticket = await _ticket_row(db, ticket_id)
        assert ticket is not None
        found = await _resolve_recipients(
            db, _NotificationRow(id=0, name="wc48", items={"Recipients": ["Customer"]}), ticket
        )
        return [r.email for r in found if r.kind == "Customer"]


async def _chat_row(env: Env, session_id: str) -> TiqoraChatSession:
    async with env.factory() as db:
        row = await get_chat_session(db, session_id)
        assert row is not None
        return row


# -- create --------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_create_session_creates_ticket_article_and_first_message(env: Env) -> None:
    await _agent_online(env)
    cfg, values = await _config(env)
    async with env.factory() as db:
        chat, token, first = await create_session(
            db,
            env.factory,
            SysConfig(db),
            env.redis,
            config=cfg,
            values=values,
            payload=_payload(),
            ctx=CTX,
            now=NOW,
        )
        events = list(db.info[AGENT_EVENTS_KEY])
        assert events[0]["type"] == "chat_waiting"
        assert events[0]["queue_id"] == QUEUE and events[0]["topic_ref"] == f"{CONFIG}:support"
        assert db.info[WAKEUPS_KEY] == [chat.id]
        await db.commit()
    assert chat.status == "waiting" and chat.queue_id == QUEUE
    assert chat.last_seq == 1 and first.seq == 1 and first.client_id == "c-1"
    assert token.startswith(chat.id + ".") and chat.token_hash != ""
    assert token.split(".", 1)[1] not in chat.token_hash

    async with env.factory() as db:
        t = (
            (
                await db.execute(
                    text(
                        "SELECT title, customer_user_id, customer_id, queue_id, user_id"
                        " FROM ticket WHERE id = :t"
                    ),
                    {"t": chat.ticket_id},
                )
            )
            .mappings()
            .one()
        )
        assert t["title"] == "Chat: Mein Router blinkt rot"
        assert t["customer_user_id"] == "erika@example.org"
        assert t["customer_id"] == "erika@example.org"
        assert int(t["queue_id"]) == QUEUE and int(t["user_id"]) == 1
        arts = (
            (
                await db.execute(
                    text(
                        "SELECT a.id, cc.name AS channel, st.name AS sender,"
                        " a.is_visible_for_customer AS visible, m.a_body, m.a_from"
                        " FROM article a"
                        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                        " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                        " JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.ticket_id = :t"
                    ),
                    {"t": chat.ticket_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(arts) == 1
        a = arts[0]
        assert a["channel"] == "WebChat" and a["sender"] == "customer" and int(a["visible"]) == 1
        assert a["a_body"].startswith("Mein Router blinkt rot")
        assert PRECHAT_BLOCK_HEADER not in a["a_body"]
        assert a["a_from"] == '"Erika" <erika@example.org>'
        assert first.article_id == int(a["id"])
        df = (
            await db.execute(
                text(
                    "SELECT value_text FROM dynamic_field_value"
                    " WHERE field_id = :f AND object_id = :t"
                ),
                {"f": DF_ID, "t": chat.ticket_id},
            )
        ).all()
        assert [r[0] for r in df] == ["a"]
        row = await get_chat_session(db, chat.id)
        assert row is not None
        assert row.visitor_name == "Erika" and row.visitor_email == "erika@example.org"
        assert row.customer_user_login == "erika@example.org"
        assert row.page_url == CTX.page_url and row.language == "de"
        assert row.ip_hash == CTX.ip_hash and row.waiting_since == NOW
        assert row.visitor_last_seen == NOW and row.ended_at is None
    history = await _history_types(env, chat.ticket_id)
    assert "NewTicket" in history and "WebRequestCustomer" in history
    assert await _outbox_count(env, chat.ticket_id, "NotificationNewTicket") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_extra_lines_and_removed_df_option_go_to_the_article_block(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(
        env,
        prechat={
            "name": "Erika",
            "email": "erika@example.org",
            "contract": "b",  # in the form, but no longer a PossibleValue of the field
            "newsletter": True,
        },
        message="Erste Zeile\nzweite Zeile",
    )
    body = await _scalar(
        env,
        "SELECT m.a_body FROM article a JOIN article_data_mime m ON m.article_id = a.id"
        " WHERE a.ticket_id = :t",
        t=chat.ticket_id,
    )
    assert body == (
        "Erste Zeile\nzweite Zeile\n\n— Angaben aus dem Vorab-Formular —\n"
        "Newsletter: Ja\nVertrag: B"
    )
    df_rows = await _scalar(
        env,
        "SELECT COUNT(*) FROM dynamic_field_value WHERE field_id = :f AND object_id = :t",
        f=DF_ID,
        t=chat.ticket_id,
    )
    assert int(df_rows) == 0
    title = await _scalar(env, "SELECT title FROM ticket WHERE id = :t", t=chat.ticket_id)
    assert title == "Chat: Erste Zeile"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_create_session_offline_refused(env: Env) -> None:
    await _agent_online(env, available=False)
    before = await _ticket_count(env)
    cfg, values = await _config(env)
    async with env.factory() as db:
        with pytest.raises(ChatError) as e:
            await create_session(
                db,
                env.factory,
                SysConfig(db),
                env.redis,
                config=cfg,
                values=values,
                payload=_payload(),
                ctx=CTX,
                now=NOW,
            )
        assert e.value.code == "offline" and e.value.http_status == 409
        assert AGENT_EVENTS_KEY not in db.info
        await db.rollback()
    assert await _ticket_count(env) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_prechat_invalid_creates_nothing(env: Env) -> None:
    await _agent_online(env)
    before = await _ticket_count(env)
    cfg, values = await _config(env)
    cases: list[tuple[dict[str, Any], str, int, dict[str, str] | None]] = [
        ({"prechat": {"email": "nope"}}, "invalid_prechat", 422, {"email": "email"}),
        ({"origin": "https://evil.example"}, "origin", 403, None),
        ({"topic": "billing"}, "invalid_topic", 422, None),
        ({"message": " ‮\n "}, "message_rejected", 422, {"message": "empty"}),
    ]
    for overrides, code, status, errors in cases:
        async with env.factory() as db:
            with pytest.raises(ChatError) as e:
                await create_session(
                    db,
                    env.factory,
                    SysConfig(db),
                    env.redis,
                    config=cfg,
                    values=values,
                    payload=_payload(**overrides),
                    ctx=CTX,
                    now=NOW,
                )
            assert (e.value.code, e.value.http_status, e.value.errors) == (code, status, errors)
            await db.rollback()
    assert await _ticket_count(env) == before


# -- visitor lines -------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_follow_up_message_no_notify_and_seq(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    async with env.factory() as db:
        m = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Noch da?\r\n", client_id=None, now=NOW
        )
        assert [e["type"] for e in db.info[AGENT_EVENTS_KEY]] == ["chat_message"]
        assert db.info[AGENT_EVENTS_KEY][0]["queue_id"] == QUEUE
        await db.commit()
    assert m.seq == 2 and m.body == "Noch da?" and m.sender == "visitor"
    articles = await _article_ids(env, chat.ticket_id)
    assert len(articles) == 2 and m.article_id == articles[1]
    history = await _history_types(env, chat.ticket_id)
    assert history.count("WebRequestCustomer") == 1 and history.count("FollowUp") == 1
    # NoAgentNotify: no mail per chat line (the first article did notify)
    assert await _outbox_count(env, chat.ticket_id, "NotificationFollowUp") == 0
    assert await _outbox_count(env, chat.ticket_id, "NotificationNewTicket") == 1
    a_from = await _scalar(
        env, "SELECT a_from FROM article_data_mime WHERE article_id = :a", a=articles[1]
    )
    assert a_from == '"Erika" <erika@example.org>'
    assert (await _chat_row(env, chat.id)).last_seq == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_agent_event_uses_the_tickets_current_queue(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"),
            {"q": QUEUE_MOVED, "t": chat.ticket_id},
        )
        await db.commit()
    async with env.factory() as db:
        await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Hallo", client_id=None, now=NOW
        )
        assert db.info[AGENT_EVENTS_KEY][0]["queue_id"] == QUEUE_MOVED
        await db.commit()
    assert (await _chat_row(env, chat.id)).queue_id == QUEUE_MOVED


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_client_id_retry_returns_same_message(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    before = len(await _article_ids(env, chat.ticket_id))
    async with env.factory() as db:
        m1 = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Hallo", client_id="c-2", now=NOW
        )
        await db.commit()
    async with env.factory() as db:
        m2 = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Hallo", client_id="c-2", now=NOW
        )
        assert AGENT_EVENTS_KEY not in db.info  # nothing new, nothing to announce
        await db.commit()
    assert m1.id == m2.id and m2.seq == 2
    assert len(await _article_ids(env, chat.ticket_id)) == before + 1
    assert (await _chat_row(env, chat.id)).last_seq == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_client_id_race_rolls_back_article_and_seq(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate that slips past the first lookup hits the unique index: the SAVEPOINT
    takes the second article and the seq increment back, the stored row is returned."""
    await _agent_online(env)
    chat = await _start(env)
    async with env.factory() as db:
        m1 = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Hallo", client_id="c-3", now=NOW
        )
        await db.commit()
    articles_before = len(await _article_ids(env, chat.ticket_id))
    real = sessions_mod.find_by_client_id
    calls: list[bool] = []

    async def racing_lookup(
        db: AsyncSession, session_id: str, client_id: str, *, locking: bool = False
    ) -> Any:
        calls.append(locking)
        if not locking:
            return None  # the other request had not committed yet
        return await real(db, session_id, client_id, locking=True)

    monkeypatch.setattr(sessions_mod, "find_by_client_id", racing_lookup)
    async with env.factory() as db:
        m2 = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Hallo", client_id="c-3", now=NOW
        )
        assert AGENT_EVENTS_KEY not in db.info
        await db.commit()
    assert calls == [False, True]
    assert m2.id == m1.id
    assert len(await _article_ids(env, chat.ticket_id)) == articles_before
    assert (await _chat_row(env, chat.id)).last_seq == 2
    monkeypatch.setattr(sessions_mod, "find_by_client_id", real)
    async with env.factory() as db:
        m3 = await post_visitor_message(
            db, SysConfig(db), chat=chat, body="Weiter", client_id="c-4", now=NOW
        )
        await db.commit()
    assert m3.seq == 3  # gapless after the rolled-back attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_concurrent_messages_get_gapless_seq(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)

    async def one(i: int) -> int:
        async with env.factory() as db:
            m = await post_visitor_message(
                db, SysConfig(db), chat=chat, body=f"Nachricht {i}", client_id=f"k-{i}", now=NOW
            )
            await db.commit()
            return m.seq

    seqs = await asyncio.gather(*(one(i) for i in range(10)))
    assert sorted(seqs) == list(range(2, 12))
    assert (await _chat_row(env, chat.id)).last_seq == 11
    assert len(await _article_ids(env, chat.ticket_id)) == 11
    stored = await _scalar(
        env, "SELECT COUNT(DISTINCT seq) FROM tiqora_chat_message WHERE session_id = :s", s=chat.id
    )
    assert int(stored) == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_closed_session_refuses_messages(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    later = NOW + timedelta(minutes=5)
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=later)
        assert [e["type"] for e in db.info[AGENT_EVENTS_KEY]] == ["chat_ended"]
        await db.commit()
    row = await _chat_row(env, chat.id)
    assert row.status == "ended" and row.end_reason == "visitor" and row.ended_at == later
    assert row.visitor_last_seen == later and row.last_seq == 2
    async with env.factory() as db:
        system = (
            (
                await db.execute(
                    text(
                        "SELECT sender, body, meta, article_id FROM tiqora_chat_message"
                        " WHERE session_id = :s AND seq = 2"
                    ),
                    {"s": chat.id},
                )
            )
            .mappings()
            .one()
        )
    assert system["sender"] == "system" and system["body"] == "visitor_left"
    assert json.loads(system["meta"]) == {} and system["article_id"] is None
    async with env.factory() as db:
        with pytest.raises(ChatError) as e:
            await post_visitor_message(
                db, SysConfig(db), chat=chat, body="Hallo?", client_id=None, now=later
            )
        assert (e.value.code, e.value.http_status) == ("session_closed", 409)
        await db.rollback()
    # ending twice changes nothing
    async with env.factory() as db:
        await end_by_visitor(db, chat=chat, now=later + timedelta(minutes=1))
        assert AGENT_EVENTS_KEY not in db.info
        await db.commit()
    assert (await _chat_row(env, chat.id)).last_seq == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_visitor_last_seen_is_throttled(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    for offset, expected in ((30, NOW), (61, NOW + timedelta(seconds=61))):
        async with env.factory() as db:
            await post_visitor_message(
                db,
                SysConfig(db),
                chat=chat,
                body=f"t{offset}",
                client_id=None,
                now=NOW + timedelta(seconds=offset),
            )
            await db.commit()
        assert (await _chat_row(env, chat.id)).visitor_last_seen == expected


# -- uploads -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_attachment_message(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    _cfg, values = await _config(env)
    async with env.factory() as db:
        m = await post_visitor_attachment(
            db,
            SysConfig(db),
            chat=chat,
            values=values,
            filename="../bild.png",
            content=PNG,
            caption=None,
            client_id=None,
            now=NOW,
        )
        assert [e["type"] for e in db.info[AGENT_EVENTS_KEY]] == ["chat_message"]
        await db.commit()
    assert json.loads(m.attachments or "[]") == [
        {"index": 0, "filename": "bild.png", "content_type": "image/png", "size": 72}
    ]
    assert m.body == "bild.png" and m.seq == 2
    async with env.factory() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT filename, content_type, content_size, content"
                        " FROM article_data_mime_attachment WHERE article_id = :a"
                    ),
                    {"a": m.article_id},
                )
            )
            .mappings()
            .all()
        )
        body = (
            await db.execute(
                text("SELECT a_body FROM article_data_mime WHERE article_id = :a"),
                {"a": m.article_id},
            )
        ).scalar()
    assert len(rows) == 1 and rows[0]["filename"] == "bild.png"
    assert rows[0]["content_type"] == "image/png" and str(rows[0]["content_size"]) == "72"
    stored = rows[0]["content"]
    raw = base64.b64decode(stored) if isinstance(stored, str) else bytes(stored)
    assert raw == PNG and body == "bild.png"

    # caption becomes the body
    async with env.factory() as db:
        m2 = await post_visitor_attachment(
            db,
            SysConfig(db),
            chat=chat,
            values=values,
            filename="foto.png",
            content=PNG,
            caption="Das Lämpchen",
            client_id="u-1",
            now=NOW,
        )
        await db.commit()
    assert m2.body == "Das Lämpchen" and m2.seq == 3 and m2.client_id == "u-1"

    articles = len(await _article_ids(env, chat.ticket_id))
    svg = b"<svg onload='alert(1)'/>"
    small = values.model_copy(update={"upload_policy": UploadPolicy(max_bytes=MIB)})
    for content, policy_values, status, errors in (
        (svg, values, 422, {"file": "type"}),
        (PNG + b"0" * MIB, small, 413, {"file": "size"}),
        (b"", values, 422, {"file": "empty"}),
    ):
        async with env.factory() as db:
            with pytest.raises(ChatError) as e:
                await post_visitor_attachment(
                    db,
                    SysConfig(db),
                    chat=chat,
                    values=policy_values,
                    filename="x.png",
                    content=content,
                    caption=None,
                    client_id=None,
                    now=NOW,
                )
            assert (e.value.code, e.value.http_status, e.value.errors) == (
                "upload_rejected",
                status,
                errors,
            )
            await db.rollback()
    assert len(await _article_ids(env, chat.ticket_id)) == articles


# -- offline form --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_offline_form_creates_ended_session_and_ticket(env: Env) -> None:
    before = await _ticket_count(env)  # nobody online: the form works anyway
    cfg, values = await _config(env)
    payload = ChatOfflineIn(
        key=KEY,
        origin=ORIGIN,
        visitor_id=VISITOR,
        name="Max",
        email="max@example.org",
        message="Bitte um Rückruf",
    )
    async with env.factory() as db:
        await submit_offline(
            db, env.factory, SysConfig(db), config=cfg, values=values, payload=payload,
            ctx=CTX, now=NOW,
        )  # fmt: skip
        assert AGENT_EVENTS_KEY not in db.info and WAKEUPS_KEY not in db.info
        await db.commit()
    assert await _ticket_count(env) == before + 1
    async with env.factory() as db:
        ticket_id = int(
            (
                await db.execute(
                    text("SELECT MAX(id) FROM ticket WHERE queue_id = :q"), {"q": QUEUE}
                )
            ).scalar_one()
        )
        t = (
            (
                await db.execute(
                    text("SELECT title, customer_user_id FROM ticket WHERE id = :t"),
                    {"t": ticket_id},
                )
            )
            .mappings()
            .one()
        )
        art = (
            (
                await db.execute(
                    text(
                        "SELECT cc.name AS channel, m.a_from, m.a_body FROM article a"
                        " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                        " JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.ticket_id = :t"
                    ),
                    {"t": ticket_id},
                )
            )
            .mappings()
            .one()
        )
        row = (
            await db.execute(
                text("SELECT id FROM tiqora_chat_session WHERE ticket_id = :t"), {"t": ticket_id}
            )
        ).scalar_one()
        chat = await get_chat_session(db, str(row))
        assert chat is not None
        messages = (
            await db.execute(
                text("SELECT COUNT(*) FROM tiqora_chat_message WHERE session_id = :s"),
                {"s": chat.id},
            )
        ).scalar_one()
    assert t["title"] == "Chat: Bitte um Rückruf" and t["customer_user_id"] == "max@example.org"
    assert art["channel"] == "WebChat" and art["a_from"] == '"Max" <max@example.org>'
    assert art["a_body"] == "Bitte um Rückruf"
    assert chat.status == "ended" and chat.end_reason == "offline_form"
    assert chat.ended_at == NOW and chat.waiting_since is None and chat.token_hash
    assert chat.visitor_email == "max@example.org" and chat.visitor_name == "Max"
    assert int(messages) == 0
    assert "WebRequestCustomer" in await _history_types(env, ticket_id)

    # the address is required (R-OFFLINE-EMAIL), the form can be switched off
    hidden = values.model_copy(update={"offline_mode": "hide"})
    for vals, data, code, errors in (
        (values, {"email": ""}, "invalid_prechat", {"email": "required"}),
        (values, {"email": "x@y"}, "invalid_prechat", {"email": "email"}),
        (hidden, {}, "disabled", None),
        # the address only in the mapped prechat field still counts (no overwrite with "")
        (
            values,
            {"email": "", "prechat": {"email": "nope"}},
            "invalid_prechat",
            {"email": "email"},
        ),
    ):
        async with env.factory() as db:
            with pytest.raises(ChatError) as e:
                await submit_offline(
                    db,
                    env.factory,
                    SysConfig(db),
                    config=cfg,
                    values=vals,
                    payload=payload.model_copy(update=data),
                    ctx=CTX,
                    now=NOW,
                )
            assert (e.value.code, e.value.errors) == (code, errors)
            await db.rollback()
    assert await _ticket_count(env) == before + 1


# -- contact -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_set_contact_after_no_agent(env: Env) -> None:
    await _agent_online(env)
    anon = await _start(env, CONFIG_ANON, key=KEY_ANON, prechat={}, client_id=None)
    live = await _start(env, CONFIG_ANON, key=KEY_ANON, prechat={}, client_id=None)
    late = await _start(env, CONFIG_ANON, key=KEY_ANON, prechat={}, client_id=None)
    assert anon.visitor_email is None
    t0 = await _scalar(env, "SELECT customer_user_id FROM ticket WHERE id = :t", t=anon.ticket_id)
    assert t0 is None
    # anonymous: the only customer address is the visitor's @chat.invalid From -> dropped
    assert await _customer_recipients(env, anon.ticket_id) == []
    a_from = await _scalar(
        env,
        "SELECT m.a_from FROM article a JOIN article_data_mime m ON m.article_id = a.id"
        " WHERE a.ticket_id = :t",
        t=anon.ticket_id,
    )
    assert a_from == f'"Besucher" <visitor-{VISITOR[:12]}@chat.invalid>'
    async with env.factory() as db:
        await db.execute(
            text(
                "UPDATE tiqora_chat_session SET status = 'ended', end_reason = 'no_agent',"
                " ended_at = :t WHERE id IN (:a, :b)"
            ),
            {"t": NOW, "a": anon.id, "b": late.id},
        )
        await db.commit()

    async with env.factory() as db:
        await set_contact(
            db,
            SysConfig(db),
            chat=anon,
            name="Erika <x>",
            email="Erika@Example.org",
            now=NOW + timedelta(hours=1),
        )
        assert db.info[WAKEUPS_KEY] == [anon.id]
        await db.commit()
    row = await _chat_row(env, anon.id)
    assert row.visitor_email == "Erika@example.org" and row.visitor_name == "Erika <x>"
    assert row.customer_user_login == "Erika@example.org"
    t = await _scalar(env, "SELECT customer_user_id FROM ticket WHERE id = :t", t=anon.ticket_id)
    assert t == "Erika@example.org"
    assert (await _history_types(env, anon.ticket_id)).count("CustomerUpdate") == 1
    # Znuny parity: the typed address (no customer_user row) receives customer notifications
    assert await _customer_recipients(env, anon.ticket_id) == ["Erika@example.org"]

    refusals: list[tuple[TiqoraChatSession, str, Any]] = [
        (anon, "erika@example.org", NOW + timedelta(hours=1)),  # address already given
        (live, "erika@example.org", NOW),  # chat still waiting
        (late, "erika@example.org", NOW + timedelta(seconds=7200)),  # window over
    ]
    for chat, email, when in refusals:
        async with env.factory() as db:
            with pytest.raises(ChatError) as e:
                await set_contact(db, SysConfig(db), chat=chat, name=None, email=email, now=when)
            assert (e.value.code, e.value.http_status) == ("contact_not_allowed", 409)
            await db.rollback()
    async with env.factory() as db:
        with pytest.raises(ChatError) as e:
            await set_contact(db, SysConfig(db), chat=late, name=None, email="nope", now=NOW)
        assert (e.value.code, e.value.errors) == ("invalid_prechat", {"email": "email"})
        await db.rollback()
    async with env.factory() as db:
        # one second inside the window is still fine
        await set_contact(
            db,
            SysConfig(db),
            chat=late,
            name=None,
            email="max@example.org",
            now=NOW + timedelta(seconds=7199),
        )
        await db.commit()
    assert (await _chat_row(env, late.id)).visitor_email == "max@example.org"


# -- post-commit notifications -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_flush_publishes_after_commit(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    r = env.redis
    payload = {
        "type": "chat_message",
        "session_id": chat.id,
        "ticket_id": chat.ticket_id,
        "queue_id": QUEUE,
    }
    async with env.factory() as db:
        queue_wakeup(db, chat.id)
        queue_agent_event(db, payload)
        await flush_chat_notifications(db, r)  # nothing committed yet: nothing published
        assert r.published == []
        await db.commit()
        await flush_chat_notifications(db, r)
        assert (chat_channel(chat.id), '{"type": "wake"}') in r.published
        assert any('"chat_message"' in m for ch, m in r.published if ch == "tiqora:events")
        assert WAKEUPS_KEY not in db.info and AGENT_EVENTS_KEY not in db.info
        # a second flush publishes nothing again
        count = len(r.published)
        await flush_chat_notifications(db, r)
        assert len(r.published) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_rolled_back_work_is_never_announced(env: Env) -> None:
    await _agent_online(env)
    chat = await _start(env)
    r = FakeRedis()
    async with env.factory() as db:
        await post_visitor_message(
            db, SysConfig(db), chat=chat, body="verworfen", client_id=None, now=NOW
        )
        assert db.info[AGENT_EVENTS_KEY]
        await db.rollback()
        await db.commit()
        await flush_chat_notifications(db, r)
    assert r.published == []
    # a SAVEPOINT release is not the commit; the outer commit is
    async with env.factory() as db:
        async with db.begin_nested():
            queue_wakeup(db, chat.id)
        await flush_chat_notifications(db, r)
        assert r.published == []
        await db.commit()
        await flush_chat_notifications(db, r)
    assert r.published == [(chat_channel(chat.id), '{"type": "wake"}')]
    # closing a session with queued work drops it
    async with env.factory() as db:
        queue_wakeup(db, chat.id)
        await db.execute(text("SELECT 1"))
        await db.close()
        await flush_chat_notifications(db, r)
    assert len(r.published) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_services_fit_the_handler_transaction_pattern(env: Env) -> None:
    """R-TX: pre-checks read and commit, then the write runs in ``async with db.begin()``
    (no InvalidRequestError: the services never open a transaction themselves), then the
    handler flushes the notifications of the committed block."""
    await _agent_online(env)
    chat = await _start(env)
    r = FakeRedis()
    async with env.factory() as db:
        cfg = await load_config(db, CONFIG)  # pre-check read autobegins ...
        assert cfg is not None
        await db.commit()  # ... and is closed before the write block
        async with db.begin():
            m = await post_visitor_message(
                db, SysConfig(db), chat=chat, body="Über begin()", client_id=None, now=NOW
            )
        await flush_chat_notifications(db, r)
    assert m.seq == 2
    assert (chat_channel(chat.id), '{"type": "wake"}') in r.published
    async with env.factory() as db:
        with pytest.raises(ChatError):
            async with db.begin():
                await end_by_visitor(db, chat=chat, now=NOW)
                await post_visitor_message(
                    db, SysConfig(db), chat=chat, body="zu spät", client_id=None, now=NOW
                )
        await flush_chat_notifications(db, r)  # the block rolled back: nothing announced
    assert len(r.published) == 2
    assert (await _chat_row(env, chat.id)).status == "waiting"


@pytest.mark.asyncio
@pytest.mark.parametrize("env", _URLS, indirect=True)
async def test_offline_address_from_prechat_and_missing_default_customer(env: Env) -> None:
    cfg, values = await _config(env)
    payload = ChatOfflineIn(
        key=KEY,
        origin=ORIGIN,
        visitor_id=VISITOR,
        message="Rückruf bitte",
        prechat={"email": "erika@example.org"},
    )
    async with env.factory() as db:
        await submit_offline(
            db, env.factory, SysConfig(db), config=cfg, values=values, payload=payload,
            ctx=CTX, now=NOW,
        )  # fmt: skip
        await db.commit()
        cid = await _scalar(
            env,
            "SELECT customer_user_id FROM ticket WHERE queue_id = :q ORDER BY id DESC LIMIT 1",
            q=QUEUE,
        )
        assert cid == "erika@example.org"
        # a default customer deleted since the config was saved is no customer at all
        assert await resolve_chat_customer(
            db, email=None, default_customer_user="wc48-deleted-login"
        ) == (None, None)
