"""Visitor SSE stream (C7) against a real DB on both dialects (band 48600-48619).

The generator is driven directly (``ASGITransport`` buffers a streaming body until it ends);
the router tests only use answers that finish: refusals and already-ended sessions.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text, update
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
from tiqora.api.deps import get_redis
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.webchat import stream as stream_module
from tiqora.webchat.availability import VISITOR_SEEN_PREFIX, mark_agent_online, set_agent_state
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.hub import ChatHub, get_chat_hub, publish_chat_typing, publish_chat_wake
from tiqora.webchat.store import get_chat_session, insert_message
from tiqora.webchat.stream import (
    HEARTBEAT_CHUNK,
    STREAM_HEARTBEAT_SECONDS,
    parse_last_event_id,
    sse_event,
    visitor_event_stream,
    visitor_stream_response,
)
from tiqora.webchat.tokens import hash_ip
from tiqora.znuny.sysconfig import SysConfig

GROUP, CONFIG, AGENT, QUEUE = 48600, 48601, 48602, 48603
CONFIG_IDS = range(48600, 48620)
KEY = "wc_" + "c8" * 16
ORIGIN = "https://www.example.org"
API = "/api/chat/v1"
SELF = "http://testserver"
VISITOR = "visitor_wc48_" + "s" * 10
OTHER_VISITOR = "visitor_wc48_" + "t" * 10
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CREATE: dict[str, Any] = {
    "key": KEY,
    "origin": ORIGIN,
    "visitor_id": VISITOR,
    "message": "Mein Router blinkt rot",
    "client_id": "c-1",
}


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


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis
    hubs: list[ChatHub] = field(default_factory=list)

    def sql(self, statement: str, **params: Any) -> list[Any]:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()

    def hub(self, **kwargs: Any) -> ChatHub:
        hub = ChatHub(self.redis, **{"max_streams": 100, **kwargs})
        self.hubs.append(hub)
        return hub


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
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-sse")
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        enable_webchat(conn)
    engine.dispose()
    clear_config_cache()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeRedis()
    async with factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(fake, AGENT)
    current = Env(url, factory, fake)
    try:
        yield current
    finally:
        for hub in current.hubs:
            await hub.aclose()
        clear_config_cache()
        await aengine.dispose()
        delete_above_marks(url, marks, first=mark_cleanup_first(marks))
        _cleanup(url)


parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


class _FakeRequest:
    """The generator only calls ``is_disconnected()``."""

    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def _client(env: Env, hub: ChatHub | None = None) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    if hub is not None:
        app.dependency_overrides[get_chat_hub] = lambda: hub
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


async def _start(env: Env, **overrides: Any) -> dict[str, Any]:
    """A chat through the public API (session row, ticket, first visitor line = seq 1)."""
    async with _client(env) as client:
        res = await client.post(f"{API}/sessions", json={**CREATE, **overrides})
    assert res.status_code == 201, res.text
    return dict(res.json())


async def _add_lines(env: Env, sid: str, *bodies: str) -> None:
    async with env.factory() as db:
        chat = await get_chat_session(db, sid)
        assert chat is not None
        for body in bodies:
            await insert_message(
                db, chat=chat, sender="agent", body=body, meta={"agent_name": "Max"}, now=NOW
            )
        await db.commit()


def _stream(
    env: Env, hub: ChatHub, sid: str, after: int, req: _FakeRequest | None = None, **kw: Any
) -> AsyncGenerator[bytes, None]:
    return visitor_event_stream(
        cast(Any, req or _FakeRequest()),
        hub=hub,
        session_factory=env.factory,
        redis_client=env.redis,
        session_id=sid,
        after_seq=after,
        **kw,
    )


async def _next(gen: AsyncGenerator[bytes, None], timeout: float = 5.0) -> bytes:  # noqa: ASYNC109
    return await asyncio.wait_for(anext(gen), timeout=timeout)


def _data(chunk: bytes) -> dict[str, Any]:
    line = next(x for x in chunk.decode().split("\n") if x.startswith("data: "))
    return dict(json.loads(line[len("data: ") :]))


# -- pure helpers ----------------------------------------------------------------------------------


def test_sse_event_and_last_event_id_parsing() -> None:
    assert sse_event(event="message", data={"a": 1}, event_id=3) == (
        b'id: 3\nevent: message\ndata: {"a": 1}\n\n'
    )
    assert sse_event(event="end", data={"reason": "x\ny"}) == (
        b'event: end\ndata: {"reason": "x\\ny"}\n\n'
    )
    assert STREAM_HEARTBEAT_SECONDS == 25
    assert parse_last_event_id("12") == 12
    assert parse_last_event_id(" 0 ") == 0
    for bad in (None, "", "-1", "abc", "1.5", "²", "9" * 19):
        assert parse_last_event_id(bad) is None


# -- replay and live events ------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_replay_after_last_event_id(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    await _add_lines(env, sid, "zwei", "drei", "vier")
    gen = _stream(env, env.hub(), sid, after=2)
    try:
        chunks = [await _next(gen) for _ in range(3)]
    finally:
        await gen.aclose()
    assert chunks[0].startswith(b"id: 3\nevent: message\n")
    assert chunks[1].startswith(b"id: 4\nevent: message\n")
    assert chunks[2].startswith(b"event: session\n") and b'"status": "waiting"' in chunks[2]
    line = _data(chunks[0])
    assert line["seq"] == 3 and line["body"] == "drei" and line["sender"] == "agent"
    assert line["agent_name"] == "Max"
    assert not {"ticket_id", "article_id", "user_id", "tn", "queue_id"} & set(line)
    assert _data(chunks[2]) == {"status": "waiting", "agent_name": None}


@pytest.mark.asyncio
@parametrize_db
async def test_replay_pages_beyond_one_read(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    await _add_lines(env, sid, *[f"z{i}" for i in range(205)])
    gen = _stream(env, env.hub(), sid, after=0)
    try:
        chunks = [await _next(gen) for _ in range(207)]
    finally:
        await gen.aclose()
    seqs = [_data(c)["seq"] for c in chunks[:206]]
    assert seqs == list(range(1, 207))
    assert chunks[206].startswith(b"event: session\n")


@pytest.mark.asyncio
@parametrize_db
async def test_live_message_after_wake(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    await _add_lines(env, sid, "zwei", "drei", "vier")
    gen = _stream(env, env.hub(), sid, after=4)
    try:
        assert (await _next(gen)).startswith(b"event: session\n")
        await _add_lines(env, sid, "fuenf")
        await publish_chat_wake(env.redis, sid)
        chunk = await _next(gen, timeout=2)
    finally:
        await gen.aclose()
    assert chunk.startswith(b"id: 5\nevent: message\n")
    assert _data(chunk)["body"] == "fuenf"


@pytest.mark.asyncio
@parametrize_db
async def test_wake_published_before_commit_is_still_delivered(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    await _add_lines(env, sid, "zwei", "drei", "vier", "fuenf")
    hub = env.hub()
    queue = await hub.subscribe(sid)
    gen = _stream(env, hub, sid, after=5, heartbeat_seconds=0.2, queue=queue)
    pending: asyncio.Future[bytes] | None = None
    try:
        assert (await _next(gen)).startswith(b"event: session\n")
        pending = asyncio.ensure_future(anext(gen))
        # The wake arrives first: the stream consumes it and its re-read finds nothing.
        await publish_chat_wake(env.redis, sid)
        loop = asyncio.get_running_loop()
        give_up = loop.time() + 2.0
        while not queue.empty() or env.redis.published[-1][1] != '{"type": "wake"}':
            assert loop.time() < give_up, "wake never consumed"
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # let that re-read finish before the commit
        assert not pending.done()
        await _add_lines(env, sid, "sechs")  # the commit after the wake
        deadline = loop.time() + 1.0
        seen = [await asyncio.wait_for(pending, timeout=1.0)]
        pending = None
        while not seen[-1].startswith(b"id: 6\n"):
            seen.append(await _next(gen, timeout=max(0.01, deadline - loop.time())))
    finally:
        if pending is not None:
            pending.cancel()
        await gen.aclose()
    assert seen[-1].startswith(b"id: 6\nevent: message\n")
    assert all(c == HEARTBEAT_CHUNK for c in seen[:-1])
    assert HEARTBEAT_CHUNK in seen  # only the heartbeat re-read could find seq 6


@pytest.mark.asyncio
@parametrize_db
async def test_typing_passes_through_without_id(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    gen = _stream(env, env.hub(), sid, after=1)
    try:
        assert (await _next(gen)).startswith(b"event: session\n")
        await publish_chat_typing(env.redis, sid)
        chunk = await _next(gen, timeout=2)
    finally:
        await gen.aclose()
    assert chunk == b'event: typing\ndata: {"who": "agent"}\n\n'


@pytest.mark.asyncio
@parametrize_db
async def test_session_event_on_accept_and_end_closes(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    hub = env.hub()
    gen = _stream(env, hub, sid, after=1)
    try:
        assert _data(await _next(gen)) == {"status": "waiting", "agent_name": None}
        env.sql("UPDATE users SET first_name = 'Max' WHERE id = :u", u=AGENT)
        env.sql(
            "UPDATE tiqora_chat_session SET status = 'active', assigned_user_id = :u,"
            " accepted_at = :t WHERE id = :s",
            u=AGENT,
            t=NOW,
            s=sid,
        )
        await publish_chat_wake(env.redis, sid)
        accepted = await _next(gen, timeout=2)
        assert accepted.startswith(b"event: session\n")
        assert _data(accepted) == {"status": "active", "agent_name": "Max"}

        env.sql(
            "UPDATE tiqora_chat_session SET status = 'ended', end_reason = 'agent',"
            " ended_at = :t WHERE id = :s",
            t=NOW,
            s=sid,
        )
        await publish_chat_wake(env.redis, sid)
        assert await _next(gen, timeout=2) == b'event: end\ndata: {"reason": "agent"}\n\n'
        with pytest.raises(StopAsyncIteration):
            await _next(gen, timeout=2)
    finally:
        await gen.aclose()
    assert hub.active_streams == 0


@pytest.mark.asyncio
@parametrize_db
async def test_ended_session_replays_then_ends_at_connect(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    env.sql(
        "UPDATE tiqora_chat_session SET status = 'abandoned', end_reason = 'idle',"
        " ended_at = :t WHERE id = :s",
        t=NOW,
        s=sid,
    )
    gen = _stream(env, env.hub(), sid, after=0)
    try:
        first = await _next(gen)
        end = await _next(gen)
        with pytest.raises(StopAsyncIteration):
            await _next(gen)
    finally:
        await gen.aclose()
    assert first.startswith(b"id: 1\nevent: message\n")
    assert end == b'event: end\ndata: {"reason": "idle"}\n\n'


@pytest.mark.asyncio
@parametrize_db
async def test_internal_and_foreign_rows_never_emitted(env: Env) -> None:
    mine = await _start(env)
    other = await _start(env, visitor_id=OTHER_VISITOR, message="Andere Frage", client_id="c-9")
    sid, other_sid = mine["session_id"], other["session_id"]
    other_ticket = env.sql("SELECT ticket_id FROM tiqora_chat_session WHERE id = :s", s=other_sid)
    env.sql(
        "INSERT INTO tiqora_chat_message (session_id, ticket_id, seq, sender, body, created)"
        " VALUES (:s, :t, 7, 'agent', 'foreign-wc48', :c)",
        s=other_sid,
        t=int(other_ticket[0][0]),
        c=NOW,
    )
    ticket_id = int(env.sql("SELECT ticket_id FROM tiqora_chat_session WHERE id = :s", s=sid)[0][0])
    async with env.factory() as db:
        await add_article(
            db,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="Intern",
                body="internal-wc48",
                channel="internal",
            ),
            user_id=1,
            sysconfig=SysConfig(db),
        )
        await db.commit()
    gen = _stream(env, env.hub(), sid, after=0)
    chunks: list[bytes] = []
    try:
        chunks.append(await _next(gen))
        chunks.append(await _next(gen))
        await publish_chat_wake(env.redis, other_sid)
        await publish_chat_wake(env.redis, sid)
        await _add_lines(env, sid, "zwei")
        await publish_chat_wake(env.redis, sid)
        chunks.append(await _next(gen, timeout=2))
    finally:
        await gen.aclose()
    assert chunks[0].startswith(b"id: 1\n") and chunks[1].startswith(b"event: session\n")
    assert chunks[2].startswith(b"id: 2\nevent: message\n")
    joined = b"".join(chunks)
    assert b"foreign-wc48" not in joined and b"id: 7\n" not in joined
    assert b"internal-wc48" not in joined and b"Andere Frage" not in joined


@pytest.mark.asyncio
@parametrize_db
async def test_stream_marks_visitor_seen(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    old = NOW - timedelta(hours=1)
    env.sql("UPDATE tiqora_chat_session SET visitor_last_seen = :t WHERE id = :s", t=old, s=sid)
    key = f"{VISITOR_SEEN_PREFIX}{sid}"
    assert await env.redis.get(key) is None
    gen = _stream(env, env.hub(), sid, after=1, heartbeat_seconds=0.2)
    pending: asyncio.Task[bytes] | None = None
    try:
        assert (await _next(gen)).startswith(b"event: session\n")
        assert await env.redis.get(key) == "1"
        assert 0 < await env.redis.ttl(key) <= 90
        seen_at = env.sql("SELECT visitor_last_seen FROM tiqora_chat_session WHERE id = :s", s=sid)
        assert seen_at[0][0] > old  # R-LASTSEEN on connect
        # Every heartbeat refreshes the marker.
        await env.redis.delete(key)
        assert await _next(gen, timeout=2) == HEARTBEAT_CHUNK
        pending = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(0.1)
        assert await env.redis.get(key) == "1"
    finally:
        if pending is not None:
            pending.cancel()
            with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
                await pending
        await gen.aclose()


@pytest.mark.asyncio
@parametrize_db
async def test_heartbeat_counts_from_last_bytes_sent(env: Env) -> None:
    """Fake clock: a byte-less wake right before the deadline never defers the heartbeat."""
    created = await _start(env)
    sid = created["session_id"]
    hub = env.hub()
    queue = await hub.subscribe(sid)
    now = [0.0]
    gen = _stream(env, hub, sid, after=1, queue=queue, clock=lambda: now[0])
    key = f"{VISITOR_SEEN_PREFIX}{sid}"
    pending: asyncio.Future[bytes] | None = None
    try:
        assert (await _next(gen)).startswith(b"event: session\n")  # bytes at t=0
        pending = asyncio.ensure_future(anext(gen))
        # A wake whose re-read finds nothing, just before the deadline: no bytes sent.
        now[0] = 24.9
        queue.put_nowait({"type": "wake"})
        await asyncio.sleep(0.05)
        assert not pending.done()
        now[0] = 25.0  # 25 s after the last bytes: the heartbeat is due now, not at ~50 s
        assert await asyncio.wait_for(pending, timeout=1) == HEARTBEAT_CHUNK

        # The re-read after that heartbeat refreshed the markers (t=25); the next deadline
        # is t=50 — again not deferred by a byte-less wake just before it.
        await env.redis.delete(key)
        pending = asyncio.ensure_future(anext(gen))
        loop = asyncio.get_running_loop()
        give_up = loop.time() + 2.0
        while await env.redis.get(key) is None:
            assert loop.time() < give_up, "seen marker not refreshed"
            await asyncio.sleep(0.01)
        now[0] = 49.9
        queue.put_nowait({"type": "wake"})
        await asyncio.sleep(0.05)
        assert not pending.done()
        now[0] = 50.0
        assert await asyncio.wait_for(pending, timeout=1) == HEARTBEAT_CHUNK
        # Typing is forwarded as soon as it arrives, with no heartbeat in between.
        queue.put_nowait({"type": "typing", "who": "agent"})
        assert await _next(gen, timeout=1) == b'event: typing\ndata: {"who": "agent"}\n\n'
        # A wake that lands exactly on the deadline (bytes last sent at t=50) still yields
        # the heartbeat at once instead of restarting a full 25 s wait.
        pending = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(0.05)
        now[0] = 75.0
        queue.put_nowait({"type": "wake"})
        assert await asyncio.wait_for(pending, timeout=1) == HEARTBEAT_CHUNK
        pending = None
    finally:
        if pending is not None:
            pending.cancel()
        await gen.aclose()


@pytest.mark.asyncio
@parametrize_db
async def test_wake_burst_reads_once(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    created = await _start(env)
    sid = created["session_id"]
    reads: list[int] = []
    original = stream_module._read

    async def counting_read(*args: Any, **kwargs: Any) -> Any:
        reads.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(stream_module, "_read", counting_read)
    hub = env.hub()
    queue = await hub.subscribe(sid)
    gen = _stream(env, hub, sid, after=1, queue=queue)
    try:
        assert (await _next(gen)).startswith(b"event: session\n")
        await _add_lines(env, sid, "zwei")
        for _ in range(5):
            queue.put_nowait({"type": "wake"})
        queue.put_nowait({"type": "typing", "who": "agent"})
        assert (await _next(gen)).startswith(b"event: typing\n")
        assert (await _next(gen)).startswith(b"id: 2\nevent: message\n")
    finally:
        await gen.aclose()
    assert len(reads) == 2  # connect + ONE re-read for the whole burst


# -- stream lifetime and releasing the hub slot ----------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_disconnect_and_shutdown_release_the_subscription(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    hub = env.hub(max_per_ip=1)

    # Client disconnect: the generator ends and the per-IP slot is free again.
    req = _FakeRequest()
    gen = _stream(env, hub, sid, after=1, req=req, ip_hash="ip-wc48")
    assert (await _next(gen)).startswith(b"event: session\n")
    assert hub.active_streams == 1
    req.disconnected = True
    await publish_chat_wake(env.redis, sid)
    with pytest.raises(StopAsyncIteration):
        await _next(gen, timeout=2)
    assert hub.active_streams == 0

    # Closing the generator (task cancelled by the server) releases it too.
    gen = _stream(env, hub, sid, after=1, ip_hash="ip-wc48")
    assert (await _next(gen)).startswith(b"event: session\n")
    await gen.aclose()
    assert hub.active_streams == 0

    # Hub shutdown: the waiting stream ends without an ``end`` event.
    gen = _stream(env, hub, sid, after=1, ip_hash="ip-wc48")
    assert (await _next(gen)).startswith(b"event: session\n")
    pending = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0.05)
    await hub.aclose()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, timeout=2)
    assert hub.active_streams == 0


@pytest.mark.asyncio
@parametrize_db
async def test_response_releases_a_body_that_never_started(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    hub = env.hub(max_per_ip=1)
    queue = await hub.subscribe(sid, ip_hash="ip-wc48")
    gen = _stream(env, hub, sid, after=1, queue=queue)
    response = visitor_stream_response(gen, release=lambda: hub.unsubscribe(sid, queue))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.media_type == "text/event-stream"

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        raise OSError("client gone")

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}}
    with pytest.raises(Exception):  # noqa: B017 - starlette maps OSError to ClientDisconnect
        await response(cast(Any, scope), cast(Any, receive), cast(Any, send))
    assert hub.active_streams == 0
    assert gen.ag_frame is None  # closed, never left running
    await hub.subscribe(sid, ip_hash="ip-wc48")  # the per-IP slot is free again


@pytest.mark.asyncio
@parametrize_db
async def test_evicted_stream_ends_without_end_event(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    hub = env.hub(max_per_session=1)
    first = _stream(env, hub, sid, after=1)
    second = _stream(env, hub, sid, after=1)
    try:
        assert (await _next(first)).startswith(b"event: session\n")
        assert (await _next(second)).startswith(b"event: session\n")
        with pytest.raises(StopAsyncIteration):
            await _next(first, timeout=2)  # reconnect, not "end"
        assert hub.active_streams == 1
    finally:
        await first.aclose()
        await second.aclose()
    assert hub.active_streams == 0


# -- endpoint ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_stream_endpoint_limits(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    url = f"{API}/sessions/{sid}/stream"
    token = {"X-Chat-Token": created["token"]}

    full = env.hub(max_streams=1)
    await full.subscribe("f" * 32)
    async with _client(env, full) as client:
        wrong = await client.get(url, headers={"X-Chat-Token": f"{sid}.wrong"})
        assert wrong.status_code == 404 and wrong.json() == {"detail": "not_found"}
        busy = await client.get(url, headers=token)
    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "30"
    assert busy.json() == {"detail": "chat_unavailable"}
    assert full.active_streams == 1

    per_ip = env.hub(max_per_ip=1)
    secret = Settings(environment="test").secret_key
    await per_ip.subscribe("e" * 32, ip_hash=hash_ip("127.0.0.1", secret))
    async with _client(env, per_ip) as client:
        busy_ip = await client.get(url, headers=token)
    assert busy_ip.status_code == 503 and busy_ip.headers["retry-after"] == "30"

    await env.redis.set(f"tiqora:chat:rl:stream_connect:{sid}", "30", ex=60)
    async with _client(env, env.hub()) as client:
        limited = await client.get(url, headers=token)
    assert limited.status_code == 429 and limited.json() == {"detail": "rate_limited"}


@pytest.mark.asyncio
@parametrize_db
async def test_stream_endpoint_ended_session_replays_and_closes(env: Env) -> None:
    created = await _start(env)
    sid = created["session_id"]
    await _add_lines(env, sid, "zwei")
    env.sql(
        "UPDATE tiqora_chat_session SET status = 'ended', end_reason = 'agent',"
        " ended_at = :t WHERE id = :s",
        t=NOW,
        s=sid,
    )
    # R-DISABLED-SESSIONS: the stream is a read and keeps working with the channel off.
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(
            update(TiqoraSettings)
            .where(TiqoraSettings.key == "channel.webchat.enabled")
            .values(value="0")
        )
    engine.dispose()
    clear_config_cache()
    url = f"{API}/sessions/{sid}/stream"
    token = {"X-Chat-Token": created["token"]}
    hub = env.hub()
    async with _client(env, hub) as client:
        res = await client.get(url, headers=token, params={"after": 0})
        resumed = await client.get(
            url, headers={**token, "Last-Event-ID": "1"}, params={"after": 0}
        )
        garbled = await client.get(
            url, headers={**token, "Last-Event-ID": "x"}, params={"after": 1}
        )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-accel-buffering"] == "no"
    assert res.content.startswith(b"id: 1\nevent: message\n")
    assert b"id: 2\nevent: message\n" in res.content
    assert res.content.endswith(b'event: end\ndata: {"reason": "agent"}\n\n')
    # Last-Event-ID wins over ``after``; a garbled one falls back to ``after``.
    assert resumed.content.startswith(b"id: 2\n") and b"id: 1\n" not in resumed.content
    assert garbled.content.startswith(b"id: 2\n") and b"id: 1\n" not in garbled.content
    assert hub.active_streams == 0
