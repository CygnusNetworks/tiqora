"""Draft reader and designer catalogue (both dialects).

Seed id band 894500-894699 (dynamic fields); the pm_* rows are written through the
``ProcessWriter`` with generated ids and named ``wf3 ...``; the fixture removes them and
the ``pm_entity_sync`` / cache signal rows they caused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.process.designer.catalog import build_designer_catalog
from tiqora.process.designer.draft import (
    list_entities,
    list_process_summaries,
    load_process_draft,
    load_sync_states,
)
from tiqora.process.designer.writer import EntityNotFound, ProcessWriter, WrittenEntity
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

_URLS: set[str] = set()
_TABLES = (
    "pm_process",
    "pm_activity",
    "pm_activity_dialog",
    "pm_transition",
    "pm_transition_action",
)
USER = 1
DF_BAND = (894500, 894699)
T0 = datetime(2026, 3, 4, 5, 6, 7)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _purge(sync_url: str, signal_mark: int | None = None) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for table in _TABLES:
            ids = conn.execute(
                text(f"SELECT entity_id FROM {table} WHERE name LIKE 'wf3%'")
            ).scalars()
            for eid in list(ids):
                conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})
            conn.execute(text(f"DELETE FROM {table} WHERE name LIKE 'wf3%'"))
        conn.execute(
            text("DELETE FROM dynamic_field WHERE id BETWEEN :a AND :b"),
            {"a": DF_BAND[0], "b": DF_BAND[1]},
        )
        if signal_mark is not None:
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": signal_mark}
            )
    engine.dispose()


@dataclass
class Env:
    session: AsyncSession
    writer: ProcessWriter
    sync_url: str


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraCacheInvalidation.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
    sync_engine.dispose()
    _purge(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield Env(session=session, writer=ProcessWriter(session, user_id=USER), sync_url=sync_url)
        await session.rollback()
    await engine.dispose()
    _purge(sync_url, mark)


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url)


async def _wired(env: Env) -> dict[str, WrittenEntity]:
    """Process: first -(transition, action)-> second -> third; a dialog on ``first``."""
    w = env.writer
    action = await w.create_entity(
        "TransitionAction",
        name="wf3 action",
        config={
            "Module": "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet",
            "Config": {"State": "open"},
        },
    )
    t1 = await w.create_entity(
        "Transition", name="wf3 t1", config={"Condition": {}, "ConditionLinking": "and"}
    )
    t2 = await w.create_entity(
        "Transition", name="wf3 t2", config={"Condition": {}, "ConditionLinking": "and"}
    )
    dialog = await w.create_entity(
        "ActivityDialog",
        name="wf3 dialog",
        config={
            "Fields": {"Title": {"Display": 1}},
            "FieldOrder": ["Title"],
            "Interface": ["AgentInterface"],
        },
    )
    first = await w.create_entity(
        "Activity", name="wf3 first", config={"ActivityDialog": {"1": dialog.entity_id}}
    )
    second = await w.create_entity("Activity", name="wf3 second", config={"ActivityDialog": {}})
    third = await w.create_entity("Activity", name="wf3 third", config={"ActivityDialog": {}})
    created = await w.create_process(name="wf3 process", description="d")
    process = await w.update_process(
        created.entity_id,
        expected_version=created.version,
        name="wf3 process",
        description="d",
        state_entity_id="S1",
        start_activity=first.entity_id,
        start_activity_dialog=dialog.entity_id,
        path={
            first.entity_id: {
                t1.entity_id: {
                    "ActivityEntityID": second.entity_id,
                    "TransitionAction": [action.entity_id],
                }
            },
            second.entity_id: {t2.entity_id: {"ActivityEntityID": third.entity_id}},
            third.entity_id: {},
        },
        layout={first.entity_id: {"left": "10", "top": "20"}},
    )
    await env.session.commit()
    return {
        "action": action,
        "t1": t1,
        "t2": t2,
        "dialog": dialog,
        "first": first,
        "second": second,
        "third": third,
        "process": process,
    }


async def test_draft_lists_activities_in_path_order_and_marks_sync_state(env: Env) -> None:
    ids = await _wired(env)
    draft = await load_process_draft(env.session, ids["process"].entity_id)

    assert [a.entity_id for a in draft.activities] == [
        ids["first"].entity_id,
        ids["second"].entity_id,
        ids["third"].entity_id,
    ]
    assert [t.entity_id for t in draft.transitions] == [ids["t1"].entity_id, ids["t2"].entity_id]
    assert [a.entity_id for a in draft.transition_actions] == [ids["action"].entity_id]
    assert [d.entity_id for d in draft.activity_dialogs] == [ids["dialog"].entity_id]
    assert draft.state_entity_id == "S1" and draft.state_name == "Active"
    assert draft.layout == {ids["first"].entity_id: {"left": "10", "top": "20"}}
    assert draft.missing_references == []
    assert draft.process.config["Description"] == "d"
    assert draft.process.config["StartActivity"] == ids["first"].entity_id
    assert draft.process.version == ids["process"].version  # the version after update_process
    assert draft.process.changed_by == USER
    # the writer marked everything as a draft
    assert draft.process.sync_state == "not_sync"
    assert all(a.sync_state == "not_sync" for a in draft.activities)
    assert draft.pending_changes >= 8

    # after a deploy (sync rows purged) the same rows read as synced
    await env.session.execute(
        text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": ids["first"].entity_id}
    )
    await env.session.commit()
    states = await load_sync_states(env.session)
    assert ("Activity", ids["first"].entity_id) not in states
    assert states[("Process", ids["process"].entity_id)] == "not_sync"
    again = await load_process_draft(env.session, ids["process"].entity_id)
    by_id = {a.entity_id: a for a in again.activities}
    assert by_id[ids["first"].entity_id].sync_state == "synced"
    assert by_id[ids["second"].entity_id].sync_state == "not_sync"

    # the version is the one the writer hands back for the stored row
    update = await env.writer.update_process(
        ids["process"].entity_id,
        expected_version=again.process.version,
        name="wf3 process",
        description="d2",
        state_entity_id="S1",
        start_activity=ids["first"].entity_id,
        start_activity_dialog=ids["dialog"].entity_id,
        path=again.process.config["Path"],
        layout=again.layout,
    )
    assert update.version != again.process.version


async def test_unknown_process_raises_not_found(env: Env) -> None:
    with pytest.raises(EntityNotFound):
        await load_process_draft(env.session, "Process-" + "0" * 32)


async def test_missing_references_reported(env: Env) -> None:
    ids = await _wired(env)
    ghost_dialog = "ActivityDialog-" + "a" * 32
    ghost_action = "TransitionAction-" + "b" * 32
    # Rows written behind the writer's back, as an old import or a Znuny edit could leave them.
    await env.session.execute(
        text("UPDATE pm_activity SET config = :c WHERE entity_id = :e"),
        {"c": f"---\nActivityDialog:\n  '1': {ghost_dialog}\n", "e": ids["first"].entity_id},
    )
    await env.session.execute(
        text("DELETE FROM pm_transition_action WHERE entity_id = :e"),
        {"e": ids["action"].entity_id},
    )
    await env.session.execute(  # no pm_* row is left for the purge to find it by name
        text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": ids["action"].entity_id}
    )
    await env.session.commit()

    draft = await load_process_draft(env.session, ids["process"].entity_id)
    assert ghost_dialog in draft.missing_references
    assert ids["action"].entity_id in draft.missing_references
    assert ghost_action not in draft.missing_references
    # the dialog of the start activity is gone from the activity but still referenced by the
    # process as StartActivityDialog, so it is returned
    assert [d.entity_id for d in draft.activity_dialogs] == [ids["dialog"].entity_id]
    assert draft.transition_actions == []


async def test_entities_list_used_by(env: Env) -> None:
    ids = await _wired(env)
    spare = await env.writer.create_entity(
        "Activity", name="wf3 unused", config={"ActivityDialog": {}}
    )
    await env.session.commit()

    pid = ids["process"].entity_id
    activities = {e.entity_id: e for e in await list_entities(env.session, "Activity")}
    assert activities[ids["first"].entity_id].used_by == [pid]
    assert activities[spare.entity_id].used_by == []
    assert activities[spare.entity_id].sync_state == "not_sync"
    dialogs = {e.entity_id: e for e in await list_entities(env.session, "ActivityDialog")}
    assert dialogs[ids["dialog"].entity_id].used_by == [ids["first"].entity_id]
    transitions = {e.entity_id: e for e in await list_entities(env.session, "Transition")}
    assert transitions[ids["t2"].entity_id].used_by == [pid]
    actions = {e.entity_id: e for e in await list_entities(env.session, "TransitionAction")}
    assert actions[ids["action"].entity_id].used_by == [pid]
    processes = {e.entity_id: e for e in await list_entities(env.session, "Process")}
    assert processes[pid].used_by == [] and processes[pid].name == "wf3 process"

    summaries = {p.entity_id: p for p in await list_process_summaries(env.session)}
    assert summaries[pid].state_name == "Active"
    assert summaries[pid].sync_state == "not_sync"
    assert summaries[pid].has_draft_changes


async def test_summary_has_draft_changes_for_an_edited_form(env: Env) -> None:
    ids = await _wired(env)
    pid = ids["process"].entity_id

    async def clear(*entity_ids: str) -> None:
        for eid in entity_ids:
            await env.session.execute(
                text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid}
            )
        await env.session.commit()

    async def summary() -> Any:
        return {p.entity_id: p for p in await list_process_summaries(env.session)}[pid]

    # "deployed" except the form of the first step: the process row itself looks synced
    await clear(*(w.entity_id for k, w in ids.items() if k != "dialog"))
    row = await summary()
    assert row.sync_state == "synced" and row.has_draft_changes
    await clear(ids["dialog"].entity_id)
    assert not (await summary()).has_draft_changes


async def _seed_df(env: Env, df_id: int, name: str, ftype: str, cfg: str, valid: int = 1) -> None:
    await env.session.execute(
        text(
            "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
            " field_type, object_type, config, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 0, :n, :l, 1, :ft, 'Ticket', :cfg, :v,"
            " :t, 1, :t, 1)"
        ),
        {
            "id": df_id,
            "n": name,
            "l": f"Label {name}",
            "ft": ftype,
            "cfg": cfg,
            "v": valid,
            "t": T0,
        },
    )
    await env.session.commit()


async def test_catalog_contains_registry_actions_and_modules(env: Env) -> None:
    await _seed_df(
        env,
        894500,
        "wf3Region",
        "Dropdown",
        "---\nPossibleValues:\n  n: Nord\n  s: Sued\n",
    )
    await _seed_df(env, 894501, "wf3Hidden", "Text", "---\n{}\n", valid=2)
    catalog = await build_designer_catalog(env.session, SysConfig(env.session, fetch=_fetch({})))

    actions = {a.name: a for a in catalog.actions}
    state_set = actions["TicketStateSet"]
    assert state_set.module == "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet"
    assert state_set.supported is True and state_set.tiqora_only is False
    params = {p.name: p for p in state_set.params}
    assert params["State"].required is True and params["State"].one_of == ["State", "StateID"]
    assert params["UserID"].kind == "user" and params["UserID"].required is False
    assert [p.name for p in state_set.params].count("UserID") == 1
    assert actions["ConfigItemUpdate"].supported is True
    assert "AppointmentCreate" in actions
    assert [a.name for a in catalog.actions] == sorted(actions)

    assert catalog.condition_types[0] == "Module" and "LessThanOrEqual" in catalog.condition_types
    assert (
        "Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo"
        in catalog.condition_modules
    )
    assert catalog.interfaces == ["AgentInterface", "CustomerInterface"]
    assert catalog.permissions[0] == "" and "rw" in catalog.permissions

    fields = {f.name: f for f in catalog.fields}
    for name in ("Title", "Queue", "State", "Priority", "Type", "Service", "SLA", "Owner",
                 "Responsible", "Lock", "CustomerUserID", "CustomerID", "PendingTime",
                 "Article", "Attachments"):  # fmt: skip
        assert fields[name].dialog is True, name
    assert fields["Queue"].condition is True and fields["Article"].condition is False
    assert fields["QueueID"].condition is True and fields["QueueID"].dialog is False
    assert "StateType" in fields and "Created" in fields
    assert {o.value for o in fields["Lock"].options} >= {"lock", "unlock"}
    assert fields["Queue"].options  # the seeded Znuny schema has queues
    region = fields["DynamicField_wf3Region"]
    assert region.kind == "Dropdown" and region.dialog and region.condition
    assert region.label == "Label wf3Region"
    assert [(o.value, o.label) for o in region.options] == [("n", "Nord"), ("s", "Sued")]
    assert "DynamicField_wf3Hidden" not in fields  # invalid fields are not offered


def _fetch(values: dict[str, Any]) -> Any:
    async def fetch(name: str) -> Any:
        return values.get(name)

    return fetch


async def test_catalog_account_time_flag(env: Env) -> None:
    on = await build_designer_catalog(
        env.session, SysConfig(env.session, fetch=_fetch({"Ticket::Frontend::AccountTime": "1"}))
    )
    off = await build_designer_catalog(
        env.session, SysConfig(env.session, fetch=_fetch({"Ticket::Frontend::AccountTime": "0"}))
    )
    assert on.account_time is True
    assert off.account_time is False  # Perl-truthy: the string '0' is false
    custom = await build_designer_catalog(
        env.session,
        SysConfig(env.session, fetch=_fetch({"System::Permission": ["ro", "rw"]})),
    )
    assert custom.permissions == ["", "ro", "rw"]
