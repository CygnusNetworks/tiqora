"""Plugin state store (both dialects): sync, boot counters, enable switch, reset.

Seed plugin ids are ``org.example.plg49a`` ... (band 49300-49699 for this plan); the
fixture in ``_plugins_state_helpers`` deletes them again.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import (  # noqa: F401
    T0,
    T1,
    db_url_fixture,
    discovered,
    factory_fixture,
    hook_row,
    runtime_rows,
    seed_plugin_row,
    session_fixture,
)
from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.plugins import store
from tiqora.plugins.state import RuntimeState
from tiqora.plugins.store import (
    BootInfo,
    list_plugin_rows,
    list_runtime_rows,
    mark_loading,
    mark_result,
    prepare_boot,
    reset_plugin,
    set_enabled,
    sync_discovered,
)

pytestmark = pytest.mark.db


async def test_sync_inserts_with_auto_enable_and_keeps_enabled(session: AsyncSession) -> None:
    plugins = [discovered("org.example.plg49a", version="1.0.0")]
    rows = await sync_discovered(session, plugins, auto_enable=True, now=T0)
    assert rows["org.example.plg49a"].enabled is True
    assert rows["org.example.plg49a"].source == "image"
    assert rows["org.example.plg49a"].installed_at == T0
    await set_enabled(session, "org.example.plg49a", False, by=1, now=T0)
    rows = await sync_discovered(
        session, [discovered("org.example.plg49a", version="1.1.0")], auto_enable=True, now=T1
    )
    assert rows["org.example.plg49a"].enabled is False  # admin choice survives
    assert rows["org.example.plg49a"].version == "1.1.0"
    assert rows["org.example.plg49a"].updated_at == T1
    assert rows["org.example.plg49a"].installed_at == T0


async def test_auto_enable_off(session: AsyncSession) -> None:
    rows = await sync_discovered(
        session, [discovered("org.example.plg49b")], auto_enable=False, now=T0
    )
    assert rows["org.example.plg49b"].enabled is False


async def test_sync_uses_dist_name_without_manifest(session: AsyncSession) -> None:
    import dataclasses

    broken = dataclasses.replace(
        discovered("org.example.plg49e"),
        manifest=None,
        dist_name="plg-dist",
        dist_version="2.0",
        problem="kaputt",
    )
    rows = await sync_discovered(session, [broken], auto_enable=True, now=T0)
    assert (rows["org.example.plg49e"].name, rows["org.example.plg49e"].version) == (
        "plg-dist",
        "2.0",
    )


async def test_sync_survives_a_concurrent_insert(
    factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    """Another boot inserts the row between our SELECT and INSERT: roll back and re-read."""
    real = session.flush
    calls = 0

    async def racing_flush(*a: object, **kw: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await seed_plugin_row(factory, "org.example.plg49f", enabled=False)
        await real(*a, **kw)  # type: ignore[arg-type]

    session.flush = racing_flush  # type: ignore[method-assign]
    rows = await sync_discovered(
        session, [discovered("org.example.plg49f", version="3.0.0")], auto_enable=True, now=T1
    )
    assert calls == 2
    assert rows["org.example.plg49f"].enabled is False  # the other boot's row wins
    assert rows["org.example.plg49f"].version == "3.0.0"


async def test_mark_loading_counts_and_result_resets(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await mark_loading(factory, "org.example.plg49c", "api", "1.0.0", T0) == 1
    assert await mark_loading(factory, "org.example.plg49c", "api", "1.0.0", T0) == 2
    (row,) = await runtime_rows(factory, "org.example.plg49c")
    assert (row.state, row.boot_attempts) == ("loading", 2)
    await mark_result(
        factory,
        "org.example.plg49c",
        "api",
        state=RuntimeState.ACTIVE,
        version="1.0.0",
        error=None,
        now=T1,
    )
    (row,) = await runtime_rows(factory, "org.example.plg49c")
    assert (row.state, row.boot_attempts, row.updated_at) == ("active", 0, T1)


async def test_quarantined_result_keeps_the_counter(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(3):
        await mark_loading(factory, "org.example.plg49g", "worker", "1", T0)
    await mark_result(
        factory,
        "org.example.plg49g",
        "worker",
        state=RuntimeState.QUARANTINED,
        version="1",
        error="x" * 3000,
        now=T1,
    )
    (row,) = await runtime_rows(factory, "org.example.plg49g")
    assert (row.state, row.boot_attempts) == ("quarantined", 3)
    assert row.error is not None and len(row.error) == 2000


async def test_runtime_rows_are_per_role(factory: async_sessionmaker[AsyncSession]) -> None:
    await mark_loading(factory, "org.example.plg49h", "api", "1", T0)
    await mark_loading(factory, "org.example.plg49h", "worker", "1", T0)
    await mark_loading(factory, "org.example.plg49h", "worker", "1", T0)
    async with factory() as s:
        rows = await list_runtime_rows(s, ["org.example.plg49h"])
        assert [(r.role, r.boot_attempts) for r in rows] == [("api", 1), ("worker", 2)]
        assert await list_runtime_rows(s, []) == []


async def test_prepare_boot_returns_role_counters(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await mark_loading(factory, "org.example.plg49i", "api", "1.0.0", T0)
    await mark_loading(factory, "org.example.plg49i", "api", "1.0.0", T0)
    plugins = [discovered("org.example.plg49i")]
    api = await prepare_boot(factory, plugins, role="api", auto_enable=True)
    worker = await prepare_boot(factory, plugins, role="worker", auto_enable=True)
    assert api == {"org.example.plg49i": BootInfo(enabled=True, boot_attempts=2)}
    assert worker == {"org.example.plg49i": BootInfo(enabled=True, boot_attempts=0)}
    async with factory() as s:
        assert "org.example.plg49i" in await list_plugin_rows(s)


async def test_prepare_boot_retries_on_any_error_then_raises(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    calls = 0

    async def flaky(*a: object, **kw: object) -> dict[str, BootInfo]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("Tabelle fehlt")  # not an OperationalError: still retried
        return {"x": BootInfo(True, 0)}

    monkeypatch.setattr(store, "_prepare_once", flaky)
    got = await prepare_boot(
        factory, [discovered("org.example.plg49j")], role="api", auto_enable=True, delay_seconds=0
    )
    assert got == {"x": BootInfo(True, 0)} and calls == 3

    calls = -10
    with pytest.raises(RuntimeError):
        await prepare_boot(
            factory,
            [discovered("org.example.plg49j")],
            role="api",
            auto_enable=True,
            attempts=2,
            delay_seconds=0,
        )
    assert calls == -8


async def test_prepare_boot_against_a_dead_database_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "BOOT_RETRY_DELAY_SECONDS", 0.0)
    dead = async_sessionmaker(create_async_engine("mysql+aiomysql://u:p@127.0.0.1:1/x"))
    with pytest.raises(OperationalError):
        await prepare_boot(dead, [discovered("org.example.plg49k")], role="api", auto_enable=True)


async def test_set_enabled_unknown_plugin_and_noop(session: AsyncSession) -> None:
    assert await set_enabled(session, "org.example.plg49l", True, by=1, now=T1) is None
    await sync_discovered(session, [discovered("org.example.plg49l")], auto_enable=True, now=T0)
    row = await set_enabled(session, "org.example.plg49l", True, by=7, now=T1)
    assert row is not None and row.updated_at == T0 and row.changed_by is None  # unchanged
    row = await set_enabled(session, "org.example.plg49l", False, by=7, now=T1)
    assert row is not None and (row.enabled, row.changed_by, row.updated_at) == (False, 7, T1)


async def test_re_enable_clears_event_cursors_only(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """R-REENABLE-BACKLOG: off -> on restarts event hooks at 'now', tasks keep theirs."""
    pid = "org.example.plg49m"
    async with factory() as s:
        await sync_discovered(s, [discovered(pid)], auto_enable=True, now=T0)
        for key, kind in ((f"event:{pid}:n", "event"), (f"task:{pid}:t", "task")):
            s.add(TiqoraPluginHook(hook_key=key, plugin_id=pid, kind=kind, cursor=9, created_at=T0))
        await s.commit()
    async with factory() as s:
        await set_enabled(s, pid, False, by=1, now=T0)
        await s.commit()
    assert (await hook_row(factory, f"event:{pid}:n")).cursor == 9  # switching off keeps it
    async with factory() as s:
        await set_enabled(s, pid, True, by=1, now=T1)
        await s.commit()
    assert (await hook_row(factory, f"event:{pid}:n")).cursor is None
    assert (await hook_row(factory, f"task:{pid}:t")).cursor == 9


async def test_reset_keeps_cursors_on_retry(factory: async_sessionmaker[AsyncSession]) -> None:
    """FAILED/QUARANTINED -> 'try again' is not an off/on switch: cursors stay."""
    pid = "org.example.plg49n"
    async with factory() as s:
        await sync_discovered(s, [discovered(pid)], auto_enable=True, now=T0)
        s.add(
            TiqoraPluginHook(
                hook_key=f"event:{pid}:n", plugin_id=pid, kind="event", cursor=4, created_at=T0
            )
        )
        await s.commit()
    async with factory() as s:
        await reset_plugin(s, pid, now=T1)
        await s.commit()
    assert (await hook_row(factory, f"event:{pid}:n")).cursor == 4


async def test_reset_plugin(factory: async_sessionmaker[AsyncSession]) -> None:
    await mark_result(
        factory,
        "org.example.plg49d",
        "worker",
        state=RuntimeState.QUARANTINED,
        version="1",
        error="x",
        now=T0,
    )
    await mark_result(
        factory,
        "org.example.plg49d",
        "api",
        state=RuntimeState.FAILED,
        version="1",
        error="y",
        now=T0,
    )
    async with factory() as s:
        s.add(
            TiqoraPluginHook(
                hook_key="event:org.example.plg49d:n",
                plugin_id="org.example.plg49d",
                kind="event",
                cursor=5,
                consecutive_failures=5,
                total_failures=7,
                tripped_at=T0,
                created_at=T0,
            )
        )
        await s.commit()
    async with factory() as s:
        await reset_plugin(s, "org.example.plg49d", now=T1)
        await s.commit()
    (worker,) = await runtime_rows(factory, "org.example.plg49d", "worker")
    assert (worker.state, worker.boot_attempts, worker.updated_at) == ("disabled", 0, T1)
    (api,) = await runtime_rows(factory, "org.example.plg49d", "api")
    assert api.state == "failed"  # only QUARANTINED turns into DISABLED
    hook = await hook_row(factory, "event:org.example.plg49d:n")
    assert hook.tripped_at is None and hook.consecutive_failures == 0
    assert hook.cursor == 5 and hook.total_failures == 7
