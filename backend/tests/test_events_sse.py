"""Tests for the SSE realtime stream and agent presence endpoints.

No Docker/testcontainers needed: everything here runs against an in-memory
fake Redis (``_FakeRedis`` below) that supports just enough of the
``redis.asyncio`` surface (``set``/``get``/``scan_iter``/``publish``/
``pubsub()``) for these endpoints, following the same no-op fake-Redis
pattern as ``test_admin_api.py``'s ``_FakeRedis`` — this repo doesn't
depend on ``fakeredis``, and the SSE endpoint additionally needs pub/sub
support that a plain no-op stand-in can't provide.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from typing import Any, cast

import pytest

from tiqora.api.deps import get_current_user, get_redis
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL

_FAKE_USER = AuthenticatedUser(
    id=7,
    login="agent7",
    first_name="Ada",
    last_name="Agent",
    auth_method="session",
)


class _FakePubSub:
    """In-memory pub/sub subscriber backed by an ``asyncio.Queue``."""

    def __init__(self, broker: _FakeRedis) -> None:
        self._broker = broker
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._channels: set[str] = set()

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)
        self._broker._subscribers.setdefault(channel, []).append(self._queue)

    async def unsubscribe(self, channel: str | None = None) -> None:
        channels = [channel] if channel else list(self._channels)
        for ch in channels:
            subs = self._broker._subscribers.get(ch, [])
            if self._queue in subs:
                subs.remove(self._queue)
            self._channels.discard(ch)

    # Mirrors redis.asyncio.PubSub.get_message's real signature (timeout as a
    # plain kwarg, not asyncio.timeout) so this fake is a drop-in stand-in.
    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,  # noqa: ASYNC109
    ) -> dict[str, Any] | None:
        del ignore_subscribe_messages
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout or 0.01)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        await self.unsubscribe()


class _FakeRedis:
    """In-memory Redis stand-in: string KV with TTL bookkeeping + pub/sub."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self.ttls.pop(key, None)

    async def scan_iter(self, match: str | None = None) -> Any:
        for key in list(self._store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))
        for queue in self._subscribers.get(channel, []):
            await queue.put({"type": "message", "channel": channel, "data": message})

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self)


def _build_app(fake_redis: _FakeRedis) -> Any:
    from tiqora.api.app import create_app

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_redis] = lambda: fake_redis
    return app


@pytest.mark.asyncio
async def test_presence_roundtrip_sets_ttl_and_publishes() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    app = _build_app(fake_redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/tickets/42/presence", json={"mode": "viewing"})
        assert resp.status_code == 204

        key = "tiqora:presence:42:7"
        assert key in fake_redis.ttls
        assert fake_redis.ttls[key] == 30

        resp2 = await client.get("/api/v1/tickets/42/presence")
        assert resp2.status_code == 200
        body = resp2.json()
        assert body == [{"user_id": 7, "name": "Ada Agent", "mode": "viewing"}]

    # presence write also publishes a presence_changed marker for SSE subscribers
    assert fake_redis.published
    channel, raw = fake_redis.published[-1]
    assert channel == TIQORA_EVENTS_CHANNEL
    payload = json.loads(raw)
    assert payload == {"type": "presence_changed", "ticket_id": 42}


@pytest.mark.asyncio
async def test_presence_empty_when_no_viewers() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    app = _build_app(fake_redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/tickets/999/presence")
        assert resp.status_code == 200
        assert resp.json() == []


class _FakeRequest:
    """Stands in for ``starlette.Request`` — ``_event_stream`` only calls
    ``is_disconnected()``, so that's all this needs to implement."""

    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_sse_stream_forwards_published_message() -> None:
    """Drives the streaming generator directly rather than through
    httpx's ``ASGITransport``: that transport fully buffers the ASGI
    response and only returns once the body stream ends (see
    ``httpx._transports.asgi.ASGITransport.handle_async_request``), so it
    deadlocks against an intentionally-infinite SSE generator. Calling
    ``_event_stream`` directly and reading a single bounded ``anext()``
    exercises the same subscribe → publish → forward logic without that
    trap.
    """
    from tiqora.api.v1.events import _event_stream

    fake_redis = _FakeRedis()
    generator = _event_stream(cast(Any, _FakeRequest()), cast(Any, fake_redis))

    async def _publish_soon() -> None:
        await asyncio.sleep(0.05)
        await fake_redis.publish(
            TIQORA_EVENTS_CHANNEL,
            json.dumps({"type": "ticket_changed", "ticket_id": 42, "event": "TicketCreate"}),
        )

    publisher = asyncio.create_task(_publish_soon())
    try:
        chunk = await asyncio.wait_for(generator.__anext__(), timeout=5)
    finally:
        publisher.cancel()
        await generator.aclose()

    assert chunk.startswith(b"data:")
    body = json.loads(chunk.split(b"data:", 1)[1].strip())
    assert body == {"type": "ticket_changed", "ticket_id": 42, "event": "TicketCreate"}


@pytest.mark.asyncio
async def test_sse_stream_emits_heartbeat_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.api.v1 import events as events_module

    monkeypatch.setattr(events_module, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    fake_redis = _FakeRedis()
    generator = events_module._event_stream(cast(Any, _FakeRequest()), cast(Any, fake_redis))
    try:
        chunk = await asyncio.wait_for(generator.__anext__(), timeout=5)
    finally:
        await generator.aclose()

    assert chunk == b": heartbeat\n\n"


@pytest.mark.asyncio
async def test_sse_stream_requires_auth() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app

    app = create_app(Settings(environment="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/events/stream")
        assert resp.status_code == 401
