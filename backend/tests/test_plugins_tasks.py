"""Scheduled plugin tasks: due times, failures, timeout, concurrency, catch-up (both dialects).

No Znuny rows are needed; hook rows ``task:org.example.plg49%`` are cleaned by the shared
plugin state fixtures.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tiqora.worker.__main__ as worker_main
from tests._plugins_state_helpers import (  # noqa: F401  (fixtures)
    db_url_fixture,
    factory_fixture,
)
from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.extensions.registry import Registry
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.plugins.store import reset_plugin
from tiqora.plugins.tasks import (
    MAX_PARALLEL_TASKS,
    SCHEDULED_TASKS,
    TRIP_AFTER,
    Schedule,
    ScheduledTask,
    TaskContext,
    run_plugin_tasks_tick,
)
from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG

pytestmark = pytest.mark.db

PLUGIN = "org.example.plg49b"
START = datetime(2026, 10, 10, 10, 0)

Tick = Callable[..., Awaitable[dict[str, int]]]


@pytest.fixture
def f(factory: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    return factory


@pytest.fixture
def runs() -> dict[str, int]:
    return {"every": 0, "daily": 0, "bad": 0, "slow": 0}


@pytest.fixture
def contexts() -> list[TaskContext]:
    return []


@pytest.fixture
def clock() -> list[datetime]:
    return [START]


def _task(name: str, run: Callable[[TaskContext], Awaitable[Any]], **sched: Any) -> ScheduledTask:
    schedule = Schedule(**sched)
    schedule.validate()
    return ScheduledTask(
        key=f"{PLUGIN}:{name}",
        plugin_id=PLUGIN,
        schedule=schedule,
        run=run,
        timeout_seconds=5,
    )


@pytest.fixture
async def reg(runs: dict[str, int], contexts: list[TaskContext]) -> AsyncIterator[Registry]:
    async def every(ctx: TaskContext) -> dict[str, Any]:
        runs["every"] += 1
        contexts.append(ctx)
        return {"ok": 1}

    async def daily(ctx: TaskContext) -> None:
        runs["daily"] += 1

    async def bad(ctx: TaskContext) -> None:
        runs["bad"] += 1
        raise RuntimeError("kaputt")

    async def slow(ctx: TaskContext) -> None:
        runs["slow"] += 1
        await asyncio.sleep(3)

    registry = Registry()
    for item in (
        _task("every", every, every_seconds=300),
        _task("daily", daily, daily_at="03:00"),
        _task("bad", bad, every_seconds=60),
        ScheduledTask(
            key=f"{PLUGIN}:slow",
            plugin_id=PLUGIN,
            schedule=Schedule(every_seconds=60),
            run=slow,
            timeout_seconds=1,
        ),
    ):
        registry.add(SCHEDULED_TASKS, item, owner=PLUGIN)
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        yield registry
    finally:
        set_loaded_plugins([])


@pytest.fixture
def tick(reg: Registry, f: async_sessionmaker[AsyncSession], clock: list[datetime]) -> Tick:
    async def _tick(**kw: Any) -> dict[str, int]:
        return await run_plugin_tasks_tick(
            registry=kw.pop("registry", reg),
            session_factory=f,
            now=lambda: clock[0],
            **kw,
        )

    return _tick


async def _hooks(f: async_sessionmaker[AsyncSession]) -> dict[str, TiqoraPluginHook]:
    async with f() as s:
        rows = (
            await s.execute(
                select(TiqoraPluginHook).where(
                    TiqoraPluginHook.hook_key.like("task:org.example.plg49%")
                )
            )
        ).scalars()
        return {h.hook_key: h for h in rows}


async def test_interval_runs_now_then_waits(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    clock[0] = datetime(2026, 10, 10, 10, 0)
    await tick()
    assert runs["every"] == 1
    clock[0] += timedelta(seconds=299)
    await tick()
    assert runs["every"] == 1
    clock[0] += timedelta(seconds=1)
    await tick()
    assert runs["every"] == 2
    hook = (await _hooks(f))["task:org.example.plg49b:every"]
    assert hook.last_run_at == clock[0] and hook.last_ok_at == clock[0]
    assert hook.consecutive_failures == 0 and hook.cursor is None and hook.kind == "task"


async def test_daily_waits_for_next_occurrence(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    clock[0] = datetime(2026, 10, 10, 4, 0)
    await tick()
    assert runs["daily"] == 0
    clock[0] = datetime(2026, 10, 11, 3, 0)
    await tick()
    assert runs["daily"] == 1


async def test_failure_written_and_trips_after_five(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    clock[0] = datetime(2026, 10, 10, 10, 0)
    results = []
    for _ in range(TRIP_AFTER):
        results.append(await tick())
        clock[0] += timedelta(seconds=60)
    # bad and slow (timeout every time) both open their breaker on the fifth failure
    assert [r["tripped"] for r in results] == [0, 0, 0, 0, 2]
    hook = (await _hooks(f))["task:org.example.plg49b:bad"]
    assert hook.consecutive_failures == 5 and hook.tripped_at is not None
    assert hook.last_error == "RuntimeError: kaputt" and hook.last_error_at is not None
    assert hook.last_run_at is not None  # written before the run
    before = runs["bad"]
    await tick()
    assert runs["bad"] == before  # tripped: not run
    # an admin retry (reset) lets it run again
    async with f() as s:
        await reset_plugin(s, PLUGIN, now=clock[0])
        await s.commit()
    clock[0] += timedelta(seconds=60)
    await tick()
    assert runs["bad"] == before + 1


async def test_timeout(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    clock[0] = datetime(2026, 10, 10, 10, 0)
    res = await tick()
    hook = (await _hooks(f))["task:org.example.plg49b:slow"]
    assert "Zeitlimit" in (hook.last_error or "") and res["failed"] >= 1
    assert hook.consecutive_failures == 1


async def test_result_counts(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    res = await tick()
    # every, bad, slow run at once; daily waits for 03:00
    assert res == {"tasks": 4, "ran": 3, "failed": 2, "tripped": 0}


async def test_task_context_uses_stored_context_and_times(  # type: ignore[no-untyped-def]
    f, reg, runs, clock, tick, contexts
) -> None:
    await tick()
    clock[0] += timedelta(seconds=300)
    await tick()
    first, second = contexts
    assert first.plugin.plugin_id == PLUGIN and first.last_run_at is None
    assert first.scheduled_for == START
    assert second.last_run_at == START and second.scheduled_for == START + timedelta(seconds=300)


async def test_downtime_runs_once_not_a_burst(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    await tick()
    assert runs["every"] == 1
    clock[0] += timedelta(days=3)  # worker was down for 864 missed slots
    await tick()
    assert runs["every"] == 2
    await tick()
    assert runs["every"] == 2  # the next slot counts from this run
    clock[0] += timedelta(seconds=300)
    await tick()
    assert runs["every"] == 3


async def test_daily_after_downtime_runs_once(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    clock[0] = datetime(2026, 10, 10, 4, 0)
    await tick()
    clock[0] = datetime(2026, 10, 20, 9, 0)  # ten missed days
    await tick()
    await tick()
    assert runs["daily"] == 1
    clock[0] = datetime(2026, 10, 21, 3, 0)
    await tick()
    assert runs["daily"] == 2


async def test_slow_task_does_not_hold_back_others_and_tick_ends_at_timeout(  # type: ignore[no-untyped-def]
    f, reg, runs, clock, tick
) -> None:
    loop = asyncio.get_running_loop()
    began = loop.time()
    await tick()
    assert runs["every"] == 1 and runs["bad"] == 1 and runs["slow"] == 1
    assert loop.time() - began < 2.5  # slow sleeps 3 s, cut at its 1 s timeout


async def test_never_more_than_four_at_once_and_budget_leaves_rest_due(  # type: ignore[no-untyped-def]
    f, clock
) -> None:
    active = 0
    peak = 0
    runs: list[str] = []

    def make(name: str) -> Callable[[TaskContext], Awaitable[None]]:
        async def run(ctx: TaskContext) -> None:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            runs.append(name)

        return run

    registry = Registry()
    for i in range(7):
        registry.add(
            SCHEDULED_TASKS,
            _task(f"t{i}", make(f"t{i}"), every_seconds=60),
            owner=PLUGIN,
        )
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        res = await run_plugin_tasks_tick(
            registry=registry, session_factory=f, now=lambda: clock[0]
        )
        assert peak == MAX_PARALLEL_TASKS == 4
        assert res["ran"] == 7 and len(runs) == 7

        # budget used up: tasks that would have to wait for a slot stay due
        runs.clear()
        clock[0] += timedelta(seconds=60)
        ticks = iter(range(1000))
        res = await run_plugin_tasks_tick(
            registry=registry,
            session_factory=f,
            now=lambda: clock[0],
            wall_clock=lambda: 100.0 * (next(ticks) > 0),  # start 0, then always 100
            budget_seconds=30,
        )
        assert res["ran"] == 0 and runs == []
        hooks = await _hooks(f)
        assert all(h.last_run_at == START for h in hooks.values())  # not marked as run
        res = await run_plugin_tasks_tick(
            registry=registry, session_factory=f, now=lambda: clock[0]
        )
        assert res["ran"] == 7
    finally:
        set_loaded_plugins([])


async def test_stop_event_starts_nothing(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    stop = asyncio.Event()
    stop.set()
    res = await tick(stop=stop)
    assert res["ran"] == 0 and sum(runs.values()) == 0


async def test_inactive_plugin_tasks_do_not_run(f, reg, runs, clock, tick) -> None:  # type: ignore[no-untyped-def]
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.FAILED, "x", ())])
    res = await tick()
    assert res["tasks"] == 0 and sum(runs.values()) == 0
    assert await _hooks(f) == {}


async def test_live_registry_swap_is_seen(f, clock, runs, tick) -> None:  # type: ignore[no-untyped-def]
    registry = Registry()  # core-only at first (late boot retry swaps the content in place)
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        assert (await tick(registry=registry))["tasks"] == 0
        seen: list[int] = []

        async def late(ctx: TaskContext) -> None:
            seen.append(1)

        full = Registry()
        full.add(SCHEDULED_TASKS, _task("late", late, every_seconds=60), owner=PLUGIN)
        full.freeze()
        registry.replace_contents(full)
        res = await tick(registry=registry)
        assert res["tasks"] == 1 and res["ran"] == 1 and seen == [1]
    finally:
        set_loaded_plugins([])


async def test_system_exit_in_a_task_is_a_failure(f, clock, tick) -> None:  # type: ignore[no-untyped-def]
    async def leave(ctx: TaskContext) -> None:
        raise SystemExit(3)

    registry = Registry()
    registry.add(SCHEDULED_TASKS, _task("leave", leave, every_seconds=60), owner=PLUGIN)
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        res = await tick(registry=registry)
        assert res["failed"] == 1
        hook = (await _hooks(f))["task:org.example.plg49b:leave"]
        assert (hook.last_error or "").startswith("SystemExit")
    finally:
        set_loaded_plugins([])


async def test_cancelled_error_raised_by_a_task_is_a_failure(f, clock, tick) -> None:  # type: ignore[no-untyped-def]
    """A plugin raising CancelledError outside a shutdown must not end the worker (B-I1)."""
    seen: list[int] = []

    async def cancels(ctx: TaskContext) -> None:
        raise asyncio.CancelledError

    async def other(ctx: TaskContext) -> None:
        seen.append(1)

    registry = Registry()
    registry.add(SCHEDULED_TASKS, _task("cancels", cancels, every_seconds=60), owner=PLUGIN)
    registry.add(SCHEDULED_TASKS, _task("other", other, every_seconds=60), owner=PLUGIN)
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        res = await tick(registry=registry)
        assert res["ran"] == 2 and res["failed"] == 1 and seen == [1]
        hook = (await _hooks(f))["task:org.example.plg49b:cancels"]
        assert hook.consecutive_failures == 1
        assert (hook.last_error or "").startswith("CancelledError")
    finally:
        set_loaded_plugins([])


async def test_real_cancellation_of_the_task_tick_still_propagates(f, clock, tick) -> None:  # type: ignore[no-untyped-def]
    async def sleeper(ctx: TaskContext) -> None:
        await asyncio.sleep(5)

    registry = Registry()
    registry.add(SCHEDULED_TASKS, _task("sleeper", sleeper, every_seconds=60), owner=PLUGIN)
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    try:
        running = asyncio.create_task(tick(registry=registry))
        await asyncio.sleep(0.3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        hook = (await _hooks(f))["task:org.example.plg49b:sleeper"]
        assert hook.consecutive_failures == 0
    finally:
        set_loaded_plugins([])


def test_constants_and_daemon_row() -> None:
    assert MAX_PARALLEL_TASKS == 4 and TRIP_AFTER == 5
    row = DAEMON_SERVICES_BY_SLUG["plugin_tasks"]
    assert row.interval_settings_attr == "plugin_tasks_interval_seconds"
    assert row.tick_timeout_seconds >= 300 + 30


def test_daemon_catalog_and_loop_wiring() -> None:
    assert (
        DAEMON_SERVICES_BY_SLUG["plugin_tasks"].interval_settings_attr
        == "plugin_tasks_interval_seconds"
    )
    source = inspect.getsource(worker_main._run_all_loops)
    assert '"plugin_tasks"' in source and "stop=stop" in source
