"""The AI worker's web chat assistant loop (band 54630-54659, both dialects)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tiqora.ai import chat_bot_loop
from tiqora.ai.chat_bot_loop import rescan_pending_sessions, run_chat_bot_loop
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatSession, TiqoraSettings
from tiqora.domain.settings_store import KEY_AI_WORKER_ENABLED
from tiqora.jobs.service import utc_now
from tiqora.webchat.bot import BOT_ALIVE_KEY, BOT_CLAIM_STALE_SECONDS
from tiqora.webchat.notify import BOT_QUEUE_KEY

pytestmark = pytest.mark.db

CONFIG_LO, CONFIG_HI = 54630, 54660
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


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
        conn.execute(
            delete(TiqoraChatSession).where(
                TiqoraChatSession.config_id >= CONFIG_LO, TiqoraChatSession.config_id < CONFIG_HI
            )
        )
        conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == KEY_AI_WORKER_ENABLED))
    engine.dispose()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis
    settings: Settings

    def add_session(
        self,
        n: int,
        *,
        status: str = "bot",
        last_visitor_seq: int = 1,
        answered: int = 0,
        claimed_ago: float | None = None,
    ) -> str:
        sid = f"wc{54630 + n}".ljust(32, "0")
        now = utc_now()
        engine = create_engine(self.url)
        with engine.begin() as conn:
            conn.execute(
                insert(TiqoraChatSession).values(
                    id=sid,
                    config_id=CONFIG_LO,
                    queue_id=54631,
                    ticket_id=54630 + n,
                    status=status,
                    token_hash="0" * 64,
                    visitor_id="v" * 22,
                    last_seq=last_visitor_seq,
                    visitor_last_seen=now,
                    created=now + timedelta(seconds=n),
                    last_visitor_seq=last_visitor_seq,
                    bot_answered_seq=answered,
                    bot_claimed_at=(
                        None if claimed_ago is None else now - timedelta(seconds=claimed_ago)
                    ),
                    bot_claim_owner=None if claimed_ago is None else "t:1",
                )
            )
        engine.dispose()
        return sid

    def set_enabled(self, enabled: bool) -> None:
        engine = create_engine(self.url)
        with engine.begin() as conn:
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == KEY_AI_WORKER_ENABLED))
            conn.execute(
                insert(TiqoraSettings).values(
                    key=KEY_AI_WORKER_ENABLED, value="true" if enabled else "false"
                )
            )
        engine.dispose()


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    e = Env(
        url, factory, FakeRedis(), get_settings().model_copy(update={"chat_bot_concurrency": 4})
    )
    e.set_enabled(True)
    yield e
    await aengine.dispose()
    _cleanup(url)


async def _until(predicate: Callable[[], bool], limit: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


def _start(
    env: Env, clock: Callable[[], float] | None = None
) -> tuple[asyncio.Event, asyncio.Task]:
    stop = asyncio.Event()
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    task = asyncio.create_task(
        run_chat_bot_loop(
            stop,
            settings=env.settings,
            factory=env.factory,
            redis_client=env.redis,
            **kwargs,
        )
    )
    return stop, task


async def _stop(stop: asyncio.Event, task: asyncio.Task, limit: float = 1.0) -> None:
    stop.set()
    await asyncio.wait_for(task, timeout=limit)
    assert task.exception() is None


@pytest.fixture
def turn(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value="replied")
    monkeypatch.setattr(chat_bot_loop, "run_chat_bot_turn", mock)
    return mock


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_loop_runs_enqueued_turn(env: Env, turn: AsyncMock) -> None:
    await env.redis.rpush(BOT_QUEUE_KEY, "sid-queued")
    stop, task = _start(env)
    await _until(lambda: turn.await_count == 1)
    assert turn.await_args.kwargs["session_id"] == "sid-queued"
    assert turn.await_args.kwargs["owner"]
    assert await env.redis.exists(BOT_ALIVE_KEY) == 1
    assert await env.redis.llen(BOT_QUEUE_KEY) == 0
    await _stop(stop, task)  # the stop event ends the loop within a second
    assert turn.await_count == 1


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_alive_marker_only_while_worker_enabled(env: Env, turn: AsyncMock) -> None:
    env.set_enabled(False)
    env.add_session(1)
    await env.redis.rpush(BOT_QUEUE_KEY, "sid-queued")
    stop, task = _start(env)
    await asyncio.sleep(0.4)
    assert await env.redis.exists(BOT_ALIVE_KEY) == 0
    assert await env.redis.llen(BOT_QUEUE_KEY) == 1  # not consumed
    assert turn.await_count == 0  # and the database rescan is off as well
    await _stop(stop, task)


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_stale_claim_is_retaken_after_restart(env: Env, turn: AsyncMock) -> None:
    stale = env.add_session(1, claimed_ago=BOT_CLAIM_STALE_SECONDS + 20)
    env.add_session(2, claimed_ago=10)  # somebody else's fresh claim
    env.add_session(3, answered=1)  # nothing pending
    env.add_session(4, status="waiting")  # a human has it
    fresh = env.add_session(5)  # never claimed
    found = await rescan_pending_sessions(env.factory, now=utc_now())
    assert sorted(found) == sorted([stale, fresh])
    stop, task = _start(env)
    await _until(lambda: turn.await_count == 2)
    assert {c.kwargs["session_id"] for c in turn.await_args_list} == {stale, fresh}
    await _stop(stop, task)


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_concurrency_bounded(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.settings = env.settings.model_copy(update={"chat_bot_concurrency": 2})
    inflight = 0
    peak = 0
    done: list[str] = []

    async def slow_turn(*_args: Any, session_id: str, **_kwargs: Any) -> str:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        done.append(session_id)
        return "replied"

    monkeypatch.setattr(chat_bot_loop, "run_chat_bot_turn", slow_turn)
    await env.redis.rpush(BOT_QUEUE_KEY, *[f"sid-{i}" for i in range(10)])
    stop, task = _start(env)
    await _until(lambda: len(done) == 10, limit=5)
    assert peak == 2
    await _stop(stop, task)


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_redis_down_falls_back_to_rescan(
    env: Env, turn: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_bot_loop, "BOT_REDIS_ERROR_SLEEP_SECONDS", 0.01)
    env.redis.fail = True  # BLPOP, SET and DELETE all raise
    sid = env.add_session(1)
    stop, task = _start(env)
    await _until(lambda: turn.await_count >= 1)
    assert turn.await_args.kwargs["session_id"] == sid
    assert not task.done()  # the loop survived the outage
    await _stop(stop, task)


@pytest.mark.parametrize("env", _URLS, indirect=True)
@pytest.mark.asyncio
async def test_retry_is_delayed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = AsyncMock(side_effect=["retry", "replied"])
    monkeypatch.setattr(chat_bot_loop, "run_chat_bot_turn", mock)
    clock = FakeClock()
    await env.redis.rpush(BOT_QUEUE_KEY, "sid-retry")
    stop, task = _start(env, clock)
    await _until(lambda: mock.await_count == 1)
    clock.now += 4.0  # not yet
    await asyncio.sleep(0.3)
    assert mock.await_count == 1
    clock.now += 1.5  # 5.5 s after the failed turn
    await _until(lambda: mock.await_count == 2)
    assert mock.await_args_list[1].kwargs["session_id"] == "sid-retry"
    await _stop(stop, task)
