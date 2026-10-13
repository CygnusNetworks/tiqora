"""Sync-hook breaker state in ``tiqora_plugin_hook`` (both dialects).

No seed ids: all rows belong to the points fixture plugin and are removed by ``plugin_id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._plugin_points import PLUGIN_ID, boot_points_plugin, points_behaviour
from tests._plugins_state_helpers import (  # noqa: F401
    T0,
    db_url_fixture,
    factory_fixture,
    hook_row,
    settings_fixture,
)
from tests.plugins.helpers import Installer
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginHook, TiqoraPluginRuntime
from tiqora.extensions import set_registry_for_tests
from tiqora.plugins import sync_hooks
from tiqora.plugins.bootstrap import init_registry
from tiqora.plugins.state import set_loaded_plugins
from tiqora.plugins.store import ensure_hook, reset_plugin
from tiqora.plugins.sync_hooks import SyncBreaker, TicketCreateOp, hook_key, run_sync_hooks

pytestmark = pytest.mark.db

OPERATIONS = (
    "article.create",
    "ticket.create",
    "ticket.dynamic_fields_update",
    "ticket.queue_move",
    "ticket.state_change",
)


@pytest.fixture(autouse=True)
async def _own_rows(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    async def wipe() -> None:
        async with factory() as s:
            for model in (TiqoraPluginHook, TiqoraPluginRuntime):
                await s.execute(delete(model).where(model.plugin_id == PLUGIN_ID))
            await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id == PLUGIN_ID))
            await s.commit()

    await wipe()
    SyncBreaker.reset_for_tests()
    monkeypatch.setattr(sync_hooks, "get_session_factory", lambda: factory)
    yield
    await SyncBreaker.flush()
    SyncBreaker.reset_for_tests()
    set_registry_for_tests(None)
    set_loaded_plugins([])
    await wipe()


def _op() -> TicketCreateOp:
    return TicketCreateOp(
        queue_id=2,
        queue="Raw",
        title="Drucker defekt",
        state="new",
        priority="3 normal",
        type=None,
        customer_user=None,
        customer_id=None,
        dynamic_fields={},
        article=None,
    )


async def _rows(factory: async_sessionmaker[AsyncSession]) -> dict[str, TiqoraPluginHook]:
    async with factory() as s:
        found = (
            await s.execute(select(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == PLUGIN_ID))
        ).scalars()
        return {h.hook_key: h for h in found}


async def test_boot_creates_one_sync_row_per_hook(
    fixture_plugins: Installer,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    fixture_plugins("tq_fixture_points", plugin_id=PLUGIN_ID)
    on = settings.model_copy(update={"plugins_auto_enable": True})
    await init_registry(role="api", settings=on, session_factory=factory)
    await init_registry(role="worker", settings=on, session_factory=factory)  # idempotent
    rows = await _rows(factory)
    assert sorted(rows) == [f"sync:{PLUGIN_ID}:{op.replace('.', '_')}" for op in OPERATIONS]
    assert {h.kind for h in rows.values()} == {"sync"}
    assert {(h.tripped_at, h.cursor, h.consecutive_failures) for h in rows.values()} == {
        (None, None, 0)
    }


async def test_trip_is_persisted_and_reset_calls_hook_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    behaviour = points_behaviour()
    behaviour.MODE["hook.ticket.create"] = "raise"
    key = hook_key(
        [h for h in registry.items(sync_hooks.SYNC_HOOKS) if h.operation == "ticket.create"][0]
    )
    for _ in range(5):  # no session passed: the breaker writes with its own
        await run_sync_hooks("ticket.create", _op(), agent_id=1)
    await SyncBreaker.flush()
    row = await hook_row(factory, key)
    assert row.kind == "sync" and row.consecutive_failures == 5
    assert row.tripped_at is not None
    assert "RuntimeError: kaputt" in (row.last_error or "")
    assert SyncBreaker.is_tripped(key) and len(behaviour.CALLS) == 5

    await run_sync_hooks("ticket.create", _op(), agent_id=1)
    assert len(behaviour.CALLS) == 5  # tripped: not called

    async with factory() as s:  # admin: "Erneut versuchen"
        await reset_plugin(s, PLUGIN_ID, now=T0)
        await s.commit()
    assert SyncBreaker.is_tripped(key)  # not noticed before the next refresh
    monkeypatch.setattr(sync_hooks, "BREAKER_REFRESH_SECONDS", 0)
    behaviour.MODE["hook.ticket.create"] = "ok"
    await SyncBreaker.refresh_if_due()
    assert not SyncBreaker.is_tripped(key)
    await run_sync_hooks("ticket.create", _op(), agent_id=1)
    await SyncBreaker.flush()
    assert len(behaviour.CALLS) == 6
    row = await hook_row(factory, key)
    assert (row.tripped_at, row.consecutive_failures) == (None, 0)


async def test_trip_in_another_process_is_picked_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    hook = next(h for h in registry.items(sync_hooks.SYNC_HOOKS) if h.operation == "ticket.create")
    key = hook_key(hook)
    async with factory() as s:
        await ensure_hook(
            s, hook_key=key, plugin_id=PLUGIN_ID, kind="sync", initial_cursor=None, now=T0
        )
        await s.execute(
            update(TiqoraPluginHook).where(TiqoraPluginHook.hook_key == key).values(tripped_at=T0)
        )
        await s.commit()
    out = await run_sync_hooks("ticket.create", _op(), agent_id=1)
    assert out.title == "Drucker defekt"
    assert SyncBreaker.is_tripped(key)
    assert points_behaviour().CALLS == []
