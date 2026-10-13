"""Process registry and restart requests against a real database (both dialects).

Band 62080-62119: process hostnames and restart reasons start with ``plg62080``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob, TiqoraProcess, TiqoraRestart
from tiqora.plugins import restart, state
from tiqora.plugins.restart import (
    live_loaded_packages,
    plugin_job_active,
    purge_dead_processes,
    register_process,
    request_restart,
    restart_status,
    restart_watch_loop,
    unregister_process,
)

pytestmark = pytest.mark.db

HOST = "plg62080-host"
REASON = "plg62080 Erweiterung installiert"
T0 = datetime(2026, 10, 12, 8, 0, 0)


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(delete(TiqoraProcess).where(TiqoraProcess.hostname.like("plg62080%")))
        await s.execute(delete(TiqoraRestart).where(TiqoraRestart.reason.like("plg62080%")))
        await s.execute(delete(TiqoraJob).where(TiqoraJob.dedupe_key.like("plg62080%")))
        await s.commit()


@pytest.fixture(name="factory", params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory_fixture(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(request.getfixturevalue(request.param)))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clean(sm)
    try:
        yield sm
    finally:
        await _clean(sm)
        await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restart, "_REQUESTED", False)


def _proc(
    name: str,
    role: str,
    gen: int,
    hb: int = 0,
    exiting: int | None = None,
    host: str = HOST,
) -> TiqoraProcess:
    return TiqoraProcess(
        instance_id=f"{role}:{host}:1:{name}",
        role=role,
        hostname=host,
        pid=1,
        boot_generation=gen,
        started_at=T0,
        heartbeat_at=T0 + timedelta(seconds=hb),
        exiting_at=None if exiting is None else T0 + timedelta(seconds=exiting),
    )


async def _request(factory: async_sessionmaker[AsyncSession], now: datetime = T0) -> int:
    async with factory() as s:
        gen = await request_restart(s, by=7, reason=REASON, now=now)
        await s.commit()
    return gen


def _settings(**kw: object) -> Settings:
    return Settings(environment="test", **kw)  # type: ignore[arg-type]


async def test_register_takes_current_generation(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gen = await _request(factory)
    now = T0 + timedelta(seconds=5)
    iid = await register_process(factory, "worker", now=now, hostname=HOST, pid=42)
    assert iid.startswith(f"worker:{HOST}:42:")
    async with factory() as s:
        newest = (await s.execute(select(func.max(TiqoraRestart.generation)))).scalar()
        row = (
            await s.execute(select(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
        ).scalar_one()
    assert row.boot_generation == newest and newest >= gen
    assert row.heartbeat_at == now and row.exiting_at is None and row.pid == 42
    await unregister_process(factory, iid)
    async with factory() as s:
        assert (
            await s.execute(select(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
        ).first() is None


async def test_request_restart_snapshots_live_roles_and_supersedes(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        s.add_all(
            [
                _proc("w", "worker", 0, hb=100),
                _proc("a", "api", 0, hb=100),
                _proc("m", "mcp", 0, hb=0),  # stale: 100 s old
                _proc("x", "ai-worker", 0, hb=100, exiting=100),  # leaving: not counted
            ]
        )
        await s.commit()
    first = await _request(factory, T0 + timedelta(seconds=100))
    second = await _request(factory, T0 + timedelta(seconds=110))
    assert second > first
    async with factory() as s:
        r1 = await s.get(TiqoraRestart, first)
        r2 = await s.get(TiqoraRestart, second)
    assert r1 is not None and r2 is not None
    assert r2.roles == "worker,api"
    assert r1.finished_at == T0 + timedelta(seconds=110) and r1.failed_message is None
    assert r2.finished_at is None and r2.requested_by == 7


async def test_request_restart_roles_follow_plugin_roles(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as s:
        s.add_all([_proc("w", "worker", 0), _proc("ai", "ai-worker", 0), _proc("a", "api", 0)])
        await s.commit()
    monkeypatch.setattr(state, "PLUGIN_ROLES", ("api", "worker", "mcp"))
    g1 = await _request(factory)
    monkeypatch.setattr(state, "PLUGIN_ROLES", ("api", "worker", "mcp", "ai-worker"))
    g2 = await _request(factory)
    async with factory() as s:
        assert (await s.get(TiqoraRestart, g1)).roles == "worker,api"  # type: ignore[union-attr]
        assert (await s.get(TiqoraRestart, g2)).roles == "worker,ai-worker,api"  # type: ignore[union-attr]


async def test_watch_loop_exits_once(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add(_proc("w", "worker", 0))
        await s.commit()
    gen = await _request(factory)
    exits: list[int] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await restart_watch_loop(
        factory,
        "worker",
        f"worker:{HOST}:1:w",
        settings=_settings(),
        on_exit=lambda: exits.append(1),
        sleep=fake_sleep,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    assert exits == [1] and restart.restart_requested() is True
    async with factory() as s:
        row = (await s.execute(select(TiqoraProcess))).scalars().all()
        mine = next(r for r in row if r.instance_id == f"worker:{HOST}:1:w")
    assert mine.exiting_at == T0 + timedelta(seconds=1)
    assert mine.heartbeat_at == T0 + timedelta(seconds=1)
    assert gen > 0


async def test_watch_loop_stays_when_disabled(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add(_proc("w", "worker", 0))
        await s.commit()
    await _request(factory)
    stop_after = 3
    ticks: list[float] = []
    import asyncio

    stop = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) >= stop_after:
            stop.set()

    exits: list[int] = []
    await restart_watch_loop(
        factory,
        "worker",
        f"worker:{HOST}:1:w",
        settings=_settings(plugins_coordinated_restart=False),
        on_exit=lambda: exits.append(1),
        stop=stop,
        sleep=fake_sleep,
        clock=lambda: T0,
    )
    assert exits == [] and len(ticks) == stop_after and restart.restart_requested() is False


async def test_watch_loop_busy_worker_stays_until_job_ends(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import os
    import socket

    async with factory() as s:
        s.add(_proc("w", "worker", 0))
        s.add(
            TiqoraJob(
                kind="core.plugin_install",
                status="running",
                payload="{}",
                dedupe_key="plg62080:job",
                worker_id=f"{socket.gethostname()}:{os.getpid()}",
                created_at=T0,
            )
        )
        await s.commit()
    await _request(factory)
    exits: list[int] = []
    calls: list[float] = []
    stop = asyncio.Event()

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) == 2:
            async with factory() as s:
                job = (
                    await s.execute(select(TiqoraJob).where(TiqoraJob.dedupe_key == "plg62080:job"))
                ).scalar_one()
                job.status = "succeeded"
                await s.commit()
        if len(calls) >= 4:
            stop.set()

    await restart_watch_loop(
        factory,
        "worker",
        f"worker:{HOST}:1:w",
        settings=_settings(),
        on_exit=lambda: exits.append(len(calls)),
        stop=stop,
        sleep=fake_sleep,
        clock=lambda: T0,
    )
    # stayed through the two ticks with the running job, left right after it finished
    assert exits == [2]


async def test_plugin_job_active(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        job = TiqoraJob(
            kind="core.plugin_uninstall",
            status="succeeded",
            payload="{}",
            dedupe_key="plg62080:q",
            created_at=T0,
        )
        s.add(job)
        await s.commit()
        done = await plugin_job_active(s)
        assert done is False
        job.status = "queued"
        await s.commit()
        assert await plugin_job_active(s) is True
        job.status = "running"
        await s.commit()
        assert await plugin_job_active(s) is True
        job.status = "failed"
        await s.commit()
        assert await plugin_job_active(s) is done


async def test_watch_loop_survives_db_errors(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add(_proc("w", "worker", 0))
        await s.commit()
    await _request(factory)
    attempts = {"n": 0}

    def flaky() -> AsyncSession:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise OperationalError("select 1", {}, Exception("connection lost"))
        return factory()

    exits: list[int] = []
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    await restart_watch_loop(
        flaky,  # type: ignore[arg-type]
        "worker",
        f"worker:{HOST}:1:w",
        settings=_settings(),
        on_exit=lambda: exits.append(1),
        sleep=fake_sleep,
        clock=lambda: T0,
    )
    assert len(slept) == 2  # two failed ticks, the third one worked
    assert exits == [1]


async def test_watch_loop_finishes_when_all_roles_are_back(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    gen = await _request(factory)
    async with factory() as s:
        row = await s.get(TiqoraRestart, gen)
        assert row is not None
        row.roles = "worker,api"
        s.add_all(
            [
                _proc("w1", "worker", 0, hb=5, exiting=5),
                _proc("w2", "worker", gen, hb=60),
                _proc("a2", "api", gen, hb=60),
            ]
        )
        await s.commit()
    exits: list[int] = []
    await restart_watch_loop(
        factory,
        "api",
        f"api:{HOST}:1:a2",
        settings=_settings(),
        on_exit=lambda: exits.append(1),
        stop=_Once(),
        sleep=_noop,
        clock=lambda: T0 + timedelta(seconds=60),
    )
    async with factory() as s:
        row = await s.get(TiqoraRestart, gen)
        assert row is not None and row.finished_at == T0 + timedelta(seconds=60)
        assert row.failed_message is None
    assert exits == []


class _Once:
    """Stop event that allows exactly one loop iteration."""

    def __init__(self) -> None:
        self._n = 0

    def is_set(self) -> bool:
        self._n += 1
        return self._n > 1


async def _noop(_seconds: float) -> None:
    return None


async def test_watch_loop_records_failure_once(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add_all([_proc("m1", "mcp", 0, hb=400), _proc("w1", "worker", 0, hb=5, exiting=5)])
        await s.commit()
    gen = await _request(factory, T0)
    async with factory() as s:
        row = await s.get(TiqoraRestart, gen)
        assert row is not None
        row.roles = "worker,mcp"
        await s.commit()
    for _ in range(2):
        await restart_watch_loop(
            factory,
            "mcp",
            f"mcp:{HOST}:1:m1",
            settings=_settings(),
            on_exit=lambda: None,
            stop=_Once(),
            sleep=_noop,
            clock=lambda: T0 + timedelta(seconds=400),
        )
    async with factory() as s:
        row = await s.get(TiqoraRestart, gen)
        assert row is not None
        assert row.failed_message is not None and "Hintergrunddienst" in row.failed_message
        assert row.finished_at is None


async def test_restart_status_role_states(factory: async_sessionmaker[AsyncSession]) -> None:
    gen = await _request(factory)
    async with factory() as s:
        row = await s.get(TiqoraRestart, gen)
        assert row is not None
        row.roles = "worker,mcp,api"
        s.add_all(
            [
                _proc("w1", "worker", 0, hb=20, exiting=10),
                _proc("w2", "worker", gen, hb=20),
                _proc("m1", "mcp", 0, hb=20),
                _proc("a1", "api", 0, hb=20),
                _proc("a2", "api", 0, hb=20),
            ]
        )
        await s.commit()
    async with factory() as s:
        st = await restart_status(s, now=T0 + timedelta(seconds=25), timeout_seconds=180)
    assert st is not None and st.generation == gen and st.coordinated is True
    assert st.reason == REASON and st.finished_at is None and st.failed_message is None
    by_role = {r.role: r for r in st.roles}
    assert [r.role for r in st.roles] == ["worker", "mcp", "api"]
    assert (by_role["worker"].state, by_role["worker"].old, by_role["worker"].new) == ("done", 0, 1)
    assert (by_role["mcp"].state, by_role["mcp"].old, by_role["mcp"].new) == ("waiting", 1, 0)
    assert (by_role["api"].state, by_role["api"].old) == ("waiting", 2)
    # an old mcp process left and none came back within the timeout
    async with factory() as s:
        await s.execute(
            delete(TiqoraProcess).where(TiqoraProcess.instance_id == f"mcp:{HOST}:1:m1")
        )
        s.add(_proc("m1", "mcp", 0, hb=30, exiting=30))
        await s.commit()
    async with factory() as s:
        mid = await restart_status(s, now=T0 + timedelta(seconds=100), timeout_seconds=180)
        late = await restart_status(
            s, now=T0 + timedelta(seconds=300), timeout_seconds=180, coordinated=False
        )
    assert mid is not None and {r.role: r.state for r in mid.roles}["mcp"] == "restarting"
    assert late is not None and {r.role: r.state for r in late.roles}["mcp"] == "timeout"
    assert late.coordinated is False


async def test_purge_dead_processes(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add_all(
            [
                _proc("old", "worker", 0, hb=-7200),
                _proc("fresh", "worker", 0, hb=0),
            ]
        )
        await s.commit()
    async with factory() as s:
        n = await purge_dead_processes(s, older_than=T0 - timedelta(hours=1))
        await s.commit()
    assert n >= 1
    async with factory() as s:
        ids = {
            r.instance_id
            for r in (
                await s.execute(select(TiqoraProcess).where(TiqoraProcess.hostname == HOST))
            ).scalars()
        }
    assert ids == {f"worker:{HOST}:1:fresh"}


async def test_unregister_keeps_exiting_row(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        s.add(_proc("a1", "api", 0))
        s.add(_proc("a2", "api", 0))
        await s.commit()
    await _request(factory)
    exits: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        pass

    await restart_watch_loop(
        factory,
        "api",
        f"api:{HOST}:1:a1",
        settings=_settings(),
        on_exit=lambda: exits.append(1),
        sleep=fake_sleep,
        clock=lambda: T0 + timedelta(seconds=1),
    )
    assert exits == [1]
    await unregister_process(factory, f"api:{HOST}:1:a1")
    async with factory() as s:
        a1 = (
            await s.execute(
                select(TiqoraProcess).where(TiqoraProcess.instance_id == f"api:{HOST}:1:a1")
            )
        ).scalar_one()
    assert a1.exiting_at is not None
    # a2 must still wait for the replacement of a1
    stayed: list[int] = []

    async def stop_sleep(seconds: float) -> None:
        stayed.append(1)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await restart_watch_loop(
            factory,
            "api",
            f"api:{HOST}:1:a2",
            settings=_settings(),
            on_exit=lambda: exits.append(2),
            sleep=stop_sleep,
            clock=lambda: T0 + timedelta(seconds=2),
        )
    assert exits == [1] and stayed == [1]


async def test_loaded_packages_stored_and_read(factory: async_sessionmaker[AsyncSession]) -> None:
    iid = await register_process(
        factory,
        "api",
        now=T0,
        hostname=HOST,
        pid=7,
        loaded_packages=["acme.demo@abc123", "acme.other@def456"],
    )
    async with factory() as s:
        row = (
            await s.execute(select(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
        ).scalar_one()
        assert json.loads(row.loaded_packages or "") == ["acme.demo@abc123", "acme.other@def456"]
        live = await live_loaded_packages(s, now=T0 + timedelta(seconds=5))
        assert live["acme.demo"] == {"abc123"} and live["acme.other"] == {"def456"}
        assert await live_loaded_packages(s, now=T0 + timedelta(hours=1)) == {}


async def test_reinsert_keeps_generation_and_packages(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _request(factory)
    iid = await register_process(
        factory, "api", now=T0, hostname=HOST, pid=8, loaded_packages=["acme.demo@abc"]
    )
    gen_at_boot = (await _row_of(factory, iid)).boot_generation
    ticks = iter([T0, T0 + timedelta(seconds=1)])

    async def sleep(seconds: float) -> None:
        async with factory() as s:
            await s.execute(delete(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
            await _request_in(s)
            await s.commit()

    stop = asyncio.Event()
    calls = 0

    async def sleeper(seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await sleep(seconds)
        else:
            stop.set()

    await restart_watch_loop(
        factory,
        "api",
        iid,
        settings=_settings(plugins_coordinated_restart=False),
        on_exit=lambda: None,
        stop=stop,
        sleep=sleeper,
        clock=lambda: next(ticks, T0 + timedelta(seconds=2)),
        loaded_packages=["acme.demo@abc"],
    )
    row = await _row_of(factory, iid)
    async with factory() as s:  # the re-inserted row carries the real hostname
        await s.execute(delete(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
        await s.commit()
    assert row.boot_generation == gen_at_boot
    assert json.loads(row.loaded_packages or "") == ["acme.demo@abc"]


async def _row_of(factory: async_sessionmaker[AsyncSession], iid: str) -> TiqoraProcess:
    async with factory() as s:
        return (
            await s.execute(select(TiqoraProcess).where(TiqoraProcess.instance_id == iid))
        ).scalar_one()


async def _request_in(s: AsyncSession) -> None:
    await request_restart(s, by=7, reason=REASON, now=T0 + timedelta(seconds=1))
