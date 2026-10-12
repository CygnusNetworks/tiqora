"""Web chat sweep: waiting timeout, idle chats, retention (band 48700-48729)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, insert, text
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
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL
from tiqora.webchat import sweep as sweep_module
from tiqora.webchat.availability import VISITOR_SEEN_PREFIX, mark_agent_online, set_agent_state
from tiqora.webchat.hub import chat_channel
from tiqora.webchat.schemas import ChatSessionCreateIn
from tiqora.webchat.sessions import VisitorContext, create_session
from tiqora.webchat.store import config_values, load_config
from tiqora.webchat.sweep import ACTIVE_IDLE_SECONDS, run_webchat_sweep_tick
from tiqora.worker import webchat as worker_module
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE, CONFIG, CONFIG_OFF, AGENT = 48700, 48701, 48702, 48703, 48704
CONFIG_IDS = range(48700, 48730)
KEY, KEY_OFF = "wc_" + "7a" * 16, "wc_" + "7b" * 16
ORIGIN = "https://www.example.org"
SWITCH = "daemon.webchat_sweep.enabled"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CTX = VisitorContext(
    ip_hash="0123456789abcdef0123456789abcdef", user_agent="UA", page_url=None, language="de"
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
            user_ids=[AGENT],
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
        seed_queue_agent(
            conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-sweep-agent"
        )
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        enable_webchat(conn)
        insert_config(
            conn, config_id=CONFIG_OFF, queue_id=QUEUE, public_key=KEY_OFF, origins=[ORIGIN]
        )
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    environment = Env(url, factory, FakeRedis())
    async with factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(environment.redis, AGENT)
    yield environment
    await aengine.dispose()
    delete_above_marks(url, marks, first=mark_cleanup_first(marks))
    _cleanup(url)


parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


async def _start(
    env: Env, n: int, *, config_id: int = CONFIG, key: str = KEY, email: str | None = None
) -> str:
    """A waiting chat with a real ticket; returns the session id."""
    async with env.factory() as db:
        cfg = await load_config(db, config_id)
        assert cfg is not None
        prechat = {"name": f"Erika {n}"}
        if email:
            prechat["email"] = email
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
        return chat.id


async def _disable_config(env: Env) -> None:
    async with env.factory() as db:
        await db.execute(
            text("UPDATE tiqora_chat_config SET valid = :v WHERE id = :c"),
            {"v": False, "c": CONFIG_OFF},
        )
        await db.commit()


async def _set(env: Env, session_id: str, **cols: Any) -> None:
    sets = ", ".join(f"{c} = :{c}" for c in cols)
    async with env.factory() as db:
        await db.execute(
            text(f"UPDATE tiqora_chat_session SET {sets} WHERE id = :sid"),
            {**cols, "sid": session_id},
        )
        await db.commit()


async def _row(env: Env, session_id: str) -> dict[str, Any]:
    async with env.factory() as db:
        res = await db.execute(
            text("SELECT * FROM tiqora_chat_session WHERE id = :s"), {"s": session_id}
        )
        return dict(res.mappings().one())


async def _messages(env: Env, session_id: str) -> list[tuple[str, str, Any]]:
    async with env.factory() as db:
        res = await db.execute(
            text(
                "SELECT sender, body, meta FROM tiqora_chat_message WHERE session_id = :s"
                " ORDER BY seq"
            ),
            {"s": session_id},
        )
        return [(r[0], r[1], json.loads(r[2]) if r[2] else None) for r in res.all()]


async def _ticket_facts(env: Env, session_id: str) -> tuple[Any, ...]:
    row = await _row(env, session_id)
    async with env.factory() as db:
        t = (
            await db.execute(
                text(
                    "SELECT ticket_state_id, ticket_lock_id, change_time FROM ticket WHERE id = :t"
                ),
                {"t": row["ticket_id"]},
            )
        ).one()
        n = (
            await db.execute(
                text("SELECT COUNT(*) FROM article WHERE ticket_id = :t"), {"t": row["ticket_id"]}
            )
        ).scalar_one()
    return (*t, n)


async def _seen(env: Env, session_id: str) -> None:
    await env.redis.set(f"{VISITOR_SEEN_PREFIX}{session_id}", "1", ex=90)


async def _tick(env: Env, now: datetime = NOW) -> dict[str, int]:
    return await run_webchat_sweep_tick(
        session_factory=env.factory, redis_client=env.redis, now=now
    )


@pytest.mark.asyncio
@parametrize_db
async def test_waiting_timeout_ends_with_no_agent(env: Env) -> None:
    late = await _start(env, 1)
    early = await _start(env, 2)
    with_mail = await _start(env, 3, email="erika@example.org")
    await _set(env, late, waiting_since=NOW - timedelta(seconds=181))
    await _set(env, early, waiting_since=NOW - timedelta(seconds=179))
    await _set(env, with_mail, waiting_since=NOW - timedelta(seconds=500))
    for sid in (late, early, with_mail):
        await _seen(env, sid)
    before = await _ticket_facts(env, late)
    env.redis.published.clear()

    res = await _tick(env)

    assert res == {"timed_out": 2, "abandoned": 0, "purged": 0}
    row = await _row(env, late)
    assert (row["status"], row["end_reason"], row["ended_at"]) == ("ended", "no_agent", NOW)
    assert (await _messages(env, late))[-1] == ("system", "no_agent", {"has_email": False})
    assert (await _messages(env, with_mail))[-1] == ("system", "no_agent", {"has_email": True})
    assert (await _row(env, early))["status"] == "waiting"
    assert await _ticket_facts(env, late) == before  # ticket untouched, still open
    events = [json.loads(p) for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert sorted(e["session_id"] for e in events if e["type"] == "chat_ended") == sorted(
        [late, with_mail]
    )
    assert {e["queue_id"] for e in events} == {QUEUE}
    assert [json.loads(p) for c, p in env.redis.published if c == chat_channel(late)] == [
        {"type": "wake"}
    ]
    assert (await _tick(env))["timed_out"] == 0


@pytest.mark.asyncio
@parametrize_db
async def test_idle_visitor_abandoned(env: Env) -> None:
    active_gone = await _start(env, 10)
    active_seen = await _start(env, 11)
    active_fresh = await _start(env, 12)
    waiting_gone = await _start(env, 13)
    active_edge = await _start(env, 14)
    for sid in (active_gone, active_seen, active_fresh, active_edge):
        await _set(env, sid, status="active", assigned_user_id=AGENT, accepted_at=NOW)
    await _set(env, active_gone, visitor_last_seen=NOW - timedelta(seconds=901))
    await _set(env, active_seen, visitor_last_seen=NOW - timedelta(hours=2))
    await _seen(env, active_seen)
    # R-LASTSEEN: the marker lapsed for a moment, the last seen time is fresh.
    await _set(env, active_fresh, visitor_last_seen=NOW - timedelta(seconds=300))
    await _set(env, active_edge, visitor_last_seen=NOW - timedelta(seconds=899))
    await _set(env, waiting_gone, visitor_last_seen=NOW - timedelta(seconds=121))
    env.redis.published.clear()

    res = await _tick(env)

    assert res == {"timed_out": 0, "abandoned": 2, "purged": 0}
    for sid in (active_gone, waiting_gone):
        row = await _row(env, sid)
        assert (row["status"], row["end_reason"], row["ended_at"]) == ("abandoned", "idle", NOW)
        assert (await _messages(env, sid))[-1] == ("system", "idle_closed", {})
    for sid in (active_seen, active_fresh, active_edge):
        assert (await _row(env, sid))["status"] == "active"
    events = [json.loads(p) for c, p in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
    assert sorted(e["session_id"] for e in events) == sorted([active_gone, waiting_gone])


@pytest.mark.asyncio
@parametrize_db
async def test_fresh_last_seen_without_marker_keeps_chat(env: Env) -> None:
    sid = await _start(env, 20)
    await _set(env, sid, status="active", assigned_user_id=AGENT, accepted_at=NOW)
    await _set(env, sid, visitor_last_seen=NOW - timedelta(seconds=90))
    assert (await _tick(env)) == {"timed_out": 0, "abandoned": 0, "purged": 0}
    assert (await _row(env, sid))["status"] == "active"
    # ...but it goes once the last seen time is stale as well.
    later = NOW + timedelta(seconds=ACTIVE_IDLE_SECONDS)
    assert (await _tick(env, later))["abandoned"] == 1


@pytest.mark.asyncio
@parametrize_db
async def test_retention_purges_messages_and_pii(env: Env) -> None:
    old = await _start(env, 30, email="erika@example.org")
    recent = await _start(env, 31)
    abandoned = await _start(env, 32)
    off = await _start(env, 33, config_id=CONFIG_OFF, key=KEY_OFF)
    await _set(env, old, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=31))
    await _set(env, recent, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=29))
    await _set(
        env, abandoned, status="abandoned", end_reason="idle", ended_at=NOW - timedelta(days=40)
    )
    await _disable_config(env)
    # A disabled config is swept just the same (R-DISABLED-SESSIONS).
    await _set(env, off, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=45))
    for sid in (old, recent, abandoned, off):
        await _seen(env, sid)
    before = await _ticket_facts(env, old)

    res = await _tick(env)

    assert res == {"timed_out": 0, "abandoned": 0, "purged": 3}
    row = await _row(env, old)
    for col in ("visitor_name", "visitor_email", "page_url", "user_agent", "ip_hash"):
        assert row[col] is None, col
    assert row["token_hash"] == ""
    assert row["visitor_id"] == ""
    assert row["purged_at"] == NOW
    assert await _messages(env, old) == []
    assert await _ticket_facts(env, old) == before
    assert (await _row(env, abandoned))["purged_at"] == NOW
    assert (await _row(env, off))["purged_at"] == NOW
    kept = await _row(env, recent)
    assert kept["purged_at"] is None and kept["visitor_name"] is not None
    assert len(await _messages(env, recent)) >= 1
    assert (await _tick(env))["purged"] == 0


@pytest.mark.asyncio
@parametrize_db
async def test_disabled_config_sessions_time_out(env: Env) -> None:
    sid = await _start(env, 40, config_id=CONFIG_OFF, key=KEY_OFF)
    await _disable_config(env)
    await _set(env, sid, waiting_since=NOW - timedelta(seconds=400))
    await _seen(env, sid)
    assert (await _tick(env))["timed_out"] == 1


@pytest.mark.asyncio
@parametrize_db
async def test_row_that_no_longer_qualifies_is_skipped(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch select is stale (a visitor/agent changed the row): the lock re-check skips."""
    fresh = await _start(env, 50)  # waiting since NOW: not timed out
    await _seen(env, fresh)

    async def stale_ids(db: AsyncSession, *conditions: Any, limit: int) -> list[str]:
        return [fresh]

    monkeypatch.setattr(sweep_module, "_ids", stale_ids)
    assert (await _tick(env))["timed_out"] == 0
    assert (await _row(env, fresh))["status"] == "waiting"
    assert [m[1] for m in await _messages(env, fresh) if m[0] == "system"] == []


async def _ids_of(env: Env, *session_ids: str) -> dict[str, str]:
    return {sid: (await _row(env, sid))["status"] for sid in session_ids}


@pytest.mark.asyncio
@parametrize_db
async def test_batch_limit_spreads_over_ticks(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep_module, "SWEEP_BATCH", 2)
    sids = [await _start(env, 60 + i) for i in range(3)]
    sids.append(await _start(env, 63, config_id=CONFIG_OFF, key=KEY_OFF))
    for sid in sids:
        await _set(env, sid, waiting_since=NOW - timedelta(seconds=400))
        await _seen(env, sid)
    # Two configs, four expired rows, batch of 2: never more than 2 per tick.
    assert (await _tick(env))["timed_out"] == 2
    assert (await _tick(env))["timed_out"] == 2
    assert (await _tick(env))["timed_out"] == 0
    assert set((await _ids_of(env, *sids)).values()) == {"ended"}

    # The same bound holds for the idle and the purge step.
    idle = [await _start(env, 70 + i) for i in range(3)]
    for sid in idle:
        await _set(env, sid, visitor_last_seen=NOW - timedelta(seconds=500))
    assert (await _tick(env))["abandoned"] == 2
    assert (await _tick(env))["abandoned"] == 1
    # 7 ended/abandoned rows, all old enough: purged 2 per tick.
    for sid in sids + idle:
        await _set(env, sid, ended_at=NOW - timedelta(days=60))
    assert [(await _tick(env))["purged"] for _ in range(4)] == [2, 2, 2, 1]


@pytest.mark.asyncio
@parametrize_db
async def test_config_loaded_once_per_config_and_tick(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sids = [await _start(env, 80 + i) for i in range(3)]
    sids.append(await _start(env, 83, config_id=CONFIG_OFF, key=KEY_OFF))
    for sid in sids:
        await _set(env, sid, waiting_since=NOW - timedelta(seconds=400))
        await _seen(env, sid)
    calls: list[int] = []
    real = sweep_module.load_config

    async def counting(db: AsyncSession, config_id: int) -> Any:
        calls.append(config_id)
        return await real(db, config_id)

    monkeypatch.setattr(sweep_module, "load_config", counting)
    assert (await _tick(env))["timed_out"] == 4
    # timeout step and purge step share the per-tick cache: one load per config id.
    assert sorted(calls) == [CONFIG, CONFIG_OFF]


@pytest.mark.asyncio
@parametrize_db
async def test_deleted_config_uses_defaults(env: Env) -> None:
    gone = await _start(env, 90)
    fresh = await _start(env, 91)
    await _set(env, gone, waiting_since=NOW - timedelta(seconds=181))
    await _set(env, fresh, waiting_since=NOW - timedelta(seconds=179))
    old = await _start(env, 92)
    young = await _start(env, 93)
    await _set(env, old, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=31))
    await _set(env, young, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=29))
    for sid in (gone, fresh):
        await _seen(env, sid)
    # Delete the config row after the sessions exist (no FK).
    async with env.factory() as db:
        await db.execute(text("DELETE FROM tiqora_chat_config WHERE id = :c"), {"c": CONFIG})
        await db.commit()
    # Defaults: wait_timeout 180 s, retention 30 days.
    assert await _tick(env) == {"timed_out": 1, "abandoned": 0, "purged": 1}
    assert (await _row(env, gone))["status"] == "ended"
    assert (await _row(env, fresh))["status"] == "waiting"
    assert (await _row(env, old))["purged_at"] == NOW
    assert (await _row(env, young))["purged_at"] is None


@pytest.mark.asyncio
@parametrize_db
async def test_redis_failure_does_not_crash_tick(env: Env) -> None:
    gone = await _start(env, 100)
    timed = await _start(env, 101)
    live = await _start(env, 102)
    await _set(env, gone, visitor_last_seen=NOW - timedelta(seconds=500))
    await _set(env, timed, waiting_since=NOW - timedelta(seconds=400))
    await _set(env, live, status="active", assigned_user_id=AGENT, accepted_at=NOW)
    await _set(env, live, visitor_last_seen=NOW - timedelta(seconds=60))
    env.redis.fail = True
    # Marker lookups and publishing fail; the database decides, the writes still commit.
    res = await _tick(env)
    assert res == {"timed_out": 1, "abandoned": 1, "purged": 0}
    statuses = await _ids_of(env, gone, timed, live)
    assert statuses == {gone: "abandoned", timed: "ended", live: "active"}
    assert (await _messages(env, timed))[-1][1] == "no_agent"
    assert (await _messages(env, gone))[-1][1] == "idle_closed"
    env.redis.fail = False
    assert (await _tick(env)) == {"timed_out": 0, "abandoned": 0, "purged": 0}


@pytest.mark.asyncio
@parametrize_db
async def test_idle_and_purge_recheck_skip_changed_rows(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch select is stale: the visitor came back / the chat was never ended."""
    back = await _start(env, 110)  # waiting, visitor seen just now
    live = await _start(env, 111)  # still waiting: not final, no ended_at
    await _set(env, live, status="active", assigned_user_id=AGENT, accepted_at=NOW)

    async def stale_ids(db: AsyncSession, *conditions: Any, limit: int) -> list[str]:
        return [live]

    monkeypatch.setattr(sweep_module, "_ids", stale_ids)
    # Purge re-check: an active chat must never be purged even if a stale batch names it.
    # (_purge_expired only runs for configs with final rows; give it one.)
    done = await _start(env, 112)
    await _set(env, done, status="ended", end_reason="visitor", ended_at=NOW - timedelta(days=90))
    assert (await _tick(env))["purged"] == 0
    row = await _row(env, live)
    assert row["status"] == "active" and row["purged_at"] is None
    assert await _messages(env, live)

    # Idle re-check: patch the candidate select via a visitor that returned after the select.
    real = sweep_module._in_transaction
    seen_ids: list[str] = []

    async def late_touch(factory: Any, redis_client: Any, session_id: str, work: Any) -> bool:
        seen_ids.append(session_id)
        await _set(env, session_id, visitor_last_seen=NOW)  # visitor returns before the lock
        return await real(factory, redis_client, session_id, work)

    monkeypatch.setattr(sweep_module, "_in_transaction", late_touch)
    await _set(env, back, visitor_last_seen=NOW - timedelta(seconds=500))
    assert (await _tick(env))["abandoned"] == 0
    assert back in seen_ids
    assert (await _row(env, back))["status"] == "waiting"


@pytest.mark.asyncio
@parametrize_db
async def test_worker_tick_reads_switch_and_runs(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await _start(env, 120)
    await _set(env, sid, waiting_since=NOW - timedelta(seconds=400))
    await _seen(env, sid)
    monkeypatch.setattr(worker_module, "get_session_factory", lambda: env.factory)
    monkeypatch.setattr(worker_module, "get_pubsub_redis", lambda settings=None: env.redis)
    real = worker_module.run_webchat_sweep_tick

    async def at_now(**kwargs: Any) -> dict[str, int]:
        return await real(now=NOW, **kwargs)

    monkeypatch.setattr(worker_module, "run_webchat_sweep_tick", at_now)
    settings = Settings(environment="test")

    async def set_switch(value: str | None) -> None:
        async with env.factory() as db:
            await db.execute(delete(TiqoraSettings).where(TiqoraSettings.key == SWITCH))
            if value is not None:
                await db.execute(insert(TiqoraSettings).values(key=SWITCH, value=value))
            await db.commit()

    try:
        await set_switch("0")
        assert await worker_module.run_webchat_worker_tick(settings) == {"skipped": 1}
        assert (await _row(env, sid))["status"] == "waiting"
        await set_switch("1")
        res = await worker_module.run_webchat_worker_tick(settings)
        assert res["timed_out"] == 1
        assert (await _row(env, sid))["status"] == "ended"
    finally:
        await set_switch(None)


@pytest.mark.asyncio
async def test_switch_off_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Ctx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def disabled(session: object, key: str, default: bool) -> bool:
        seen.append(key)
        assert default is True
        return False

    async def must_not_run(**kwargs: object) -> dict[str, int]:
        raise AssertionError("tick ran although the service is disabled")

    monkeypatch.setattr(worker_module, "get_session_factory", lambda: lambda: _Ctx())
    monkeypatch.setattr(worker_module, "get_setting_bool", disabled)
    monkeypatch.setattr(worker_module, "run_webchat_sweep_tick", must_not_run)
    assert await worker_module.run_webchat_worker_tick(Settings(environment="test")) == {
        "skipped": 1
    }
    assert seen == ["daemon.webchat_sweep.enabled"]
