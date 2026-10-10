"""ChatHub: one pattern subscription, routing, caps, eviction, lifetime, lazy dependency."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests._fake_redis import FakeRedis
from tiqora.config import Settings
from tiqora.webchat.hub import (
    CLOSE_ITEM,
    ChatHub,
    StreamLimitReached,
    effective_max_streams,
    get_chat_hub,
    publish_chat_typing,
    publish_chat_wake,
)

A, B, C = "a" * 32, "b" * 32, "c" * 32


async def test_hub_routes_by_session_with_one_subscription() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10)
    qa = await hub.subscribe(A)
    qb = await hub.subscribe(B)
    await publish_chat_wake(r, A)
    assert (await asyncio.wait_for(qa.get(), 2)) == {"type": "wake"}
    await asyncio.sleep(0.05)
    assert qb.empty()
    assert r.pattern_subscription_count() == 1
    hub.unsubscribe(A, qa)
    assert hub.active_streams == 1
    await hub.aclose()


async def test_total_limit() -> None:
    hub = ChatHub(FakeRedis(), max_streams=2)
    await hub.subscribe(A)
    await hub.subscribe(B)
    with pytest.raises(StreamLimitReached) as e:
        await hub.subscribe(C)
    assert e.value.reason == "total"
    assert hub.active_streams == 2
    await hub.aclose()


async def test_per_session_cap_evicts_oldest() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10, max_per_session=3)
    queues = [await hub.subscribe(A) for _ in range(3)]
    fourth = await hub.subscribe(A)
    assert hub.active_streams == 3
    assert queues[0].get_nowait() == CLOSE_ITEM  # oldest got the close item
    assert queues[1].empty() and queues[2].empty()
    await publish_chat_wake(r, A)
    assert (await asyncio.wait_for(fourth.get(), 2)) == {"type": "wake"}
    assert (await asyncio.wait_for(queues[1].get(), 2)) == {"type": "wake"}
    await asyncio.sleep(0.05)
    assert queues[0].empty()  # evicted stream no longer receives
    await hub.aclose()


async def test_eviction_allowed_at_total_cap() -> None:
    hub = ChatHub(FakeRedis(), max_streams=1, max_per_session=1)
    first = await hub.subscribe(A)
    await hub.subscribe(A)
    assert first.get_nowait() == CLOSE_ITEM
    assert hub.active_streams == 1
    await hub.aclose()


async def test_per_ip_cap_six() -> None:
    hub = ChatHub(FakeRedis(), max_streams=100)
    queues = [await hub.subscribe(f"{i:032d}", ip_hash="i" * 32) for i in range(6)]
    with pytest.raises(StreamLimitReached) as e:
        await hub.subscribe("9" * 32, ip_hash="i" * 32)
    assert e.value.reason == "ip"
    await hub.subscribe("9" * 32, ip_hash="j" * 32)  # another IP is fine
    hub.unsubscribe(f"{0:032d}", queues[0])
    await hub.subscribe("8" * 32, ip_hash="i" * 32)  # slot freed
    await hub.aclose()


async def test_typing_is_coalesced() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10)
    q = await hub.subscribe(A)
    for _ in range(60):
        await publish_chat_typing(r, A)
    await asyncio.sleep(0.05)
    assert q.qsize() == 1
    assert q.get_nowait() == {"type": "typing", "who": "agent"}
    await hub.aclose()


async def test_typing_never_pushes_out_a_wake() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10)
    q = await hub.subscribe(A)
    await publish_chat_wake(r, A)
    for _ in range(60):
        await publish_chat_typing(r, A)
    await publish_chat_wake(r, A)
    await asyncio.sleep(0.05)
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert [i["type"] for i in items].count("wake") == 2
    await hub.aclose()


async def test_wake_overflow_closes_stream_instead_of_dropping() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10)
    q = await hub.subscribe(A)
    for _ in range(51):
        await publish_chat_wake(r, A)
    await asyncio.sleep(0.1)
    assert q.qsize() == 1 and q.get_nowait() == CLOSE_ITEM
    assert hub.active_streams == 0
    await hub.aclose()


async def test_full_queue_of_wakes_with_typing_drops_typing_first() -> None:
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10)
    q = await hub.subscribe(A)
    await publish_chat_typing(r, A)
    for _ in range(50):
        await publish_chat_wake(r, A)
    await asyncio.sleep(0.1)
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert len(items) == 50 and all(i["type"] == "wake" for i in items)
    assert hub.active_streams == 1
    await hub.aclose()


async def test_subscribe_after_aclose_refused() -> None:
    hub = ChatHub(FakeRedis(), max_streams=10)
    await hub.aclose()
    with pytest.raises(StreamLimitReached):
        await hub.subscribe(A)


async def test_lifetime_enforced_during_redis_outage() -> None:
    now = [0.0]
    r = FakeRedis()
    hub = ChatHub(r, max_streams=10, max_lifetime=1800, clock=lambda: now[0])
    q = await hub.subscribe(A)
    r.fail = True
    now[0] = 1801.0
    assert (await asyncio.wait_for(q.get(), 3)) == CLOSE_ITEM
    assert hub.active_streams == 0
    await hub.aclose()


async def test_max_lifetime_closes_stream() -> None:
    now = [0.0]
    hub = ChatHub(FakeRedis(), max_streams=10, max_lifetime=1800, clock=lambda: now[0])
    q = await hub.subscribe(A)
    now[0] = 1801.0
    assert (await asyncio.wait_for(q.get(), 3)) == CLOSE_ITEM
    assert hub.active_streams == 0
    await hub.aclose()


async def test_reader_survives_redis_error_and_resubscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = FakeRedis()
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fast_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0.01)

    monkeypatch.setattr("tiqora.webchat.hub.asyncio.sleep", fast_sleep)
    hub = ChatHub(r, max_streams=10)
    q = await hub.subscribe(A)
    r.fail = True
    await real_sleep(0.1)
    r.fail = False
    await real_sleep(0.1)
    await publish_chat_wake(r, A)
    assert (await asyncio.wait_for(q.get(), 3)) == {"type": "wake"}
    assert 1.0 in sleeps
    await hub.aclose()


async def test_publish_helpers_never_raise() -> None:
    r = FakeRedis()
    r.fail = True
    await publish_chat_wake(r, A)
    await publish_chat_typing(r, A)
    assert r.published == []


def test_effective_max_streams_clamps_to_fd_limit() -> None:
    assert effective_max_streams(500, soft_nofile=1024) == 500
    assert effective_max_streams(500, soft_nofile=600) == 344
    assert effective_max_streams(500, soft_nofile=100) == 10


async def test_get_chat_hub_is_lazy_and_cached() -> None:
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(chat_hub=None))
    request: Any = SimpleNamespace(app=app)
    r = FakeRedis()
    settings = Settings(environment="test")
    hub = await get_chat_hub(request, r, settings)  # type: ignore[arg-type]
    assert app.state.chat_hub is hub
    assert await get_chat_hub(request, r, settings) is hub  # type: ignore[arg-type]
    assert hub._max_streams == effective_max_streams(settings.chat_max_streams)
    await hub.aclose()
