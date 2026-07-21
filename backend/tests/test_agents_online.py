"""Tests for global online-agent presence (``GET /agents/online`` + heartbeat).

No Docker required for the unit suite: Redis is an in-memory fake (same
pattern as ``test_events_sse`` / ``test_totp``), and the users table is
substituted with a tiny session stand-in that returns predefined rows.
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any

import pytest

from tiqora.api.deps import get_current_user, get_db, get_redis
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser

_FAKE_USER = AuthenticatedUser(
    id=7,
    login="agent7",
    first_name="Ada",
    last_name="Agent",
    auth_method="session",
    avatar_url="https://example.com/ada.png",
)


class _FakeRedis:
    """In-memory Redis stand-in with TTL bookkeeping + scan_iter."""

    def __init__(self, *, fail_set: bool = False, fail_scan: bool = False) -> None:
        self._store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail_set = fail_set
        self.fail_scan = fail_scan

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.fail_set:
            raise ConnectionError("redis down")
        self._store[key] = value
        self.ttls[key] = ex

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self.ttls.pop(key, None)

    async def scan_iter(self, match: str | None = None) -> Any:
        if self.fail_scan:
            raise ConnectionError("redis down")
        for key in list(self._store.keys()):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


class _UserRow:
    """Stand-in for a ``Users`` ORM row (only the fields the endpoint reads)."""

    def __init__(
        self,
        id: int,  # noqa: A002 — mirrors Users.id
        login: str,
        first_name: str,
        last_name: str,
        valid_id: int = 1,
    ) -> None:
        self.id = id
        self.login = login
        self.first_name = first_name
        self.last_name = last_name
        self.valid_id = valid_id


class _Scalars:
    def __init__(self, rows: list[_UserRow]) -> None:
        self._rows = rows

    def all(self) -> list[_UserRow]:
        return list(self._rows)


class _Result:
    def __init__(self, rows: list[_UserRow]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _FakeSession:
    """Returns every seeded valid user; the endpoint still filters by online ids."""

    def __init__(self, users: list[_UserRow]) -> None:
        self._users = users

    async def execute(self, stmt: Any) -> _Result:
        del stmt
        return _Result([u for u in self._users if u.valid_id == 1])


def _build_app(
    fake_redis: _FakeRedis,
    users: list[_UserRow] | None = None,
) -> Any:
    from tiqora.api.app import create_app

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_redis] = lambda: fake_redis

    session = _FakeSession(users or [])

    async def _override_db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _override_db
    return app


@pytest.mark.asyncio
async def test_ping_sets_online_key_with_ttl() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    app = _build_app(fake_redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/agents/presence/ping")
        assert resp.status_code == 204

    key = "tiqora:online:7"
    assert key in fake_redis.ttls
    assert fake_redis.ttls[key] == 60
    payload = json.loads(fake_redis._store[key])
    assert payload["login"] == "agent7"
    assert payload["full_name"] == "Ada Agent"
    assert payload["avatar_url"] == "https://example.com/ada.png"


@pytest.mark.asyncio
async def test_list_online_returns_resolved_valid_agents() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    users = [
        _UserRow(7, "agent7", "Ada", "Agent", valid_id=1),
        _UserRow(8, "agent8", "Bob", "Beta", valid_id=1),
        _UserRow(9, "agent9", "Inv", "Alid", valid_id=2),  # invalid — must drop
    ]
    # Two live keys + one for the invalid user + one garbage key.
    await fake_redis.set(
        "tiqora:online:7",
        json.dumps({"login": "agent7", "full_name": "Ada Agent", "avatar_url": "https://a"}),
        ex=60,
    )
    await fake_redis.set(
        "tiqora:online:8",
        json.dumps({"login": "agent8", "full_name": "Bob Beta"}),
        ex=60,
    )
    await fake_redis.set(
        "tiqora:online:9",
        json.dumps({"login": "agent9", "full_name": "Inv Alid"}),
        ex=60,
    )
    await fake_redis.set("tiqora:online:not-an-id", "x", ex=60)

    app = _build_app(fake_redis, users)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/online")
        assert resp.status_code == 200
        body = resp.json()

    assert [a["id"] for a in body] == [7, 8]  # sorted by login
    assert body[0] == {
        "id": 7,
        "login": "agent7",
        "full_name": "Ada Agent",
        "avatar_url": "https://a",
    }
    assert body[1]["login"] == "agent8"
    assert body[1]["avatar_url"] is None


@pytest.mark.asyncio
async def test_list_online_empty_when_no_keys() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    app = _build_app(fake_redis, [_UserRow(7, "agent7", "Ada", "Agent")])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/online")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_list_online_nonfatal_when_redis_down() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis(fail_scan=True)
    app = _build_app(fake_redis, [_UserRow(7, "agent7", "Ada", "Agent")])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/online")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
async def test_ping_nonfatal_when_redis_down() -> None:
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis(fail_set=True)
    app = _build_app(fake_redis)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/agents/presence/ping")
        assert resp.status_code == 204


@pytest.mark.asyncio
async def test_touch_online_presence_direct() -> None:
    from tiqora.api.v1.agents import ONLINE_TTL_SECONDS, touch_online_presence

    fake_redis = _FakeRedis()
    await touch_online_presence(fake_redis, _FAKE_USER)  # type: ignore[arg-type]
    key = "tiqora:online:7"
    assert fake_redis.ttls[key] == ONLINE_TTL_SECONDS

    # Broken redis must not raise.
    await touch_online_presence(_FakeRedis(fail_set=True), _FAKE_USER)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_user_row_excluded() -> None:
    """Online key without a matching valid users row must not appear."""
    from httpx import ASGITransport, AsyncClient

    fake_redis = _FakeRedis()
    await fake_redis.set(
        "tiqora:online:99",
        json.dumps({"login": "ghost", "full_name": "Ghost"}),
        ex=60,
    )
    # Only user 7 exists in the table — 99 is missing.
    app = _build_app(fake_redis, [_UserRow(7, "agent7", "Ada", "Agent")])
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/online")
        assert resp.status_code == 200
        assert resp.json() == []
