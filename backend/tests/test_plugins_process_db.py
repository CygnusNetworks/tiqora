"""Process transition actions and condition modules from plugins (D5, both dialects).

Seed band 55080-55109: queue ``pp55-pq`` 55081 in group 55082, Text DF ``PP55Contract`` 55083
(no marker rows of its own: the DF values and tickets are removed with the row marks).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.plugins import process_points
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.writes import TicketDraft
from tiqora.process.actions.registry import ZNUNY_MODULE_PREFIX, resolve_action
from tiqora.process.condition_modules import ZNUNY_VALIDATION_PREFIX, resolve_condition_module
from tiqora.process.config import TransitionActionConfig
from tiqora.process.designer.catalog import build_designer_catalog
from tiqora.process.engine import execute_transition_action
from tiqora.process.exceptions import PluginActionTimedOut
from tiqora.znuny.sysconfig import SysConfig

QUEUE, GROUP, DF_ID = 55081, 55082, 55083
ACTION_MODULE = ZNUNY_MODULE_PREFIX + "Plugin_points_SetContract"
NOW = datetime(2026, 10, 12, 12, 0, 0)

MARK_TABLES: tuple[str, ...] = (
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean_seed(conn: Any) -> None:
    conn.execute(text(f"DELETE FROM dynamic_field_value WHERE field_id = {DF_ID}"))
    conn.execute(text(f"DELETE FROM dynamic_field WHERE id = {DF_ID}"))
    conn.execute(text(f"DELETE FROM queue WHERE id = {QUEUE}"))
    conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP}"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'pp55-pgrp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'pp55-pq', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 0, 'PP55Contract', 'PP55Contract', :id, 'Text', 'Ticket',"
                " '---', 1, :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "t": NOW},
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac

    def rows(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return list(conn.execute(text(sql), params).all())
        finally:
            engine.dispose()

    def contract(self, ticket_id: int) -> list[str]:
        return [
            r[0]
            for r in self.rows(
                "SELECT value_text FROM dynamic_field_value"
                " WHERE object_id = :t AND field_id = :f ORDER BY id",
                t=ticket_id,
                f=DF_ID,
            )
        ]

    def history_count(self, ticket_id: int) -> int:
        return int(
            self.rows("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = :t", t=ticket_id)[0][0]
        )


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    for mod in (
        "tiqora.plugins.writes",
        "tiqora.plugins.customers",
        "tiqora.plugins.context",
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        _clean_seed(conn)
    clean.dispose()


def _sysconfig(session: AsyncSession) -> SysConfig:
    async def fetch(name: str) -> Any:
        return None

    return SysConfig(session, fetch=fetch)


async def _ticket(title: str = "Vertrag") -> int:
    return await PluginTickets().create(TicketDraft(title=title, queue="pp55-pq", body="Hallo"))


async def _run(
    session: AsyncSession, ticket_id: int, config: dict[str, Any], module: str = ACTION_MODULE
) -> str | None:
    return await execute_transition_action(
        session,
        action=TransitionActionConfig(module=module, config=config),
        ticket_id=ticket_id,
        process_entity_id="Process-pp55",
        activity_entity_id="Activity-pp55",
        transition_entity_id="Transition-pp55",
        user_id=1,
        sysconfig=_sysconfig(session),
    )


def test_action_resolves_by_znuny_module_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    entry = resolve_action(ZNUNY_MODULE_PREFIX + "Plugin_points_SetContract")
    assert entry is not None
    assert entry.tiqora_only
    assert entry.name == "Plugin_points_SetContract"
    assert entry.run is not None


def test_action_rejects_core_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.registrar import RegistrationRejected
    from tiqora.process.actions.registry import TRANSITION_ACTIONS

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.loader import import_and_register

    registrar = import_and_register(discovered, make_plugin_context(discovered))

    async def run(ac: Any) -> None:
        return None

    # a core name is always staged qualified: it can never shadow TicketStateSet
    registrar.transition_action("TicketStateSet", run)
    staged = [i.name for p, i in registrar.staged if p is TRANSITION_ACTIONS]
    assert "Plugin_points_TicketStateSet" in staged
    assert "TicketStateSet" not in staged
    # a lower-case first letter is not a Znuny-shaped name
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        registrar.transition_action("ticketStateSet", run)
    with pytest.raises(RegistrationRejected, match="async"):
        registrar.transition_action("SyncOne", lambda ac: None)  # type: ignore[arg-type]
    with pytest.raises(RegistrationRejected, match="Zeitlimit"):
        registrar.transition_action("TooSlow", run, timeout_seconds=11)
    with pytest.raises(RegistrationRejected, match="Zeitlimit"):
        registrar.transition_action("TooFast", run, timeout_seconds=0)


async def test_contract_action_runs_in_engine_session(env: Env) -> None:
    tid = await _ticket()
    tn = env.rows("SELECT tn FROM ticket WHERE id = :t", t=tid)[0][0]
    async with env.factory() as session:
        skipped = await _run(session, tid, {"Contract": "V-<OTRS_TICKET_TicketNumber>"})
        assert skipped is None
        # not committed yet: another connection sees nothing
        assert env.contract(tid) == []
        await session.commit()
    assert env.contract(tid) == [f"V-{tn}"]
    assert [("action.SetContract", {"Contract": f"V-{tn}"})] == points_behaviour().CALLS


async def test_failing_action_rolls_back_savepoint_and_continues(env: Env) -> None:
    tid = await _ticket()
    points_behaviour().MODE["action"] = "raise"
    with capture_logs() as logs:
        async with env.factory() as session:
            # an earlier write of the same session, before the plugin action
            await PluginTickets().set_dynamic_field(
                tid, "PP55Contract", ["vorher"], session=session
            )
            result = await _run(session, tid, {"Contract": "nachher"})
            await session.commit()
    assert result is None  # no exception, the transition continues
    assert env.contract(tid) == ["vorher"]
    failed = [e for e in logs if e["event"] == "plugin_transition_action_failed"]
    assert len(failed) == 1
    assert failed[0]["plugin"] == "org.example.points"
    assert "RuntimeError" in failed[0]["error"]
    assert failed[0]["timed_out"] is False


async def test_hanging_actions_are_bounded_by_one_budget(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await _ticket()
    points_behaviour().MODE["action"] = "hang"
    monkeypatch.setattr(process_points, "ACTION_BUDGET_SECONDS", 0.3)
    started = time.monotonic()
    with capture_logs() as logs:
        async with env.factory() as session:
            with process_points.action_budget_scope():
                with pytest.raises(PluginActionTimedOut, match="hat zu lange gebraucht"):
                    await _run(session, tid, {"Contract": "a"})
                # the budget is spent: the second action is not even started
                with pytest.raises(PluginActionTimedOut, match="nichts wurde gespeichert"):
                    await _run(session, tid, {"Contract": "b"})
            await session.rollback()
    assert time.monotonic() - started < 1.0
    failed = [e for e in logs if e["event"] == "plugin_transition_action_failed"]
    assert [e["timed_out"] for e in failed] == [True, True]  # the hang, then the spent budget
    assert len(points_behaviour().CALLS) == 1  # the second action never ran
    assert env.contract(tid) == []


async def test_action_timeout_inside_a_db_call_leaves_a_usable_session(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await _ticket()
    points_behaviour().MODE["action"] = "db_hang"
    monkeypatch.setattr(process_points, "ACTION_BUDGET_SECONDS", 0.5)
    started = time.monotonic()
    async with env.factory() as session:
        # the route: engine call in a transaction block, then abort_on_action_timeout
        with pytest.raises(PluginActionTimedOut) as caught:
            async with session.begin():
                with process_points.action_budget_scope():
                    await _run(session, tid, {"Contract": "x"})
        http = await process_points.abort_on_action_timeout(session, caught.value)
        assert http.status_code == 422
        assert http.detail == (
            "Aktion der Erweiterung „Erweiterungspunkte-Test“ hat zu lange gebraucht; "
            "nichts wurde gespeichert"
        )
        # the session is still usable (new connection) and nothing was stored
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        await session.rollback()
    assert time.monotonic() - started < 4.0
    assert env.contract(tid) == []
    assert env.history_count(tid) >= 1  # the ticket itself (created earlier) is untouched


def test_condition_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    entry = resolve_condition_module(ZNUNY_VALIDATION_PREFIX + "Plugin_points_IsVip")
    assert entry is not None
    assert entry.tiqora_only
    assert entry.validate({"CustomerID": "pp55-vip"}, "CustomerID") is True
    assert entry.validate({"CustomerID": "other"}, "CustomerID") is False
    behaviour = points_behaviour()
    behaviour.MODE["cond"] = "raise"
    with capture_logs() as logs:
        assert entry.validate({"CustomerID": "pp55-vip"}, "CustomerID") is False
    assert [e["event"] for e in logs] == ["plugin_call_failed"]
    behaviour.MODE["cond"] = "nonbool"
    with capture_logs() as logs:
        assert entry.validate({"CustomerID": "pp55-vip"}, "CustomerID") is False
    assert "bool" in logs[0]["error"]


def test_condition_rejects_async_and_bad_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register
    from tiqora.plugins.registrar import RegistrationRejected

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    registrar = import_and_register(discovered, make_plugin_context(discovered))

    async def coro(data: Any, field: str) -> bool:
        return True

    with pytest.raises(RegistrationRejected, match="async def"):
        registrar.condition_module("IsAsync", coro)  # type: ignore[arg-type]
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        registrar.condition_module("isvip", lambda d, f: True)


def test_inactive_plugin_items_do_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.state import set_loaded_plugins

    boot_points_plugin(tmp_path, monkeypatch)
    entry = resolve_condition_module(ZNUNY_VALIDATION_PREFIX + "Plugin_points_IsVip")
    assert entry is not None
    set_loaded_plugins([])  # the plugin is not loaded in this process any more
    assert entry.validate({"CustomerID": "pp55-vip"}, "CustomerID") is False


async def test_designer_catalogue_lists_plugin_items(env: Env) -> None:
    async with env.factory() as session:
        catalog = await build_designer_catalog(session, _sysconfig(session))
    actions = {a.name: a for a in catalog.actions}
    assert "Plugin_points_SetContract" in actions
    assert actions["Plugin_points_SetContract"].tiqora_only is True
    assert actions["Plugin_points_SetContract"].module == ACTION_MODULE
    assert ZNUNY_VALIDATION_PREFIX + "Plugin_points_IsVip" in catalog.condition_modules
