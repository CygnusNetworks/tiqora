"""Agent chat API: availability, topics, inbox, first-accept-wins, end (band 48620-48669).

Concurrency tests run accepts, ends and visitor writes on SEPARATE connections (one
``AsyncSession`` each) against MariaDB and PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
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
from tiqora.api.deps import get_current_user, get_redis
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatSession
from tiqora.domain.auth import AuthenticatedUser
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL
from tiqora.webchat.agent import accept_session, end_session_by_agent
from tiqora.webchat.availability import AgentStateMissing, mark_agent_online, set_agent_state
from tiqora.webchat.hub import chat_channel
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import ChatError, VisitorContext, create_session, post_visitor_message
from tiqora.webchat.store import config_values, get_chat_session, load_config
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

G_SUP, CONFIG, Q_SUP, Q_BILL = 48620, 48621, 48622, 48623
AGENT_A, AGENT_B, RO_AGENT, G_BILL = 48624, 48625, 48626, 48627
CONFIG_OFF, CONFIG_BILL, Q_SUP2 = 48628, 48629, 48630
CONFIG_IDS = range(48620, 48670)
KEY, KEY_OFF, KEY_BILL = "wc_" + "9a" * 16, "wc_" + "9b" * 16, "wc_" + "9c" * 16
ORIGIN = "https://www.example.org"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef", user_agent=None, page_url=None, language="de"
)
TOPICS = [
    {"key": "support", "label": "Support", "queue_id": Q_SUP},
    {"key": "billing", "label": "Rechnung", "queue_id": Q_BILL},
]
SUPPORT_REF = f"{CONFIG}:support"


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
            queue_ids=[Q_SUP, Q_BILL, Q_SUP2],
            group_ids=[G_SUP, G_BILL],
            user_ids=[AGENT_A, AGENT_B, RO_AGENT],
        )
    engine.dispose()


def _seed(conn: Any) -> None:
    seed_queue_agent(conn, queue_id=Q_SUP, group_id=G_SUP, user_id=AGENT_A, login="wc48-agent-a")
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:g, 'wc48-grp-bill', 1, :t, 1, :t, 1)"
        ),
        {"g": G_BILL, "t": NOW},
    )
    for qid, name, gid in ((Q_BILL, "wc48-queue-bill", G_BILL), (Q_SUP2, "wc48-queue-sup2", G_SUP)):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": qid, "n": name, "g": gid, "t": NOW},
        )
    for uid, login, first in ((AGENT_B, "wc48-agent-b", "Bert"), (RO_AGENT, "wc48-ro", "Rita")):
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :l, 'x', :f, 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"u": uid, "l": login, "f": first, "t": NOW},
        )
    grants = [
        (AGENT_A, G_BILL, "ro"),
        (AGENT_B, G_SUP, "ro"),
        (AGENT_B, G_SUP, "rw"),
        (AGENT_B, G_BILL, "ro"),
        (RO_AGENT, G_SUP, "ro"),
    ]
    for uid, gid, key in grants:
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": uid, "g": gid, "k": key, "t": NOW},
        )
    insert_config(conn, config_id=CONFIG, queue_id=Q_SUP, public_key=KEY, origins=[ORIGIN])
    conn.execute(
        text("UPDATE tiqora_chat_config SET topics = :t WHERE id = :c"),
        {"t": json.dumps(TOPICS), "c": CONFIG},
    )
    # Switched off: its rw topic must not be offered.
    insert_config(
        conn, config_id=CONFIG_OFF, queue_id=Q_SUP, public_key=KEY_OFF, origins=[], valid=False
    )
    # Only a topic in the ro queue.
    insert_config(conn, config_id=CONFIG_BILL, queue_id=Q_BILL, public_key=KEY_BILL, origins=[])
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
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        _seed(conn)
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    environment = Env(url, factory, FakeRedis())
    for uid in (AGENT_A, AGENT_B):
        await _set_state(environment, uid, max_concurrent=3)
        await mark_agent_online(environment.redis, uid)
    yield environment
    await aengine.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


# -- helpers -------------------------------------------------------------------------------


async def _set_state(env: Env, user_id: int, *, max_concurrent: int) -> None:
    async with env.factory() as db:
        await set_agent_state(
            db, user_id, available=True, topics=[], max_concurrent=max_concurrent, now=NOW
        )
        await db.commit()


async def _start(env: Env, n: int, message: str = "Mein Router blinkt rot") -> TiqoraChatSession:
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
                visitor_id=f"visitor_wc48_{n:010d}",
                topic="support",
                prechat={"name": f"Erika {n}"},
                message=message,
            ),
            ctx=CTX,
            now=NOW,
            settings=get_settings(),
        )
        await db.commit()
        return chat


def _client(env: Env, user_id: int) -> AsyncClient:
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
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _accept(env: Env, session_id: str, user_id: int) -> str:
    """One accept on its own connection; ``"ok"`` or the ChatError code."""
    async with env.factory() as db:
        try:
            await accept_session(db, SysConfig(db), session_id=session_id, user_id=user_id, now=NOW)
        except ChatError as exc:
            await db.rollback()
            return exc.code
        await db.commit()
        return "ok"


async def _rows(env: Env, sql: str, **params: Any) -> list[Any]:
    async with env.factory() as db:
        return list((await db.execute(text(sql), params)).all())


async def _chat(env: Env, session_id: str) -> TiqoraChatSession:
    async with env.factory() as db:
        row = await get_chat_session(db, session_id)
        assert row is not None
        return row


async def _history(env: Env, ticket_id: int) -> list[str]:
    rows = await _rows(
        env,
        "SELECT ht.name FROM ticket_history h"
        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
        " WHERE h.ticket_id = :t ORDER BY h.id",
        t=ticket_id,
    )
    return [str(r[0]) for r in rows]


async def _messages(env: Env, session_id: str) -> list[tuple[int, str, str, Any]]:
    rows = await _rows(
        env,
        "SELECT seq, sender, body, meta FROM tiqora_chat_message WHERE session_id = :s"
        " ORDER BY seq",
        s=session_id,
    )
    return [(int(r[0]), str(r[1]), str(r[2]), json.loads(r[3]) if r[3] else None) for r in rows]


async def _ticket(env: Env, ticket_id: int) -> dict[str, Any]:
    rows = await _rows(
        env,
        "SELECT user_id, ticket_lock_id, ticket_state_id, queue_id FROM ticket WHERE id = :t",
        t=ticket_id,
    )
    user_id, lock_id, state_id, queue_id = rows[0]
    name = (await _rows(env, "SELECT name FROM ticket_state WHERE id = :s", s=state_id))[0][0]
    return {"owner": int(user_id), "lock": int(lock_id), "state": str(name), "queue": int(queue_id)}


async def _blocked(task: asyncio.Task[Any], seconds: float = 0.6) -> bool:
    await asyncio.sleep(seconds)
    return not task.done()


def _assert_gapless(msgs: list[tuple[int, str, str, Any]]) -> None:
    assert [m[0] for m in msgs] == list(range(1, len(msgs) + 1))


# -- availability and topics ----------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_agent_state_put_get(env: Env) -> None:
    async with _client(env, AGENT_A) as client:
        res = await client.put(
            "/api/v1/chat/agent-state",
            json={"available": True, "topics": [SUPPORT_REF], "max_concurrent": 2},
        )
        assert res.status_code == 200, res.text
        expected = {
            "available": True,
            "topics": [SUPPORT_REF],
            "max_concurrent": 2,
            "active_count": 0,
        }
        assert res.json() == expected
        assert (await client.get("/api/v1/chat/agent-state")).json() == expected

        # billing: A has only ro on its queue; the other two refs do not exist for A
        for ref in (f"{CONFIG}:billing", f"{CONFIG_BILL}:support", f"{CONFIG_OFF}:support"):
            res = await client.put(
                "/api/v1/chat/agent-state",
                json={"available": False, "topics": [SUPPORT_REF, ref], "max_concurrent": 5},
            )
            assert res.status_code == 422
            assert res.json() == {"detail": "unknown_topic"}
        assert (await client.get("/api/v1/chat/agent-state")).json() == expected

        res = await client.put(
            "/api/v1/chat/agent-state",
            json={"available": True, "topics": [], "max_concurrent": 21},
        )
        assert res.status_code == 422


@pytest.mark.asyncio
@parametrize_db
async def test_topics_only_rw_queues(env: Env) -> None:
    async with _client(env, AGENT_A) as client:
        res = await client.get("/api/v1/chat/topics")
    assert res.status_code == 200
    assert res.json() == [
        {
            "ref": SUPPORT_REF,
            "config_name": f"wc48-config-{CONFIG}",
            "label": "Support",
            "queue_id": Q_SUP,
        }
    ]
    async with _client(env, RO_AGENT) as client:
        assert (await client.get("/api/v1/chat/topics")).json() == []


# -- inbox ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_inbox_lists_waiting_in_rw_queues_and_mine(env: Env) -> None:
    first = await _start(env, 1, "Router blinkt")
    second = await _start(env, 2, "Rechnung falsch")
    # R-QUEUE-SOT: the ticket moved to the ro queue; the session still says Q_SUP.
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"),
            {"q": Q_BILL, "t": second.ticket_id},
        )
        await db.commit()
    tn = (await _rows(env, "SELECT tn FROM ticket WHERE id = :t", t=first.ticket_id))[0][0]

    async with _client(env, AGENT_A) as client:
        res = await client.get("/api/v1/chat/inbox")
        assert res.status_code == 200
        body = res.json()
        assert body["mine"] == []
        assert body["bot"] == []
        assert [i["session_id"] for i in body["waiting"]] == [first.id]
        item = body["waiting"][0]
        assert item["ticket_id"] == first.ticket_id
        assert item["tn"] == str(tn)
        assert item["title"] == "Chat: Router blinkt"
        assert item["topic_label"] == "Support"
        assert item["visitor_name"] == "Erika 1"
        assert item["status"] == "waiting"
        assert item["waiting_since"].startswith("2026-10-10T12:00:00")
        assert item["accepted_at"] is None

        res = await client.post(f"/api/v1/chat/sessions/{first.id}/accept")
        assert res.status_code == 200, res.text
        assert res.json() == {"ticket_id": first.ticket_id}

        body = (await client.get("/api/v1/chat/inbox")).json()
        assert body["waiting"] == []
        assert [i["session_id"] for i in body["mine"]] == [first.id]
        assert body["mine"][0]["status"] == "active"
        assert body["mine"][0]["accepted_at"] is not None

    async with _client(env, AGENT_B) as client:
        assert (await client.get("/api/v1/chat/inbox")).json() == {
            "waiting": [],
            "mine": [],
            "bot": [],
        }


# -- first accept wins ----------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_first_accept_wins_both_dialects(env: Env) -> None:
    """Two agents accept the same chat at once on two connections, several rounds."""
    for n in range(3):  # capacity 3 each: the winner never reaches it
        chat = await _start(env, 10 + n)
        history_before = await _history(env, chat.ticket_id)
        results = await asyncio.gather(
            _accept(env, chat.id, AGENT_A), _accept(env, chat.id, AGENT_B)
        )
        assert sorted(results) == ["already_taken", "ok"], results
        winner = AGENT_A if results[0] == "ok" else AGENT_B
        row = await _chat(env, chat.id)
        assert row.status == "active"
        assert row.assigned_user_id == winner
        joined = [m for m in await _messages(env, chat.id) if m[2] == "agent_joined"]
        assert len(joined) == 1
        added = (await _history(env, chat.ticket_id))[len(history_before) :]
        assert [h for h in added if h in ("Lock", "OwnerUpdate")] == ["Lock", "OwnerUpdate"]
        ticket = await _ticket(env, chat.ticket_id)
        assert (ticket["owner"], ticket["lock"]) == (winner, 2)


@pytest.mark.asyncio
@parametrize_db
async def test_second_accept_waits_for_the_first_commit(env: Env) -> None:
    """The loser blocks on the session row until the winner commits, then sees 'active'."""
    chat = await _start(env, 20)
    async with env.factory() as db_a:
        await accept_session(db_a, SysConfig(db_a), session_id=chat.id, user_id=AGENT_A, now=NOW)
        task = asyncio.create_task(_accept(env, chat.id, AGENT_B))
        assert await _blocked(task)
        await db_a.commit()
    assert await asyncio.wait_for(task, 10) == "already_taken"
    assert (await _chat(env, chat.id)).assigned_user_id == AGENT_A
    added = await _history(env, chat.ticket_id)
    assert added.count("OwnerUpdate") == 1
    assert added.count("Lock") == 1


@pytest.mark.asyncio
@parametrize_db
async def test_accept_at_capacity_conflict(env: Env) -> None:
    held = await _start(env, 30)
    waiting = await _start(env, 31)
    await _set_state(env, AGENT_A, max_concurrent=1)
    assert await _accept(env, held.id, AGENT_A) == "ok"
    history_before = await _history(env, waiting.ticket_id)
    async with _client(env, AGENT_A) as client:
        res = await client.post(f"/api/v1/chat/sessions/{waiting.id}/accept")
    assert res.status_code == 409
    assert res.json() == {"detail": "at_capacity"}
    row = await _chat(env, waiting.id)
    assert (row.status, row.assigned_user_id) == ("waiting", None)
    assert await _history(env, waiting.ticket_id) == history_before
    assert [m[2] for m in await _messages(env, waiting.id)] == ["Mein Router blinkt rot"]


@pytest.mark.asyncio
@parametrize_db
async def test_same_agent_concurrent_accepts_respect_capacity(env: Env) -> None:
    """Capacity is counted under the agent-state lock: the second accept waits for that
    lock and its count (the transaction's first plain read, so MariaDB's REPEATABLE READ
    snapshot is taken only then) sees the first accept's commit."""
    one = await _start(env, 40)
    two = await _start(env, 41)
    await _set_state(env, AGENT_A, max_concurrent=1)
    async with env.factory() as db_1:
        await accept_session(db_1, SysConfig(db_1), session_id=one.id, user_id=AGENT_A, now=NOW)

        async def second() -> str:
            async with env.factory() as db_2:
                try:
                    await accept_session(
                        db_2, SysConfig(db_2), session_id=two.id, user_id=AGENT_A, now=NOW
                    )
                except ChatError as exc:
                    await db_2.rollback()
                    return exc.code
                await db_2.commit()
                return "ok"

        task = asyncio.create_task(second())
        assert await _blocked(task)  # waits for the agent-state row lock
        await db_1.commit()
    assert await asyncio.wait_for(task, 10) == "at_capacity"
    assert (await _chat(env, two.id)).status == "waiting"

    # And plain gather: two more chats, capacity 2 with one held → exactly one more.
    three, four = await _start(env, 42), await _start(env, 43)
    await _set_state(env, AGENT_A, max_concurrent=2)
    results = await asyncio.gather(_accept(env, three.id, AGENT_A), _accept(env, four.id, AGENT_A))
    assert sorted(results) == ["at_capacity", "ok"]
    active = await _rows(
        env,
        "SELECT COUNT(*) FROM tiqora_chat_session"
        " WHERE assigned_user_id = :u AND status = 'active'",
        u=AGENT_A,
    )
    assert int(active[0][0]) == 2


@pytest.mark.asyncio
@parametrize_db
async def test_accept_refuses_a_started_transaction(env: Env) -> None:
    """A read before the accept would pin MariaDB's snapshot before the agent-state lock;
    the service refuses instead of counting capacity from it."""
    chat = await _start(env, 45)
    async with env.factory() as db:
        await db.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="own transaction"):
            await accept_session(db, SysConfig(db), session_id=chat.id, user_id=AGENT_A, now=NOW)
        await db.rollback()
    assert (await _chat(env, chat.id)).status == "waiting"


async def _drop_states(env: Env, *user_ids: int) -> None:
    async with env.factory() as db:
        for uid in user_ids:
            await db.execute(
                text("DELETE FROM tiqora_chat_agent_state WHERE user_id = :u"), {"u": uid}
            )
        await db.commit()


@pytest.mark.asyncio
@parametrize_db
async def test_service_accept_needs_an_existing_agent_state_row(env: Env) -> None:
    """No insert after a locking read of a missing row (gap lock → 1213 on MariaDB)."""
    chat = await _start(env, 46)
    await _drop_states(env, AGENT_A)
    async with env.factory() as db:
        with pytest.raises(AgentStateMissing, match="ensure_agent_state_row"):
            await accept_session(db, SysConfig(db), session_id=chat.id, user_id=AGENT_A, now=NOW)
        await db.rollback()
    assert (await _chat(env, chat.id)).status == "waiting"


@pytest.mark.asyncio
@parametrize_db
async def test_first_ever_accepts_through_the_route(env: Env) -> None:
    """Agents without a state row accept at the same time through the real route: on the
    same chat exactly one wins; on two chats (two agents, or one agent twice) both win —
    never a deadlock or a 500."""
    same = await _start(env, 110)
    one, two = await _start(env, 111), await _start(env, 112)
    three, four = await _start(env, 113), await _start(env, 114)

    async def post(user_id: int, session_id: str) -> tuple[int, Any]:
        async with _client(env, user_id) as client:
            res = await client.post(f"/api/v1/chat/sessions/{session_id}/accept")
            return res.status_code, res.json()

    await _drop_states(env, AGENT_A, AGENT_B)
    results = await asyncio.gather(post(AGENT_A, same.id), post(AGENT_B, same.id))
    assert sorted(code for code, _ in results) == [200, 409], results
    assert [body for code, body in results if code == 409] == [{"detail": "already_taken"}]
    assert len([m for m in await _messages(env, same.id) if m[2] == "agent_joined"]) == 1

    await _drop_states(env, AGENT_A, AGENT_B)
    results = await asyncio.gather(post(AGENT_A, one.id), post(AGENT_B, two.id))
    assert [code for code, _ in results] == [200, 200], results
    assert (await _chat(env, one.id)).assigned_user_id == AGENT_A
    assert (await _chat(env, two.id)).assigned_user_id == AGENT_B

    # One agent twice: free A's capacity (default 3) first — A holds one or two chats now.
    for held in (same, one):
        if (await _chat(env, held.id)).assigned_user_id == AGENT_A:
            await _end(env, held.id, AGENT_A, close=False)
    await _drop_states(env, AGENT_A)
    results = await asyncio.gather(post(AGENT_A, three.id), post(AGENT_A, four.id))
    assert [code for code, _ in results] == [200, 200], results
    rows = await _rows(
        env, "SELECT COUNT(*) FROM tiqora_chat_agent_state WHERE user_id = :u", u=AGENT_A
    )
    assert int(rows[0][0]) == 1


@pytest.mark.asyncio
@parametrize_db
async def test_accept_writes_lock_then_owner_history(env: Env) -> None:
    chat = await _start(env, 50)
    before = await _ticket(env, chat.ticket_id)
    assert (before["owner"], before["lock"]) == (1, 1)
    history_before = await _history(env, chat.ticket_id)
    assert await _accept(env, chat.id, AGENT_A) == "ok"
    after = await _ticket(env, chat.ticket_id)
    assert (after["owner"], after["lock"]) == (AGENT_A, 2)
    added = (await _history(env, chat.ticket_id))[len(history_before) :]
    # R-LOCK-HISTORY: TicketLockSet writes "Reset of unlock time." (Misc) between them.
    assert added == ["Lock", "Misc", "OwnerUpdate"]

    # Locked by someone else: only the owner changes (no second Lock row).
    other = await _start(env, 51)
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET ticket_lock_id = 2, user_id = :u WHERE id = :t"),
            {"u": AGENT_B, "t": other.ticket_id},
        )
        await db.commit()
    history_before = await _history(env, other.ticket_id)
    assert await _accept(env, other.id, AGENT_A) == "ok"
    added = (await _history(env, other.ticket_id))[len(history_before) :]
    assert added == ["OwnerUpdate"]
    assert (await _ticket(env, other.ticket_id))["owner"] == AGENT_A


@pytest.mark.asyncio
@parametrize_db
async def test_accept_forbidden_without_rw(env: Env) -> None:
    chat = await _start(env, 60)
    async with _client(env, RO_AGENT) as client:
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")
        assert res.status_code == 403
        assert res.json() == {"detail": "forbidden"}
        res = await client.post("/api/v1/chat/sessions/" + "0" * 32 + "/accept")
        assert res.status_code == 404
        assert res.json() == {"detail": "not_found"}
    row = await _chat(env, chat.id)
    assert (row.status, row.assigned_user_id) == ("waiting", None)
    ticket = await _ticket(env, chat.ticket_id)
    assert (ticket["owner"], ticket["lock"]) == (1, 1)
    assert [m[2] for m in await _messages(env, chat.id)] == ["Mein Router blinkt rot"]


# -- end ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_end_by_agent_optionally_closes(env: Env) -> None:
    chat = await _start(env, 70)
    plain = await _start(env, 71)
    async with _client(env, AGENT_A) as client:
        assert (await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")).status_code == 200
        state_before = (await _ticket(env, chat.ticket_id))["state"]
        assert state_before != "closed successful"
        history_before = await _history(env, chat.ticket_id)

        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end", json={"close_ticket": True})
        assert res.status_code == 204
        row = await _chat(env, chat.id)
        assert (row.status, row.end_reason) == ("ended", "agent")
        assert row.ended_at is not None
        msgs = await _messages(env, chat.id)
        assert msgs[-1][1:3] == ("system", "chat_ended")
        _assert_gapless(msgs)
        assert (await _ticket(env, chat.ticket_id))["state"] == "closed successful"
        history = await _history(env, chat.ticket_id)
        assert "StateUpdate" in history[len(history_before) :]

        # Idempotent: no new line, no new history.
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end", json={"close_ticket": True})
        assert res.status_code == 204
        assert await _messages(env, chat.id) == msgs
        assert await _history(env, chat.ticket_id) == history

        # Without close_ticket (and without a body) the ticket state stays.
        plain_state = (await _ticket(env, plain.ticket_id))["state"]
        res = await client.post(f"/api/v1/chat/sessions/{plain.id}/end")
        assert res.status_code == 204
        assert (await _chat(env, plain.id)).status == "ended"
        assert (await _ticket(env, plain.ticket_id))["state"] == plain_state

    other = await _start(env, 72)
    async with _client(env, RO_AGENT) as client:
        res = await client.post(f"/api/v1/chat/sessions/{other.id}/end")
    assert res.status_code == 403
    assert res.json() == {"detail": "forbidden"}
    assert (await _chat(env, other.id)).status == "waiting"


@pytest.mark.asyncio
@parametrize_db
async def test_assigned_agent_may_end_own_chat_after_queue_move(env: Env) -> None:
    """R-END-OWN-CHAT: the ticket moved to a queue where the assignee has only ro."""
    chat = await _start(env, 75)
    assert await _accept(env, chat.id, AGENT_A) == "ok"
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"),
            {"q": Q_BILL, "t": chat.ticket_id},
        )
        await db.commit()
    state_before = (await _ticket(env, chat.ticket_id))["state"]

    async with _client(env, AGENT_B) as client:  # not the assignee, ro on Q_BILL
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end")
    assert (res.status_code, res.json()) == (403, {"detail": "forbidden"})

    async with _client(env, AGENT_A) as client:
        # Closing the ticket still needs rw: refused, nothing written.
        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end", json={"close_ticket": True})
        assert (res.status_code, res.json()) == (403, {"detail": "forbidden"})
        assert (await _chat(env, chat.id)).status == "active"
        assert (await _ticket(env, chat.ticket_id))["state"] == state_before

        res = await client.post(f"/api/v1/chat/sessions/{chat.id}/end")
        assert res.status_code == 204
    row = await _chat(env, chat.id)
    assert (row.status, row.end_reason) == ("ended", "agent")
    assert (await _messages(env, chat.id))[-1][2] == "chat_ended"
    assert (await _ticket(env, chat.ticket_id))["state"] == state_before


# -- notifications --------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_events_published_after_commit(env: Env) -> None:
    chat = await _start(env, 80)
    # R-QUEUE-SOT: the ticket moved to another rw queue after the chat started.
    async with env.factory() as db:
        await db.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"),
            {"q": Q_SUP2, "t": chat.ticket_id},
        )
        await db.commit()
    env.redis.published.clear()

    async with _client(env, RO_AGENT) as client:
        assert (await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")).status_code == 403
    assert env.redis.published == []

    async with _client(env, AGENT_A) as client:
        assert (await client.post(f"/api/v1/chat/sessions/{chat.id}/accept")).status_code == 200
    events = [json.loads(p) for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert events == [
        {
            "type": "chat_assigned",
            "session_id": chat.id,
            "ticket_id": chat.ticket_id,
            "queue_id": Q_SUP2,
            "user_id": AGENT_A,
        }
    ]
    wakes = [json.loads(p) for c, p in env.redis.published if c == chat_channel(chat.id)]
    assert wakes == [{"type": "wake"}]
    assert (await _chat(env, chat.id)).queue_id == Q_SUP2

    msgs = await _messages(env, chat.id)
    assert msgs[-1][1:] == ("system", "agent_joined", {"name": "Chat"})

    env.redis.published.clear()
    async with _client(env, AGENT_A) as client:
        assert (await client.post(f"/api/v1/chat/sessions/{chat.id}/end")).status_code == 204
    types = [json.loads(p)["type"] for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert types == ["chat_ended"]


@pytest.mark.asyncio
@parametrize_db
async def test_accept_reads_the_queue_under_the_ticket_lock(env: Env) -> None:
    """The ticket moves while the accept waits for the session row: the event and the
    session copy carry the NEW queue (a plain read would see MariaDB's older snapshot)."""
    from tiqora.webchat.notify import flush_chat_notifications

    chat = await _start(env, 85)
    env.redis.published.clear()

    async def accept() -> str:
        async with env.factory() as db:
            await accept_session(db, SysConfig(db), session_id=chat.id, user_id=AGENT_A, now=NOW)
            await db.commit()
            await flush_chat_notifications(db, env.redis)
            return "ok"

    async with env.factory() as db_x:
        assert await get_chat_session(db_x, chat.id, for_update=True) is not None
        task = asyncio.create_task(accept())
        assert await _blocked(task)  # snapshot taken, waiting for the session row
        await db_x.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"),
            {"q": Q_SUP2, "t": chat.ticket_id},
        )
        await db_x.commit()
    assert await asyncio.wait_for(task, 10) == "ok"
    events = [json.loads(p) for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert [e["queue_id"] for e in events] == [Q_SUP2]
    assert (await _chat(env, chat.id)).queue_id == Q_SUP2


# -- lock order (R-LOCK-ORDER): accept vs. end vs. visitor write -----------------------------


async def _guard(coro: Awaitable[Any]) -> str:
    """Run one concurrent writer; ChatError → its code; any other error fails the test."""
    try:
        await coro
    except ChatError as exc:
        return exc.code
    return "ok"


async def _visitor_line(env: Env, chat: TiqoraChatSession, body: str) -> None:
    async with env.factory() as db:
        try:
            await post_visitor_message(
                db, SysConfig(db), chat=chat, body=body, client_id=None, now=NOW
            )
        except ChatError:
            await db.rollback()
            raise
        await db.commit()


async def _end(env: Env, session_id: str, user_id: int, *, close: bool) -> None:
    async with env.factory() as db:
        try:
            await end_session_by_agent(
                db,
                SysConfig(db),
                session_id=session_id,
                user_id=user_id,
                close_ticket=close,
                now=NOW,
                factory=env.factory,
            )
        except ChatError:
            await db.rollback()
            raise
        await db.commit()


@pytest.mark.asyncio
@parametrize_db
async def test_lock_order_accept_end_visitor_no_deadlock(env: Env) -> None:
    """Accept (agent state → own active chats → session → ticket → line), an end of one of
    the same agent's active chats (session → ticket → line) and visitor lines on both chats
    (session → ticket → line) run together: no deadlock, consistent final state."""
    await _set_state(env, AGENT_A, max_concurrent=20)
    for n in range(3):
        held = await _start(env, 90 + 2 * n)
        target = await _start(env, 91 + 2 * n)
        assert await _accept(env, held.id, AGENT_A) == "ok"
        results = await asyncio.gather(
            _guard(_visitor_line(env, target, f"Hallo {n}")),
            _guard(_accept(env, target.id, AGENT_A)),
            _guard(_end(env, held.id, AGENT_A, close=True)),
            _guard(_visitor_line(env, held, f"Noch da {n}")),
            _guard(_visitor_line(env, target, f"Noch eine {n}")),
        )
        assert results[0] == "ok" and results[4] == "ok"
        assert results[1] == "ok" and results[2] == "ok"
        assert results[3] in ("ok", "session_closed")
        assert (await _chat(env, target.id)).assigned_user_id == AGENT_A
        assert (await _chat(env, target.id)).status == "active"
        assert (await _chat(env, held.id)).status == "ended"
        for chat in (held, target):
            msgs = await _messages(env, chat.id)
            _assert_gapless(msgs)
            assert (await _chat(env, chat.id)).last_seq == len(msgs)
        assert [m[2] for m in await _messages(env, target.id)].count("agent_joined") == 1
        assert (await _ticket(env, held.ticket_id))["state"] == "closed successful"


@pytest.mark.asyncio
@parametrize_db
async def test_end_racing_the_capacity_read_does_not_deadlock(env: Env) -> None:
    """An end holds one of the agent's active chats (row lock) while the same agent accepts
    another chat and counts its active chats; the end then moves that row out of
    ``active`` and commits. The count takes no locks, so neither side waits on the other."""
    held = await _start(env, 100)
    target = await _start(env, 101)
    assert await _accept(env, held.id, AGENT_A) == "ok"
    async with env.factory() as db_e:
        assert await get_chat_session(db_e, held.id, for_update=True) is not None
        task = asyncio.create_task(_accept(env, target.id, AGENT_A))
        await asyncio.sleep(0.6)
        await end_session_by_agent(
            db_e,
            SysConfig(db_e),
            session_id=held.id,
            user_id=AGENT_A,
            close_ticket=True,
            now=NOW,
            factory=env.factory,
        )
        await db_e.commit()
    assert await asyncio.wait_for(task, 10) == "ok"
    assert (await _chat(env, held.id)).status == "ended"
    assert (await _chat(env, target.id)).status == "active"
