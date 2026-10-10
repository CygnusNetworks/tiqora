"""Chat rate limiter: fixed window, fail closed, self-healing TTL."""

from __future__ import annotations

import pytest

from tests._fake_redis import FakeRedis
from tiqora.webchat.ratelimit import (
    PUBLIC_BACKSTOP_LIMIT,
    ChatBackendUnavailable,
    ChatRateLimiter,
    RateLimited,
    check_public_backstop,
)


async def test_fixed_window_limit_then_retry_after() -> None:
    r = FakeRedis()
    lim = ChatRateLimiter(r)
    for _ in range(3):
        await lim.hit("message", "s1", limit=3, window_seconds=60)
    with pytest.raises(RateLimited) as e:
        await lim.hit("message", "s1", limit=3, window_seconds=60)
    assert e.value.scope == "message" and 1 <= e.value.retry_after <= 60
    await lim.hit("message", "s2", limit=3, window_seconds=60)
    r.advance(61)
    await lim.hit("message", "s1", limit=3, window_seconds=60)


async def test_redis_down_fails_closed() -> None:
    r = FakeRedis()
    r.fail = True
    with pytest.raises(ChatBackendUnavailable):
        await ChatRateLimiter(r).hit("session_create", "ip", limit=5, window_seconds=600)


async def test_non_connection_errors_also_fail_closed() -> None:
    class Broken:
        def pipeline(self, transaction: bool = True) -> object:
            raise RuntimeError("boom")

    with pytest.raises(ChatBackendUnavailable):
        await ChatRateLimiter(Broken()).hit("x", "y", limit=1, window_seconds=1)


async def test_try_hit_once_per_window() -> None:
    r = FakeRedis()
    lim = ChatRateLimiter(r)
    assert await lim.try_hit("typing", "s1", window_seconds=2) is True
    assert await lim.try_hit("typing", "s1", window_seconds=2) is False
    r.advance(3)
    assert await lim.try_hit("typing", "s1", window_seconds=2) is True


async def test_try_hit_redis_error_is_false() -> None:
    r = FakeRedis()
    r.fail = True
    assert await ChatRateLimiter(r).try_hit("typing", "s1", window_seconds=2) is False


async def test_key_without_ttl_is_repaired() -> None:
    r = FakeRedis()
    lim = ChatRateLimiter(r)
    key = "tiqora:chat:rl:message:s1"
    await lim.hit("message", "s1", limit=2, window_seconds=60)
    r.drop_ttl(key)  # crash between INCR and EXPIRE
    assert await r.ttl(key) == -1
    await lim.hit("message", "s1", limit=2, window_seconds=60)
    assert 1 <= await r.ttl(key) <= 60
    with pytest.raises(RateLimited) as e:
        await lim.hit("message", "s1", limit=2, window_seconds=60)
    assert 1 <= e.value.retry_after <= 60
    r.advance(61)
    await lim.hit("message", "s1", limit=2, window_seconds=60)  # not a permanent 429


async def test_public_backstop_300_per_minute() -> None:
    r = FakeRedis()
    lim = ChatRateLimiter(r)
    for _ in range(PUBLIC_BACKSTOP_LIMIT):
        await check_public_backstop(lim, "a" * 32)
    with pytest.raises(RateLimited) as e:
        await check_public_backstop(lim, "a" * 32)
    assert e.value.scope == "public"
    await check_public_backstop(lim, "b" * 32)
    r.fail = True
    with pytest.raises(ChatBackendUnavailable):
        await check_public_backstop(lim, "c" * 32)


@pytest.mark.asyncio
async def test_check_refuses_without_counting_and_heals_missing_ttl() -> None:
    fake = FakeRedis()
    limiter = ChatRateLimiter(fake)
    await limiter.check("auth_fail", "h", limit=2, window_seconds=600)  # empty: passes
    key = "tiqora:chat:rl:auth_fail:h"
    await fake.set(key, "2")  # full and, after a crash, without TTL
    assert await fake.ttl(key) == -1
    with pytest.raises(RateLimited) as exc:
        await limiter.check("auth_fail", "h", limit=2, window_seconds=600)
    assert exc.value.retry_after == 600
    assert await fake.ttl(key) == 600
    assert await fake.get(key) == "2"  # check never counts
    fake.advance(601)
    await limiter.check("auth_fail", "h", limit=2, window_seconds=600)


@pytest.mark.asyncio
async def test_check_fails_closed() -> None:
    fake = FakeRedis()
    fake.fail = True
    with pytest.raises(ChatBackendUnavailable):
        await ChatRateLimiter(fake).check("auth_fail", "h", limit=2, window_seconds=600)
