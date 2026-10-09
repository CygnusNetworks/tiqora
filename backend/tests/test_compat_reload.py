"""Hot reload of webservice configs (MariaDB and PostgreSQL).

Seed ids use 8940-8949 (GI band): agent 8940, webservice 8941. Redis is the in-repo
``_FakeRedis`` from ``test_events_sse`` (no fakeredis dependency).
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tests.test_events_sse import _FakeRedis
from tiqora.api.compat.reload import (
    COMPAT_WEBSERVICES_CHANNEL,
    publish_webservices_changed,
    reload_tick,
    run_webservice_reloader,
)
from tiqora.api.compat.webservices import get_registry
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL
from tiqora.znuny.password import hash_password

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

NOW = datetime(2026, 3, 1, 12, 0, 0)
PASSWORD = "hotreload-secret"
LOGIN = "hot.agent"
AGENT_ID, WS_ID = 8940, 8941
CFG = {
    "Provider": {
        "Transport": {
            "Type": "HTTP::REST",
            "Config": {
                "RouteOperationMapping": {
                    "Sess": {"Route": "/Session", "RequestMethod": ["POST"]},
                }
            },
        },
        "Operation": {"Sess": {"Type": "Session::SessionCreate"}},
    }
}
_SEEDED: list[str] = []
_COUNTER_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_number_counter")
_SNAPSHOTS: dict[str, dict[str, int]] = {}


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        (f"DELETE FROM gi_webservice_config WHERE id = {WS_ID}", {}),
        (
            "DELETE FROM sessions WHERE session_id IN (SELECT session_id FROM"
            " (SELECT session_id FROM sessions WHERE data_key = 'UserLogin'"
            " AND data_value = 'hot.agent') s)",
            {},
        ),
        (f"DELETE FROM users WHERE id = {AGENT_ID}", {}),
    )


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        _cleanup(sync_url)
        delete_rows_above(sync_url, _SNAPSHOTS[sync_url])
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    _cleanup(sync_url)
    _SNAPSHOTS[sync_url] = snapshot_max_ids(sync_url, _COUNTER_TABLES)
    engine.dispose()


def _insert_webservice(sync_url: str, name: str) -> None:
    engine = create_engine(sync_url)
    raw = yaml.safe_dump(CFG)
    stored: bytes | str = raw if engine.dialect.name == "postgresql" else raw.encode()
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM gi_webservice_config WHERE id = {WS_ID}"))
        conn.execute(text(f"DELETE FROM users WHERE id = {AGENT_ID}"))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Hot', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "login": LOGIN, "pw": hash_password(PASSWORD), "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :name, :config, 1, :t, 1, :t, 1)"
            ),
            {"id": WS_ID, "name": name, "config": stored, "t": NOW},
        )
    engine.dispose()


def _update_webservice(sync_url: str, **sets: Any) -> None:
    engine = create_engine(sync_url)
    assignments = ", ".join(f"{col} = :{col}" for col in sets)
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE gi_webservice_config SET {assignments} WHERE id = {WS_ID}"), sets
        )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _Env:
    def __init__(self, app: Any, client: Any, engine: Any, redis: _FakeRedis | None) -> None:
        self.app, self.client, self.engine, self.redis = app, client, engine, redis


async def _make_env(sync_url: str, *, with_redis: bool = False) -> _Env:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings
    from tiqora.domain.auth import SessionStore

    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    class _KV:
        def __init__(self) -> None:
            self._store: dict[str, str] = {}

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self._store[key] = value

        async def get(self, key: str) -> str | None:
            return self._store.get(key)

        async def expire(self, key: str, ttl: int) -> None:
            pass

        async def delete(self, key: str) -> None:
            self._store.pop(key, None)

    class _FakeSettings:
        session_ttl_seconds = 86400
        session_cookie_name = "tiqora_session"

    store = SessionStore(_KV(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    fake = _FakeRedis() if with_redis else None
    app.state.redis = fake
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return _Env(app, client, engine, fake)


async def _session_status(client: Any, name: str) -> int:
    resp = await client.post(
        f"/znuny-compat/Webservice/{name}/Session",
        json={"UserLogin": LOGIN, "Password": PASSWORD},
    )
    return int(resp.status_code)


async def test_rename_and_disable_are_picked_up(sync_url: str) -> None:
    _insert_webservice(sync_url, "HotOld")
    env = await _make_env(sync_url)
    async with env.client:
        assert await reload_tick(env.app, force=True) is True
        assert await _session_status(env.client, "HotOld") == 200

        later = datetime.now() + timedelta(seconds=1)
        _update_webservice(sync_url, name="HotNew", change_time=later)
        assert await reload_tick(env.app, force=False) is True
        assert await _session_status(env.client, "HotOld") == 404
        assert await _session_status(env.client, "HotNew") == 200

        _update_webservice(sync_url, valid_id=2, change_time=later + timedelta(seconds=1))
        assert await reload_tick(env.app, force=False) is True
        assert await _session_status(env.client, "HotNew") == 404
    await env.engine.dispose()


async def test_tick_without_change_is_noop(sync_url: str) -> None:
    _insert_webservice(sync_url, "HotOld")
    env = await _make_env(sync_url)
    async with env.client:
        assert await reload_tick(env.app, force=False) is True  # first load
        assert await reload_tick(env.app, force=False) is False
        assert await reload_tick(env.app, force=True) is True  # force always reloads
    await env.engine.dispose()


async def test_redis_message_forces_reload(sync_url: str) -> None:
    _insert_webservice(sync_url, "HotOld")
    env = await _make_env(sync_url, with_redis=True)
    assert env.redis is not None
    async with env.client:
        await reload_tick(env.app, force=True)
        registry = get_registry(env.app)
        stop = asyncio.Event()
        task = asyncio.create_task(run_webservice_reloader(env.app, interval_seconds=60, stop=stop))
        try:
            for _ in range(200):  # wait until the reloader is subscribed
                if env.redis._subscribers.get(COMPAT_WEBSERVICES_CHANNEL):
                    break
                await asyncio.sleep(0.01)
            assert env.redis._subscribers.get(COMPAT_WEBSERVICES_CHANNEL)
            # rename WITHOUT touching change_time: the fingerprint cannot see it
            _update_webservice(sync_url, name="HotMissed")
            assert registry.by_name("HotMissed") is None
            await publish_webservices_changed(env.redis)
            for _ in range(200):
                if registry.by_name("HotMissed") is not None:
                    break
                await asyncio.sleep(0.01)
            assert registry.by_name("HotMissed") is not None
            assert registry.by_name("HotOld") is None
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)
        assert task.done() and task.exception() is None
    assert env.redis._subscribers.get(COMPAT_WEBSERVICES_CHANNEL) == []
    await env.engine.dispose()


async def test_reloader_survives_db_error(sync_url: str) -> None:
    _insert_webservice(sync_url, "HotOld")
    env = await _make_env(sync_url)
    real_factory = env.app.state.session_factory
    calls = {"n": 0}

    def flaky_factory() -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        return real_factory()

    env.app.state.session_factory = flaky_factory
    registry = get_registry(env.app)
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(env.app, interval_seconds=0.05, stop=stop))
    try:
        for _ in range(200):
            if registry.by_name("HotOld") is not None:
                break
            await asyncio.sleep(0.02)
        assert registry.by_name("HotOld") is not None
        assert calls["n"] >= 2
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert task.exception() is None
    await env.engine.dispose()


async def test_admin_reload_publishes(sync_url: str) -> None:
    from tiqora.api.v1.admin.deps import get_admin_user

    _insert_webservice(sync_url, "HotOld")
    env = await _make_env(sync_url, with_redis=True)
    assert env.redis is not None

    class _Admin:
        login = "root@localhost"

    env.app.dependency_overrides[get_admin_user] = lambda: _Admin()
    async with env.client:
        resp = await env.client.post("/znuny-compat/admin/reload")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert "HotOld" in body["webservices"]
        assert body["webservices_loaded"] == len(body["webservices"])
    assert env.redis.published == [(COMPAT_WEBSERVICES_CHANNEL, "reload")]
    await env.engine.dispose()


async def test_events_channel_not_used() -> None:
    redis = _FakeRedis()
    await publish_webservices_changed(redis)
    assert [channel for channel, _ in redis.published] == [COMPAT_WEBSERVICES_CHANNEL]
    assert COMPAT_WEBSERVICES_CHANNEL != TIQORA_EVENTS_CHANNEL
    assert all(channel != TIQORA_EVENTS_CHANNEL for channel, _ in redis.published)


async def test_publish_is_best_effort() -> None:
    class _Broken:
        async def publish(self, channel: str, message: str) -> None:
            raise ConnectionError("redis down")

    await publish_webservices_changed(_Broken())
    await publish_webservices_changed(None)


async def test_reloader_leaves_no_tasks_behind() -> None:
    class _App:
        class state:  # noqa: N801
            redis = _FakeRedis()
            session_factory = None

    before = asyncio.all_tasks()
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(_App, interval_seconds=60, stop=stop))
    for _ in range(200):
        if _App.state.redis._subscribers.get(COMPAT_WEBSERVICES_CHANNEL):
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 2)
    assert asyncio.all_tasks() == before


class _StubPubSub:
    """pubsub stub whose get_message / subscribe can be scripted to fail."""

    def __init__(self, owner: _StubRedis) -> None:
        self.owner = owner
        self.closed = False
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        if self.owner.subscribe_failures > 0:
            self.owner.subscribe_failures -= 1
            raise ConnectionError("subscribe failed")
        self.owner.live = self

    async def unsubscribe(self, channel: str | None = None) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,  # noqa: ASYNC109
    ) -> dict[str, Any] | None:
        if self.owner.get_failures > 0 and timeout:
            self.owner.get_failures -= 1
            raise ConnectionError("get_message failed")
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout or 0.001)
        except TimeoutError:
            return None


class _StubRedis:
    def __init__(self, *, subscribe_failures: int = 0, get_failures: int = 0) -> None:
        self.subscribe_failures = subscribe_failures
        self.get_failures = get_failures
        self.pubsubs: list[_StubPubSub] = []
        self.live: _StubPubSub | None = None
        self.factory_failures = 0

    def pubsub(self) -> _StubPubSub:
        if self.factory_failures > 0:
            self.factory_failures -= 1
            raise ConnectionError("pubsub() failed")
        ps = _StubPubSub(self)
        self.pubsubs.append(ps)
        return ps


class _CountingApp:
    """App stand-in whose registry refresh is observable via session_factory calls."""

    def __init__(self, redis: Any) -> None:
        self.calls: list[bool] = []
        outer = self

        class _State:
            pass

        self.state = _State()
        self.state.redis = redis  # type: ignore[attr-defined]
        self.state.session_factory = lambda: outer  # type: ignore[attr-defined]
        reg = type("R", (), {})()

        async def refresh(session: Any, *, force: bool = False) -> bool:
            outer.calls.append(force)
            return True

        reg.refresh = refresh  # type: ignore[attr-defined]
        self.state.compat_webservices = reg  # type: ignore[attr-defined]

    async def __aenter__(self) -> _CountingApp:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def _until(predicate: Any, seconds: float = 3.0) -> bool:
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_get_message_error_closes_pubsub_and_resubscribes() -> None:
    redis = _StubRedis(get_failures=1)
    app = _CountingApp(redis)
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(app, interval_seconds=0.3, stop=stop))
    try:
        assert await _until(lambda: len(redis.pubsubs) >= 2)  # resubscribed
        assert redis.pubsubs[0].closed is True
        # no hot loop: the failure was followed by a ~interval wait, not a spin
        assert len(redis.pubsubs) <= 3
        assert await _until(lambda: redis.live is redis.pubsubs[-1])
        calls_before = len(app.calls)
        await redis.pubsubs[-1].queue.put({"type": "message", "data": "reload"})
        assert await _until(lambda: True in app.calls[calls_before:])
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert all(ps.closed for ps in redis.pubsubs[:-1])
    assert redis.pubsubs[-1].closed is True


async def test_subscribe_error_closes_pubsub_and_polling_continues() -> None:
    redis = _StubRedis(subscribe_failures=2)
    app = _CountingApp(redis)
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(app, interval_seconds=0.05, stop=stop))
    try:
        assert await _until(lambda: len(app.calls) >= 2)  # fallback polling reloads
        assert await _until(lambda: redis.live is not None)  # then subscribes successfully
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    failed = [ps for ps in redis.pubsubs if ps is not redis.live]
    assert len(failed) >= 2
    assert all(ps.closed for ps in failed)
    assert all(force is False for force in app.calls[:2])


async def test_pubsub_factory_error_falls_back_to_polling() -> None:
    redis = _StubRedis()
    redis.factory_failures = 2
    app = _CountingApp(redis)
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(app, interval_seconds=0.05, stop=stop))
    try:
        assert await _until(lambda: len(app.calls) >= 2)
        assert await _until(lambda: redis.live is not None)
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert task.exception() is None


async def test_message_burst_is_coalesced_into_one_forced_reload() -> None:
    redis = _StubRedis()
    app = _CountingApp(redis)
    stop = asyncio.Event()
    task = asyncio.create_task(run_webservice_reloader(app, interval_seconds=30, stop=stop))
    try:
        assert await _until(lambda: redis.live is not None)
        assert redis.live is not None
        for _ in range(5):
            await redis.live.queue.put({"type": "message", "data": "reload"})
        assert await _until(lambda: True in app.calls)
        await asyncio.sleep(0.2)
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    assert app.calls.count(True) == 1


async def test_subscribe_cancelled_midway_closes_the_candidate_and_reraises() -> None:
    """A BaseException (cancellation during shutdown) must not leak the pooled pubsub."""
    from types import SimpleNamespace

    from tiqora.api.compat.reload import _subscribe

    class _Pubsub:
        closed = False

        async def subscribe(self, channel: str) -> None:
            raise asyncio.CancelledError

        async def unsubscribe(self, channel: str) -> None:
            return None

        async def aclose(self) -> None:
            self.closed = True

    candidate = _Pubsub()
    app = SimpleNamespace(state=SimpleNamespace(redis=SimpleNamespace(pubsub=lambda: candidate)))
    with pytest.raises(asyncio.CancelledError):
        await _subscribe(app)
    assert candidate.closed is True
