"""ProcessWriter: Znuny draft semantics for ``pm_*`` (both dialects).

Seed band 894000-894499 is reserved for this module; it needs no fixed ids because the
writer generates entity ids and autoincrement ids. Every test names its rows ``wf3 ...``
and the fixture deletes those ``pm_*`` rows, their ``pm_entity_sync`` rows, and the
``tiqora_cache_invalidation`` / revision rows written above the recorded marks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraCacheInvalidation,
    TiqoraProcessRevision,
    TiqoraSettings,
)
from tiqora.deployed import clear_revision_cache, record_revision
from tiqora.deployed.watcher import check_deploy_state, deploy_from_tiqora
from tiqora.process.designer.versioning import entity_version
from tiqora.process.designer.writer import (
    EntityInUse,
    EntityNotFound,
    InvalidReference,
    ProcessNotInactive,
    ProcessWriter,
    VersionConflict,
    WrittenEntity,
)
from tiqora.process.designer.yaml_codec import load_znuny_yaml
from tiqora.process.dump import build_process_dump
from tiqora.process.source import LiveProcessSource, active_process_source

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


def _s(value: object) -> str:
    """MariaDB returns the LONGBLOB columns as bytes."""
    return bytes(value).decode("utf-8") if isinstance(value, bytes | bytearray) else str(value)  # type: ignore[arg-type]


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _purge(
    sync_url: str,
    marks: dict[str, int] | None = None,
    keep_sync: set[tuple[str, str]] | None = None,
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        if keep_sync is not None:  # sync rows of deleted entities have no pm_* row left
            for etype, eid in conn.execute(
                text("SELECT entity_type, entity_id FROM pm_entity_sync")
            ).all():
                if (etype, eid) not in keep_sync:
                    conn.execute(
                        text(
                            "DELETE FROM pm_entity_sync WHERE entity_type = :t AND entity_id = :e"
                        ),
                        {"t": etype, "e": eid},
                    )
        for table in _TABLES:
            ids = conn.execute(
                text(f"SELECT entity_id FROM {table} WHERE name LIKE 'wf3%'")
            ).scalars()
            for eid in list(ids):
                conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})
            conn.execute(text(f"DELETE FROM {table} WHERE name LIKE 'wf3%'"))
        if marks is not None:
            for table, mark in marks.items():
                conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": mark})
    engine.dispose()


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    session: AsyncSession
    writer: ProcessWriter
    sync_url: str
    signal_mark: int

    def fresh_writer(self) -> ProcessWriter:
        return ProcessWriter(self.session, user_id=USER)

    async def one(self, sql: str, **params: object) -> object:
        row = (await self.session.execute(text(sql), params)).first()
        return None if row is None else row[0]

    async def signals(self) -> list[str]:
        rows = await self.session.execute(
            text("SELECT cache_type FROM tiqora_cache_invalidation WHERE id > :m ORDER BY id"),
            {"m": self.signal_mark},
        )
        return [r[0] for r in rows.all()]

    async def sync_rows(self, entity_id: str) -> list[tuple[str, str]]:
        rows = await self.session.execute(
            text("SELECT entity_type, sync_state FROM pm_entity_sync WHERE entity_id = :e"),
            {"e": entity_id},
        )
        return [(r[0], r[1]) for r in rows.all()]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[  # type: ignore[list-item]
            TiqoraCacheInvalidation.__table__,
            TiqoraProcessRevision.__table__,
            TiqoraSettings.__table__,
        ],
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        marks = {
            t: int(conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {t}")).scalar_one())
            for t in ("tiqora_cache_invalidation", "tiqora_process_revision")
        }
        keep_sync = {
            (r[0], r[1])
            for r in conn.execute(text("SELECT entity_type, entity_id FROM pm_entity_sync")).all()
        }
    sync_engine.dispose()
    _purge(sync_url)
    clear_revision_cache()
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield Env(
            factory=factory,
            session=session,
            writer=ProcessWriter(session, user_id=USER),
            sync_url=sync_url,
            signal_mark=marks["tiqora_cache_invalidation"],
        )
        await session.rollback()
    await engine.dispose()
    clear_revision_cache()
    _purge(sync_url, marks, keep_sync)


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url)


async def _wired(env: Env) -> dict[str, WrittenEntity]:
    """A process using one of each sub-entity, written through the writer."""
    w = env.writer
    action = await w.create_entity(
        "TransitionAction",
        name="wf3 action",
        config={
            "Module": "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet",
            "Config": {"State": "open"},
        },
    )
    transition = await w.create_entity(
        "Transition", name="wf3 transition", config={"Condition": {}, "ConditionLinking": "and"}
    )
    dialog = await w.create_entity(
        "ActivityDialog",
        name="wf3 dialog",
        config={
            "DescriptionShort": "before",
            "Fields": {"Title": {"Display": 1}},
            "FieldOrder": ["Title"],
            "Interface": ["AgentInterface"],
        },
    )
    first = await w.create_entity(
        "Activity", name="wf3 first", config={"ActivityDialog": {"1": dialog.entity_id}}
    )
    last = await w.create_entity("Activity", name="wf3 last", config={"ActivityDialog": {}})
    process = await w.create_process(name="wf3 process", description="x")
    await w.update_process(
        process.entity_id,
        expected_version=process.version,
        name="wf3 process",
        description="x",
        state_entity_id="S1",
        start_activity=first.entity_id,
        start_activity_dialog=dialog.entity_id,
        path={
            first.entity_id: {
                transition.entity_id: {
                    "ActivityEntityID": last.entity_id,
                    "TransitionAction": [action.entity_id],
                }
            },
            last.entity_id: {},
        },
        layout={first.entity_id: {"left": "10", "top": "20"}},
    )
    return {
        "action": action,
        "transition": transition,
        "dialog": dialog,
        "first": first,
        "last": last,
        "process": process,
    }


async def test_create_process_row_shape_like_znuny(env: Env) -> None:
    out = await env.writer.create_process(name="wf3 shape", description="Beschreibung")
    await env.session.commit()
    assert out.entity_type == "Process" and out.name == "wf3 shape"
    assert out.entity_id.startswith("Process-") and len(out.entity_id) == len("Process-") + 32
    row = (
        await env.session.execute(
            text(
                "SELECT id, state_entity_id, layout, config, create_by, change_by,"
                " create_time, change_time FROM pm_process WHERE entity_id = :e"
            ),
            {"e": out.entity_id},
        )
    ).one()
    assert row[0] == out.id
    assert row[1] == "S2"
    assert _s(row[2]).startswith("---") and load_znuny_yaml(_s(row[2])) == {}
    assert load_znuny_yaml(_s(row[3])) == {
        "Description": "Beschreibung",
        "Path": {},
        "StartActivity": "",
        "StartActivityDialog": "",
    }
    assert row[4] == USER and row[5] == USER
    assert row[6] is not None and row[7] is not None  # set by the database
    assert out.version == entity_version(
        name="wf3 shape", config=_s(row[3]), layout=_s(row[2]), state_entity_id="S2"
    )
    assert await env.sync_rows(out.entity_id) == [("Process", "not_sync")]


async def test_edit_writes_not_sync_row_and_cache_signal(env: Env) -> None:
    made = await env.writer.create_entity("Activity", name="wf3 act", config={"ActivityDialog": {}})
    await env.session.commit()
    # simulate a deploy that cleared the sync table, then edit
    await env.session.execute(
        text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": made.entity_id}
    )
    await env.session.commit()
    mark_before = len(await env.signals())

    w = env.fresh_writer()
    second = await w.update_entity(
        "Activity",
        made.entity_id,
        expected_version=made.version,
        name="wf3 act renamed",
        config={"ActivityDialog": {}},
    )
    await env.session.commit()
    assert await env.sync_rows(made.entity_id) == [("Activity", "not_sync")]
    assert (await env.signals())[mark_before:] == ["ProcessManagement_Activity"]
    first_change = await env.one(
        "SELECT change_time FROM pm_entity_sync WHERE entity_id = :e", e=made.entity_id
    )
    assert first_change is not None

    third = await w.update_entity(
        "Activity",
        made.entity_id,
        expected_version=second.version,
        name="wf3 act again",
        config={"ActivityDialog": {}},
    )
    await env.session.commit()
    assert third.version != second.version
    assert await env.sync_rows(made.entity_id) == [("Activity", "not_sync")]  # same row, updated
    assert (
        await env.one("SELECT COUNT(*) FROM pm_entity_sync WHERE entity_id = :e", e=made.entity_id)
        == 1
    )
    assert (
        await env.one("SELECT name FROM pm_activity WHERE entity_id = :e", e=made.entity_id)
        == "wf3 act again"
    )
    assert (
        await env.one("SELECT change_by FROM pm_activity WHERE entity_id = :e", e=made.entity_id)
        == USER
    )


async def test_noop_update_writes_nothing(env: Env) -> None:
    cfg = {"ActivityDialog": {"1": "ActivityDialog-" + "a" * 32}}
    made = await env.writer.create_entity("Activity", name="wf3 noop", config=cfg)
    await env.session.execute(text("DELETE FROM pm_entity_sync"))
    await env.session.commit()
    marks = len(await env.signals())

    out = await env.fresh_writer().update_entity(
        "Activity", made.entity_id, expected_version=made.version, name="wf3 noop", config=cfg
    )
    await env.session.commit()
    assert out.version == made.version
    assert await env.sync_rows(made.entity_id) == []
    assert len(await env.signals()) == marks

    # numbers typed as ints and a re-ordered dict are the same config
    out2 = await env.fresh_writer().update_entity(
        "Activity",
        made.entity_id,
        expected_version=made.version,
        name="wf3 noop",
        config={"ActivityDialog": {1: "ActivityDialog-" + "a" * 32}},
    )
    assert out2.version == made.version
    assert await env.sync_rows(made.entity_id) == []

    proc = await env.writer.create_process(name="wf3 noop proc")
    await env.session.execute(text("DELETE FROM pm_entity_sync"))
    await env.session.commit()
    same = await env.fresh_writer().update_process(
        proc.entity_id,
        expected_version=proc.version,
        name="wf3 noop proc",
        description="",
        state_entity_id="S2",
        start_activity="",
        start_activity_dialog="",
        path={},
        layout={},
    )
    assert same.version == proc.version
    assert await env.sync_rows(proc.entity_id) == []


async def test_stale_expected_version_is_409(env: Env) -> None:
    made = await env.writer.create_entity("Transition", name="wf3 stale", config={"Condition": {}})
    await env.session.commit()
    await env.fresh_writer().update_entity(
        "Transition",
        made.entity_id,
        expected_version=made.version,
        name="wf3 stale winner",
        config={"Condition": {}},
    )
    await env.session.commit()
    with pytest.raises(VersionConflict):
        await env.fresh_writer().update_entity(
            "Transition",
            made.entity_id,
            expected_version=made.version,
            name="wf3 stale loser",
            config={"Condition": {}},
        )
    await env.session.rollback()
    assert (
        await env.one("SELECT name FROM pm_transition WHERE entity_id = :e", e=made.entity_id)
        == "wf3 stale winner"
    )

    proc = await env.writer.create_process(name="wf3 stale proc")
    await env.session.commit()
    with pytest.raises(VersionConflict):
        await env.fresh_writer().update_process(
            proc.entity_id,
            expected_version="0" * 16,
            name="wf3 stale proc 2",
            description="",
            state_entity_id="S2",
            start_activity="",
            start_activity_dialog="",
            path={},
            layout={},
        )
    await env.session.rollback()
    assert (
        await env.one("SELECT name FROM pm_process WHERE entity_id = :e", e=proc.entity_id)
        == "wf3 stale proc"
    )
    with pytest.raises(EntityNotFound):
        await env.fresh_writer().update_entity(
            "Activity",
            "Activity-" + "f" * 32,
            expected_version="x",
            name="wf3 none",
            config={},
        )


async def test_delete_used_entity_is_refused(env: Env) -> None:
    made = await _wired(env)
    await env.session.commit()
    process_id = made["process"].entity_id
    expected = {
        ("ActivityDialog", "dialog"): [made["first"].entity_id],
        ("Activity", "first"): [process_id],
        ("Activity", "last"): [process_id],
        ("Transition", "transition"): [process_id],
        ("TransitionAction", "action"): [process_id],
    }
    for (kind, key), users in expected.items():
        eid = made[key].entity_id
        with pytest.raises(EntityInUse) as err:
            await env.fresh_writer().delete_entity(kind, eid)  # type: ignore[arg-type]
        assert err.value.used_by == users, kind
        assert (
            await env.one(f"SELECT COUNT(*) FROM {_table(kind)} WHERE entity_id = :e", e=eid) == 1
        )
    assert await env.fresh_writer().entity_usage("Process", process_id) == []

    # unused ones go, with a 'deleted' sync row and a signal
    spare = await env.writer.create_entity("Activity", name="wf3 spare", config={})
    await env.session.commit()
    await env.fresh_writer().delete_entity("Activity", spare.entity_id)
    await env.session.commit()
    assert (
        await env.one("SELECT COUNT(*) FROM pm_activity WHERE entity_id = :e", e=spare.entity_id)
        == 0
    )
    assert await env.sync_rows(spare.entity_id) == [("Activity", "deleted")]


def _table(kind: str) -> str:
    return {
        "Activity": "pm_activity",
        "ActivityDialog": "pm_activity_dialog",
        "Transition": "pm_transition",
        "TransitionAction": "pm_transition_action",
    }[kind]


async def test_delete_process_requires_inactive(env: Env) -> None:
    made = await _wired(env)  # leaves the process Active (S1)
    await env.session.commit()
    pid = made["process"].entity_id
    with pytest.raises(ProcessNotInactive):
        await env.fresh_writer().delete_process(pid)
    assert await env.one("SELECT COUNT(*) FROM pm_process WHERE entity_id = :e", e=pid) == 1

    row = (
        await env.session.execute(
            text("SELECT name, config, layout FROM pm_process WHERE entity_id = :e"), {"e": pid}
        )
    ).one()
    version = entity_version(
        name=row[0], config=_s(row[1]), layout=_s(row[2]), state_entity_id="S1"
    )
    cfg = load_znuny_yaml(_s(row[1]))
    await env.fresh_writer().update_process(
        pid,
        expected_version=version,
        name=row[0],
        description=cfg["Description"],
        state_entity_id="S2",
        start_activity=cfg["StartActivity"],
        start_activity_dialog=cfg["StartActivityDialog"],
        path=cfg["Path"],
        layout=load_znuny_yaml(_s(row[2])),
    )
    await env.session.commit()
    await env.fresh_writer().delete_process(pid)
    await env.session.commit()
    assert await env.one("SELECT COUNT(*) FROM pm_process WHERE entity_id = :e", e=pid) == 0
    assert await env.sync_rows(pid) == [("Process", "deleted")]
    # the activities are free now
    await env.fresh_writer().delete_entity("Activity", made["last"].entity_id)
    with pytest.raises(EntityNotFound):
        await env.fresh_writer().delete_process(pid)


async def test_update_process_rejects_unknown_references(env: Env) -> None:
    made = await _wired(env)
    await env.session.commit()
    proc = made["process"]
    first, last = made["first"].entity_id, made["last"].entity_id
    row = (
        await env.session.execute(
            text("SELECT config, layout FROM pm_process WHERE entity_id = :e"),
            {"e": proc.entity_id},
        )
    ).one()
    version = entity_version(
        name="wf3 process", config=_s(row[0]), layout=_s(row[1]), state_entity_id="S1"
    )
    ghost_a = "Activity-" + "9" * 32
    ghost_t = "Transition-" + "9" * 32
    good_t = made["transition"].entity_id

    async def attempt(**over: object) -> None:
        args: dict[str, object] = {
            "expected_version": version,
            "name": "wf3 process",
            "description": "x",
            "state_entity_id": "S1",
            "start_activity": first,
            "start_activity_dialog": made["dialog"].entity_id,
            "path": {first: {good_t: {"ActivityEntityID": last}}, last: {}},
            "layout": {},
        }
        args.update(over)
        with pytest.raises(InvalidReference):
            await env.fresh_writer().update_process(proc.entity_id, **args)  # type: ignore[arg-type]
        await env.session.rollback()

    await attempt(path={ghost_a: {}})  # unknown activity key
    await attempt(path={first: {ghost_t: {"ActivityEntityID": last}}, last: {}})  # transition
    await attempt(path={first: {good_t: {"ActivityEntityID": ghost_a}}})  # target
    await attempt(
        path={
            first: {
                good_t: {
                    "ActivityEntityID": last,
                    "TransitionAction": ["TransitionAction-" + "9" * 32],
                }
            },
            last: {},
        }
    )
    await attempt(path={first: {"not an id": {}}})  # malformed
    await attempt(state_entity_id="S9")
    await attempt(start_activity=ghost_a)
    await attempt(start_activity=last, start_activity_dialog=made["dialog"].entity_id)
    await attempt(start_activity="Activity-" + "9" * 32, start_activity_dialog="")
    # nothing was written by the refused calls
    assert (
        await env.one("SELECT config FROM pm_process WHERE entity_id = :e", e=proc.entity_id)
        == row[0]
    )


async def test_copy_reuses_entities_and_names_copy(env: Env) -> None:
    made = await _wired(env)
    await env.session.commit()
    copy = await env.fresh_writer().copy_process(made["process"].entity_id)
    await env.session.commit()
    assert copy.entity_id != made["process"].entity_id and copy.entity_id.startswith("Process-")
    assert copy.name == "wf3 process (Kopie)"
    row = (
        await env.session.execute(
            text("SELECT state_entity_id, config, layout FROM pm_process WHERE entity_id = :e"),
            {"e": copy.entity_id},
        )
    ).one()
    assert row[0] == "S2"
    cfg = load_znuny_yaml(_s(row[1]))
    assert list(cfg["Path"]) == [made["first"].entity_id, made["last"].entity_id] or set(
        cfg["Path"]
    ) == {made["first"].entity_id, made["last"].entity_id}
    assert cfg["Path"][made["first"].entity_id][made["transition"].entity_id] == {
        "ActivityEntityID": made["last"].entity_id,
        "TransitionAction": [made["action"].entity_id],
    }
    assert load_znuny_yaml(_s(row[2])) == {made["first"].entity_id: {"left": "10", "top": "20"}}
    assert await env.sync_rows(copy.entity_id) == [("Process", "not_sync")]
    assert await env.one("SELECT COUNT(*) FROM pm_activity WHERE name LIKE 'wf3%'") == 2  # reused
    # a 200 char name still fits
    long = await env.writer.create_process(name="wf3" + "x" * 197)
    copy2 = await env.fresh_writer().copy_process(long.entity_id)
    assert len(copy2.name) == 200 and copy2.name.endswith(" (Kopie)")


async def test_engine_keeps_revision_after_designer_edit(env: Env) -> None:
    made = await _wired(env)
    await env.session.commit()
    info = await record_revision(
        env.session,
        "process",
        dump=await build_process_dump(env.session),
        source="tiqora",
        deployed_by=USER,
    )
    await env.session.commit()
    clear_revision_cache()
    dialog = made["dialog"]
    eid = dialog.entity_id

    async def short(source: object) -> str:
        records = await source.load("ActivityDialog", [eid])  # type: ignore[attr-defined]
        return str(records[eid].data["DescriptionShort"])

    before = await active_process_source(env.session)
    assert before.revision_id == info.id and await short(before) == "before"

    await env.fresh_writer().update_entity(
        "ActivityDialog",
        eid,
        expected_version=dialog.version,
        name="wf3 dialog",
        config={
            "DescriptionShort": "after",
            "Fields": {"Title": {"Display": 1}},
            "FieldOrder": ["Title"],
            "Interface": ["AgentInterface"],
        },
    )
    await env.session.commit()
    clear_revision_cache()
    assert (
        await env.one("SELECT sync_state FROM pm_entity_sync WHERE entity_id = :e", e=eid)
        == "not_sync"
    )

    source = await active_process_source(env.session)
    assert source.revision_id == info.id  # the draft is not deployed
    assert await short(source) == "before"
    assert await short(LiveProcessSource(env.session)) == "after"


async def test_engine_keeps_revision_after_designer_edit_in_ownership_mode(env: Env) -> None:
    """Ownership does not make drafts live: only deploy_from_tiqora(mode="local") does."""
    made = await _wired(env)
    await env.session.commit()
    first = await check_deploy_state(env.session, "process", ownership_active=True)
    assert first.action in {"snapshot", "running_live"}
    if first.action == "running_live":  # leftover sync rows of the wiring: deploy them first
        await deploy_from_tiqora(env.session, "process", user_id=USER, mode="local")
    clear_revision_cache()
    dialog = made["dialog"]
    eid = dialog.entity_id

    async def short(source: object) -> str:
        records = await source.load("ActivityDialog", [eid])  # type: ignore[attr-defined]
        return str(records[eid].data["DescriptionShort"])

    before = await active_process_source(env.session)
    assert await short(before) == "before"

    await env.fresh_writer().update_entity(
        "ActivityDialog",
        eid,
        expected_version=dialog.version,
        name="wf3 dialog",
        config={
            "DescriptionShort": "after",
            "Fields": {"Title": {"Display": 1}},
            "FieldOrder": ["Title"],
            "Interface": ["AgentInterface"],
        },
    )
    await env.session.commit()
    tick = await check_deploy_state(env.session, "process", ownership_active=True)
    clear_revision_cache()
    assert tick.action == "waiting_for_deploy" and tick.pending_changes >= 1
    assert await short(await active_process_source(env.session)) == "before"

    result = await deploy_from_tiqora(env.session, "process", user_id=USER, mode="local")
    clear_revision_cache()
    assert result.action == "recorded"
    source = await active_process_source(env.session)
    assert source.revision_id == result.revision_id
    assert await short(source) == "after"
    assert await env.one("SELECT COUNT(*) FROM pm_entity_sync") == 0


async def test_update_defaults_scope_for_activities_only(env: Env) -> None:
    """Golden-master finding: ``*Add`` defaults ``Scope`` for all four types, ``*Update`` only for
    activities (``DB/Activity.pm:475``); an edited dialog/transition/action keeps its config."""
    w = env.writer
    made = {
        "Activity": await w.create_entity("Activity", name="wf3 scope act", config={}),
        "ActivityDialog": await w.create_entity(
            "ActivityDialog", name="wf3 scope dlg", config={"Interface": ["AgentInterface"]}
        ),
        "Transition": await w.create_entity("Transition", name="wf3 scope tr", config={}),
        "TransitionAction": await w.create_entity(
            "TransitionAction", name="wf3 scope ta", config={"Module": "My::Module"}
        ),
    }
    tables = {
        "Activity": "pm_activity",
        "ActivityDialog": "pm_activity_dialog",
        "Transition": "pm_transition",
        "TransitionAction": "pm_transition_action",
    }
    await env.session.commit()
    for kind, written in made.items():  # created: all four carry the Add default
        stored = load_znuny_yaml(
            _s(
                await env.one(
                    f"SELECT config FROM {tables[kind]} WHERE entity_id = :e", e=written.entity_id
                )
            )  # noqa: S608
        )
        assert stored["Scope"] == "Global", kind

    # a config without Scope, as ProcessImport's *Update stores it
    imported = {
        "Activity": {},
        "ActivityDialog": {"Interface": ["AgentInterface"], "Fields": {}, "FieldOrder": []},
        "Transition": {"Condition": {}},
        "TransitionAction": {"Module": "My::Module", "Config": {}},
    }
    for kind, written in made.items():
        await env.session.execute(
            text(f"DELETE FROM {tables[kind]} WHERE entity_id = :e"),  # noqa: S608
            {"e": written.entity_id},
        )
    await env.session.commit()
    for kind, written in made.items():
        await env.fresh_writer().import_entity(
            kind,  # type: ignore[arg-type]
            written.entity_id,
            name=written.name,
            config=imported[kind],
        )
        await env.session.commit()
        stored = load_znuny_yaml(
            _s(
                await env.one(
                    f"SELECT config FROM {tables[kind]} WHERE entity_id = :e", e=written.entity_id
                )
            )  # noqa: S608
        )
        assert ("Scope" in stored) is (kind == "Activity"), kind

    # editing keeps a dialog without Scope as it is, and still defaults an activity's
    dialog = made["ActivityDialog"]
    current = await env.one(
        "SELECT config FROM pm_activity_dialog WHERE entity_id = :e", e=dialog.entity_id
    )
    out = await env.fresh_writer().update_entity(
        "ActivityDialog",
        dialog.entity_id,
        expected_version=entity_version(name=dialog.name, config=_s(current)),
        name=dialog.name,
        config={**imported["ActivityDialog"], "DescriptionShort": "edited"},
    )
    await env.session.commit()
    assert out.name == dialog.name
    edited = load_znuny_yaml(
        _s(
            await env.one(
                "SELECT config FROM pm_activity_dialog WHERE entity_id = :e", e=dialog.entity_id
            )
        )
    )
    assert edited["DescriptionShort"] == "edited" and "Scope" not in edited


async def test_config_normalisation(env: Env) -> None:
    w = env.writer
    act = await w.create_entity(
        "Activity",
        name="wf3 norm act",
        config={
            "ActivityDialog": {
                "3": "ActivityDialog-" + "c" * 32,
                "10": "ActivityDialog-" + "d" * 32,
                "7": "",
            }
        },
    )
    dialog = await w.create_entity(
        "ActivityDialog",
        name="wf3 norm dialog",
        config={
            "DescriptionShort": "d",
            "Fields": {"Title": {"Display": 2, "DefaultValue": 5}, "Queue": {"Display": True}},
            "FieldOrder": ["Queue", "Gone", "Queue"],
            "Interface": "CustomerInterface",
        },
    )
    tr = await w.create_entity(
        "Transition",
        name="wf3 norm tr",
        config={"Condition": {"5": {"Type": "and"}, "2": {"Type": "or"}}},
    )
    ta = await w.create_entity(
        "TransitionAction",
        name="wf3 norm ta",
        config={"Module": "My::Own::Module", "Config": {"Count": 3}},
    )
    await env.session.commit()

    def stored(table: str, eid: str) -> object:
        return env.one(f"SELECT config FROM {table} WHERE entity_id = :e", e=eid)

    a = load_znuny_yaml(_s(await stored("pm_activity", act.entity_id)))
    assert a == {
        "ActivityDialog": {"1": "ActivityDialog-" + "c" * 32, "2": "ActivityDialog-" + "d" * 32},
        "Scope": "Global",
    }
    d = load_znuny_yaml(_s(await stored("pm_activity_dialog", dialog.entity_id)))
    assert d["FieldOrder"] == ["Queue", "Title"]  # unknown dropped, missing appended
    assert d["Interface"] == ["CustomerInterface"]
    assert d["Fields"]["Title"] == {"Display": "2", "DefaultValue": "5"}  # string scalars
    assert d["Fields"]["Queue"]["Display"] == "1"
    t = load_znuny_yaml(_s(await stored("pm_transition", tr.entity_id)))
    assert t["Condition"] == {"1": {"Type": "or"}, "2": {"Type": "and"}}
    assert t["ConditionLinking"] == "and"
    x = load_znuny_yaml(_s(await stored("pm_transition_action", ta.entity_id)))
    assert x["Config"] == {"Count": "3"} and x["Module"] == "My::Own::Module"

    for kind, cfg in (
        ("TransitionAction", {"Module": "NoPath", "Config": {}}),
        ("ActivityDialog", {"Interface": ["Nope"]}),
        ("Transition", {"Condition": {}, "ConditionLinking": "sometimes"}),
        ("Activity", {"Scope": "Process"}),
    ):
        with pytest.raises(InvalidReference):
            await env.fresh_writer().create_entity(kind, name="wf3 bad", config=cfg)  # type: ignore[arg-type]
    with pytest.raises(InvalidReference):
        await env.fresh_writer().create_entity("Activity", name="  ", config={})
