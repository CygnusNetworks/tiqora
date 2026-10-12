"""SCIM rate limiter: per-IP failed-auth lock, per-token window, key isolation (pure, FakeRedis)."""

from __future__ import annotations

from typing import Any

import pytest

from tests._identity_helpers import FakeRedis
from tiqora.config import Settings
from tiqora.security.ratelimit import ScimRateLimiter


def _settings(**kw: Any) -> Settings:
    return Settings(environment="test", secret_key="unit-test-secret-key-scim", **kw)


class _BrokenRedis:
    async def ttl(self, key: str) -> int:
        raise ConnectionError("redis down")

    incr = expire = set = ttl


@pytest.mark.asyncio
async def test_ip_lockout_after_max_failures() -> None:
    lim = ScimRateLimiter(
        FakeRedis(), _settings(scim_auth_fail_max=3, scim_auth_lockout_seconds=900)
    )
    assert (await lim.check_ip("192.0.2.7")).allowed
    assert await lim.record_auth_failure("192.0.2.7") is None
    assert await lim.record_auth_failure("192.0.2.7") is None
    locked = await lim.record_auth_failure("192.0.2.7")
    assert locked is not None and not locked.allowed and locked.retry_after == 900
    d = await lim.check_ip("192.0.2.7")
    assert not d.allowed and d.retry_after > 0
    assert (await lim.check_ip("192.0.2.8")).allowed


@pytest.mark.asyncio
async def test_token_window() -> None:
    lim = ScimRateLimiter(FakeRedis(), _settings(scim_rate_limit_max=2))
    assert (await lim.check_token(5)).allowed
    assert (await lim.check_token(5)).allowed
    third = await lim.check_token(5)
    assert not third.allowed and third.retry_after >= 1
    assert (await lim.check_token(6)).allowed


@pytest.mark.asyncio
async def test_keys_do_not_collide_with_api_keys() -> None:
    redis = FakeRedis()
    lim = ScimRateLimiter(redis, _settings())
    await lim.check_token(5)
    await lim.record_auth_failure("192.0.2.9")
    assert "tiqora:scim:rl:token:5" in redis.store
    assert "tiqora:rl:apikey:5" not in redis.store
    assert all(k.startswith("tiqora:scim:") for k in redis.store)


@pytest.mark.asyncio
async def test_fail_open_when_redis_raises() -> None:
    lim = ScimRateLimiter(_BrokenRedis(), _settings(scim_auth_fail_max=1, scim_rate_limit_max=1))
    assert (await lim.check_ip("192.0.2.7")).allowed
    assert await lim.record_auth_failure("192.0.2.7") is None
    assert (await lim.check_token(1)).allowed
    assert (await lim.check_token(1)).allowed


@pytest.mark.asyncio
async def test_disabled_limiter_allows_everything() -> None:
    redis = FakeRedis()
    lim = ScimRateLimiter(
        redis, _settings(scim_rate_limit_enabled=False, scim_rate_limit_max=1, scim_auth_fail_max=1)
    )
    assert await lim.record_auth_failure("192.0.2.7") is None
    assert (await lim.check_ip("192.0.2.7")).allowed
    assert (await lim.check_token(1)).allowed and (await lim.check_token(1)).allowed
    assert redis.store == {}
