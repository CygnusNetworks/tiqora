"""Event dispatcher: ticket_history -> plugin subscribers, cursor, circuit breaker (both dialects).

Seed band 49400-49499: tickets 49400/49401 (title ``plg49 events``), ``ticket_history`` ids
49400-49440 (seeded and deleted per test), hook rows ``event:org.example.plg49%``. Handlers record
only events of the two seeded tickets, so rows other modules leave above the band never count.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.worker.__main__ as worker_main
from tests._plugins_state_helpers import to_async_url
from tiqora.config import Settings
from tiqora.db.id_step import autoincrement_step
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.plugins import event_dispatch
from tiqora.plugins.event_dispatch import (
    EVENT_BATCH,
    MIN_RUN_BUDGET_SECONDS,
    TRIP_AFTER,
    run_plugin_events_tick,
)
from tiqora.plugins.events import (
    EVENT_SUBSCRIBERS,
    EventContext,
    EventSubscriber,
    GapTracker,
    TicketEvent,
)
from tiqora.plugins.registrar import MAX_TIMEOUT_SECONDS
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.plugins.store import ensure_hook, load_hooks, reset_plugin
from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG

pytestmark = pytest.mark.db

PLUGIN = "org.example.plg49a"
GOOD = f"event:{PLUGIN}:good"
FLAKY = f"event:{PLUGIN}:flaky"
TICKETS = (49400, 49401)
NOW = datetime(2026, 10, 10, 12, 0, 0)

Got = list[tuple[str, int, str]]


def _clean(conn: Any) -> None:
    conn.execute(text("DELETE FROM ticket_history WHERE id BETWEEN 49400 AND 49440"))
    conn.execute(text("DELETE FROM ticket_history WHERE ticket_id IN (49400, 49401)"))
    conn.execute(text("DELETE FROM ticket WHERE id IN (49400, 49401)"))
    conn.execute(
        delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id.like("org.example.plg49%"))
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean(conn)
        for tid in TICKETS:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'plg49 events', 1, 1, 1, 1, 1, 3, 1,"
                    " 'plg49-cust', 'plg49.cust@example.org',"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"plg49{tid}", "t": NOW},
            )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()


def _hist(sync_url: str, id_: int, htype: str, ticket: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        type_id = conn.execute(
            text("SELECT id FROM ticket_history_type WHERE name = :n"), {"n": htype}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ticket_history (id, name, history_type_id, ticket_id, article_id,"
                " type_id, queue_id, owner_id, priority_id, state_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :ht, :tid, NULL, 1, 1, 1, 3, 1, :t, 1, :t, 1)"
            ),
            {"id": id_, "name": f"%%plg49 {htype}", "ht": type_id, "tid": ticket, "t": NOW},
        )
    engine.dispose()


async def _hooks(f: async_sessionmaker[AsyncSession]) -> dict[str, TiqoraPluginHook]:
    async with f() as session:
        return await load_hooks(session, plugin_id=PLUGIN)


def _ids(got: Got, name: str) -> list[int]:
    return [i for k, i, _e in got if k.endswith(f":{name}")]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url(request: pytest.FixtureRequest) -> Iterator[str]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    try:
        yield sync_url
    finally:
        _teardown(sync_url)


@pytest.fixture
async def f(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def got() -> Got:
    return []


@pytest.fixture
def fail_on() -> set[int]:
    return set()


@pytest.fixture
def slow() -> list[float]:
    return [0.0]


@pytest.fixture
def fail_with() -> list[type[BaseException]]:
    return [RuntimeError]


@pytest.fixture
def on_good() -> list[Callable[[TicketEvent], Awaitable[None]]]:
    """Optional extra step the ``good`` handler runs after recording (test probes)."""
    return []


@pytest.fixture
async def reg(
    f: async_sessionmaker[AsyncSession],
    got: Got,
    fail_on: set[int],
    slow: list[float],
    fail_with: list[type[BaseException]],
    on_good: list[Callable[[TicketEvent], Awaitable[None]]],
) -> AsyncIterator[Registry]:
    async def good(ctx: EventContext, event: TicketEvent) -> None:
        assert ctx.plugin.plugin_id == PLUGIN
        if event.ticket_id in TICKETS:
            got.append((f"{PLUGIN}:good", event.id, event.event))
            for step in on_good:
                await step(event)

    async def flaky(ctx: EventContext, event: TicketEvent) -> None:
        if event.ticket_id not in TICKETS:
            return
        await asyncio.sleep(slow[0])
        if event.id in fail_on:
            raise fail_with[0](f"kaputt bei {event.id}")
        got.append((f"{PLUGIN}:flaky", event.id, event.event))

    registry = Registry()
    registry.add(
        EVENT_SUBSCRIBERS,
        EventSubscriber(
            key=f"{PLUGIN}:good",
            plugin_id=PLUGIN,
            events=frozenset({"TicketCreate", "TicketStateUpdate"}),
            handler=good,
            timeout_seconds=5,
        ),
        owner=PLUGIN,
    )
    registry.add(
        EVENT_SUBSCRIBERS,
        EventSubscriber(
            key=f"{PLUGIN}:flaky",
            plugin_id=PLUGIN,
            events=frozenset({"*"}),
            handler=flaky,
            timeout_seconds=1,
        ),
        owner=PLUGIN,
    )
    registry.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.ACTIVE, None, ())])
    async with f() as session:
        for key in (GOOD, FLAKY):
            await ensure_hook(
                session, hook_key=key, plugin_id=PLUGIN, kind="event", initial_cursor=49399, now=NOW
            )
        await session.commit()
    try:
        yield registry
    finally:
        set_loaded_plugins([])


Tick = Callable[..., Awaitable[dict[str, int]]]


@pytest.fixture
def tick(reg: Registry, f: async_sessionmaker[AsyncSession]) -> Tick:
    async def _tick(**kw: Any) -> dict[str, int]:
        tracker = kw.pop("tracker", None) or GapTracker(hold_seconds=60)
        return await run_plugin_events_tick(
            registry=reg,
            settings=Settings(environment="test"),
            session_factory=f,
            tracker=tracker,
            **kw,
        )

    return _tick


async def test_delivers_in_order_to_matching_subscribers(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "OwnerUpdate", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    res = await tick()
    assert [(i, e) for k, i, e in got if k.endswith(":good")] == [
        (49400, "TicketCreate"),
        (49402, "TicketStateUpdate"),
    ]
    assert [(i, e) for k, i, e in got if k.endswith(":flaky")] == [
        (49400, "TicketCreate"),
        (49401, "TicketOwnerUpdate"),
        (49402, "TicketStateUpdate"),
    ]
    hooks = await _hooks(f)
    assert hooks[GOOD].cursor is not None and hooks[GOOD].cursor >= 49402
    assert hooks[GOOD].last_ok_at is not None
    assert res["subscribers"] == 2 and res["failed"] == 0
    assert res["events"] >= 5 and res["horizon"] >= 49402


async def test_failing_subscriber_trips_others_continue(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "StateUpdate", 49400)
    fail_on.add(49401)
    results = [await tick() for _ in range(TRIP_AFTER)]
    assert [r["tripped"] for r in results] == [0, 0, 0, 0, 1]
    assert all(r["failed"] == 1 for r in results)
    flaky = (await _hooks(f))[FLAKY]
    assert flaky.tripped_at is not None
    assert flaky.consecutive_failures == 5 and flaky.total_failures == 5
    assert flaky.cursor == 49400 and "kaputt bei 49401" in (flaky.last_error or "")
    assert (flaky.last_error or "").startswith("RuntimeError: ")
    _hist(url, 49402, "StateUpdate", 49401)
    await tick()
    assert (49402, "TicketStateUpdate") in [(i, e) for k, i, e in got if k.endswith(":good")]
    assert 49402 not in _ids(got, "flaky")  # stopped


async def test_reset_resumes_at_failed_event(url, f, reg, got, fail_on, slow, tick) -> None:
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "StateUpdate", 49400)
    fail_on.add(49401)
    for _ in range(TRIP_AFTER):
        await tick()
    async with f() as session:
        await reset_plugin(session, PLUGIN, now=utc_now())
        await session.commit()
    fail_on.clear()
    await tick()
    assert _ids(got, "flaky") == [49400, 49401]  # failed event delivered, once


async def test_failed_third_event_does_not_redeliver_first_two(
    url, f, reg, got, fail_on, slow, tick, on_good
) -> None:
    """R-ATLEASTONCE: the cursor is persisted after every handled matching event."""
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "StateUpdate", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    seen: list[int | None] = []

    async def probe(event: TicketEvent) -> None:  # cursor as committed while the run goes on
        if event.id == 49402:
            seen.append((await _hooks(f))[GOOD].cursor)
            raise RuntimeError("kaputt beim dritten")

    on_good.append(probe)
    await tick()
    assert seen == [49401]  # committed before the third handler call started
    good = (await _hooks(f))[GOOD]
    assert good.cursor == 49401 and good.consecutive_failures == 1
    on_good.clear()
    await tick()
    assert _ids(got, "good") == [49400, 49401, 49402, 49402]  # only the failed one again
    assert (await _hooks(f))[GOOD].consecutive_failures == 0


async def test_timeout_counts_as_failure(url, f, reg, got, fail_on, slow, tick) -> None:
    slow[0] = 3.0
    _hist(url, 49400, "NewTicket", 49400)
    await tick()
    flaky = (await _hooks(f))[FLAKY]
    assert flaky.consecutive_failures == 1
    assert flaky.last_error == "Zeitlimit von 1 Sekunden überschritten"
    assert flaky.cursor == 49399


async def test_system_exit_in_handler_is_a_failure_not_a_crash(
    url, f, reg, got, fail_on, slow, fail_with, tick
) -> None:
    fail_with[0] = SystemExit
    fail_on.add(49400)
    _hist(url, 49400, "NewTicket", 49400)
    res = await tick()
    flaky = (await _hooks(f))[FLAKY]
    assert res["failed"] == 1 and flaky.consecutive_failures == 1
    assert (flaky.last_error or "").startswith("SystemExit: ")
    assert _ids(got, "good") == [49400]


async def test_new_subscriber_starts_at_max_history_id(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    async with f() as session:
        await session.execute(delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == PLUGIN))
        await session.commit()
    _hist(url, 49400, "NewTicket", 49400)
    await tick()
    hooks = await _hooks(f)
    assert hooks[GOOD].cursor is not None and hooks[GOOD].cursor >= 49400 and got == []


async def test_reenabled_subscriber_skips_the_backlog(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    """R-REENABLE-BACKLOG: a NULL cursor restarts at the current end of the history."""
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "StateUpdate", 49400)
    async with f() as session:
        await session.execute(
            update(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == PLUGIN).values(cursor=None)
        )
        await session.commit()
    await tick()
    hooks = await _hooks(f)
    assert got == []
    assert hooks[GOOD].cursor is not None and hooks[GOOD].cursor >= 49401
    _hist(url, 49440, "StateUpdate", 49401)  # ids up to the next one are missing: held back
    gap_free_tracker = GapTracker(hold_seconds=0)
    await tick(tracker=gap_free_tracker)
    assert _ids(got, "good") == [49440]


async def test_late_commit_within_hold_delivered_in_order(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    clock = [0.0]
    tracker = GapTracker(hold_seconds=60)
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert _ids(got, "flaky") == [49400]
    _hist(url, 49401, "OwnerUpdate", 49400)  # the late commit
    clock[0] = 5.0
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert _ids(got, "flaky") == [49400, 49401, 49402]


async def test_inactive_plugin_subscribers_skipped(url, f, reg, got, fail_on, slow, tick) -> None:
    set_loaded_plugins([PluginLoadResult(PLUGIN, "1.0.0", RuntimeState.FAILED, "x", ())])
    _hist(url, 49400, "NewTicket", 49400)
    res = await tick()
    assert got == [] and res["subscribers"] == 0
    assert (await _hooks(f))[GOOD].cursor == 49399


async def test_run_budget_stops_starting_handlers(
    url, f, reg, got, fail_on, slow, tick, on_good
) -> None:
    """R-HEAD-OF-LINE: after max(interval x 2, 30 s) no new handler call starts this tick."""
    clock = [0.0]

    async def slow_handler(event: TicketEvent) -> None:
        clock[0] += MIN_RUN_BUDGET_SECONDS + 1

    on_good.append(slow_handler)
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "OwnerUpdate", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    await tick(wall_clock=lambda: clock[0])
    good = (await _hooks(f))[GOOD]
    assert _ids(got, "good") == [49400]
    assert good.cursor == 49401 and good.consecutive_failures == 0  # not a failure
    await tick(wall_clock=lambda: clock[0])
    assert _ids(got, "good") == [49400, 49402]


async def test_stop_event_stops_starting_handlers(
    url, f, reg, got, fail_on, slow, tick, on_good
) -> None:
    stop = asyncio.Event()

    async def stop_now(event: TicketEvent) -> None:
        stop.set()

    on_good.append(stop_now)
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49401, "StateUpdate", 49400)
    await tick(stop=stop)
    good = (await _hooks(f))[GOOD]
    assert _ids(got, "good") == [49400] and good.cursor == 49400
    on_good.clear()
    await tick()
    assert _ids(got, "good") == [49400, 49401]


async def test_reads_subscribers_from_the_live_registry(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    """A late boot retry swaps the registry contents in place; the next tick must see it."""
    only_good = Registry()
    only_good.add(EVENT_SUBSCRIBERS, reg.require(EVENT_SUBSCRIBERS, f"{PLUGIN}:good"), owner=PLUGIN)
    reg.replace_contents(only_good)
    _hist(url, 49400, "NewTicket", 49400)
    res = await tick()
    assert res["subscribers"] == 1
    assert _ids(got, "good") == [49400] and _ids(got, "flaky") == []


async def test_without_tracker_uses_a_process_tracker_with_step_and_offset(
    url, f, reg, got, monkeypatch
) -> None:
    monkeypatch.setattr(event_dispatch, "_TRACKER", None)
    _hist(url, 49400, "NewTicket", 49400)
    await run_plugin_events_tick(
        registry=reg, settings=Settings(environment="test"), session_factory=f
    )
    tracker = event_dispatch._TRACKER
    assert tracker is not None
    step, offset = await autoincrement_step(f)
    assert (tracker._step, tracker._offset) == (step, offset)
    assert _ids(got, "good") == [49400]


async def _set_hook(f: async_sessionmaker[AsyncSession], key: str, **values: Any) -> None:
    async with f() as session:
        await session.execute(
            update(TiqoraPluginHook).where(TiqoraPluginHook.hook_key == key).values(**values)
        )
        await session.commit()


async def test_lagging_subscriber_does_not_hold_back_others(
    url, f, reg, got, fail_on, slow, tick, monkeypatch
) -> None:
    """R-DISPATCH-FRONTIER: each subscriber reads its own range.

    ``flaky`` is 5,000 ids behind; the band has no room for 5,000 real rows, so the batch is
    shrunk to 2 rows instead: a shared window from the smallest cursor would hand ``good``
    nothing this tick.
    """
    monkeypatch.setattr(event_dispatch, "EVENT_BATCH", 2)
    _hist(url, 49400, "NewTicket", 49400)
    for id_ in (49401, 49402, 49403):
        _hist(url, id_, "OwnerUpdate", 49400)
    _hist(url, 49404, "StateUpdate", 49400)
    await _set_hook(f, GOOD, cursor=49403)
    await _set_hook(f, FLAKY, cursor=44399)
    await tick()
    assert _ids(got, "good") == [49404]
    assert (await _hooks(f))[GOOD].cursor == 49404


async def test_reset_replays_old_holes_without_waiting(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    """Holes at or below the frontier are final: a reset subscriber's replay never waits."""
    clock = [0.0]
    tracker = GapTracker(hold_seconds=60)
    ids = list(range(49400, 49420, 2))  # 10 rows, 9 holes
    _hist(url, ids[0], "NewTicket", 49400)
    for id_ in ids[1:]:
        _hist(url, id_, "StateUpdate", 49400)
    await _set_hook(f, FLAKY, tripped_at=NOW, consecutive_failures=TRIP_AFTER)
    for _ in range(len(ids)):  # each new hole above the frontier is held once
        await tick(tracker=tracker, wall_clock=lambda: clock[0])
        clock[0] += 60
    assert _ids(got, "good") == ids and _ids(got, "flaky") == []
    async with f() as session:
        await reset_plugin(session, PLUGIN, now=utc_now())
        await session.commit()
    res = await tick(tracker=tracker, wall_clock=lambda: clock[0])  # clock does not move
    assert _ids(got, "flaky") == ids and res["failed"] == 0


async def test_skipped_hole_is_never_held_again(url, f, reg, got, fail_on, slow, tick) -> None:
    clock = [0.0]
    tracker = GapTracker(hold_seconds=60)
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    clock[0] = 60.0
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert _ids(got, "good") == [49400, 49402]
    await _set_hook(f, GOOD, cursor=49399)  # replay right after the skip
    clock[0] = 61.0
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert _ids(got, "good") == [49400, 49402, 49400, 49402]


async def test_new_hole_above_frontier_holds_up_to_hold_seconds(
    url, f, reg, got, fail_on, slow, tick
) -> None:
    clock = [0.0]
    tracker = GapTracker(hold_seconds=60)
    _hist(url, 49400, "NewTicket", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    for at in (0.0, 30.0, 59.0):
        clock[0] = at
        res = await tick(tracker=tracker, wall_clock=lambda: clock[0])
        assert _ids(got, "good") == [49400] and res["horizon"] == 49400
    clock[0] = 60.0
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert _ids(got, "good") == [49400, 49402]


async def test_batch_size_is_bounded() -> None:
    assert EVENT_BATCH == 500 and TRIP_AFTER == 5


def test_daemon_catalog_and_loop_wiring() -> None:
    svc = DAEMON_SERVICES_BY_SLUG["plugin_events"]
    assert svc.interval_settings_attr == "plugin_events_interval_seconds"
    assert svc.tick_timeout_seconds == MIN_RUN_BUDGET_SECONDS + MAX_TIMEOUT_SECONDS
    source = inspect.getsource(worker_main._run_all_loops)
    assert '"plugin_events"' in source and "stop=stop" in source
