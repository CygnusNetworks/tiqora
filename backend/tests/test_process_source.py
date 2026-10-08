"""Process source: the engine reads the newest deployed revision, not live drafts.

Seed ids use the 881700-881799 band (rulings F8). Entity ids end in the numeric id.
Revisions are cleared around every test: ``tiqora_process_revision`` is empty in
the pristine snapshot, so this module owns whatever it finds there.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation, TiqoraEventOutbox
from tiqora.deployed import clear_revision_cache, load_active_revision, record_revision
from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.process import source as source_module
from tiqora.process.dump import build_process_dump
from tiqora.process.engine import start_process, submit_activity_dialog
from tiqora.process.graph import ProcessRepository
from tiqora.process.source import (
    LiveProcessSource,
    RevisionProcessSource,
    active_process_source,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T0 = datetime(2026, 3, 4, 5, 6, 7)

PROCESS = "Process-881701"
ACT_A = "Activity-881701"
ACT_B = "Activity-881702"
DIALOG = "ActivityDialog-881701"
TRANSITION = "Transition-881701"
ACTION = "TransitionAction-881701"
DRAFT_ONLY = "Process-881702"

_SEEDED: list[str] = []

_PROCESS_CFG = (
    "Description: Example process\n"
    f"StartActivity: {ACT_A}\n"
    f"StartActivityDialog: {DIALOG}\n"
    f"Path:\n  {ACT_A}:\n    {TRANSITION}:\n"
    f"      ActivityEntityID: {ACT_B}\n"
    f"      TransitionAction: [{ACTION}]\n"
    f"  {ACT_B}: {{}}\n"
)
_ACTION_CFG = (
    "Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet\n"
    "Config:\n  State: {state}\n"
)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _cleanup_statements() -> tuple[str, ...]:
    return (
        "DELETE FROM tiqora_process_revision",
        "DELETE FROM tiqora_acl_revision",
        "DELETE FROM pm_entity_sync WHERE entity_id LIKE '%-8817%'",
        "DELETE FROM pm_process WHERE id BETWEEN 881700 AND 881799",
        "DELETE FROM pm_activity WHERE id BETWEEN 881700 AND 881799",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 881700 AND 881799",
        "DELETE FROM pm_transition WHERE id BETWEEN 881700 AND 881799",
        "DELETE FROM pm_transition_action WHERE id BETWEEN 881700 AND 881799",
    )


def _entity(conn: Any, table: str, n: int, kind: str, name: str, config: str, **extra: Any) -> None:
    cols = "id, entity_id, name, config, create_time, create_by, change_time, change_by"
    vals = ":id, :eid, :name, :cfg, :t0, 1, :t0, 1"
    params: dict[str, Any] = {
        "id": 881700 + n,
        "eid": f"{kind}-{881700 + n}",
        "name": name,
        "cfg": config,
        "t0": T0,
    }
    if table == "pm_process":
        cols += ", state_entity_id, layout"
        vals += ", 'S1', :layout"
        params["layout"] = extra.get("layout", "{}")
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"), params)


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        _entity(
            conn,
            "pm_process",
            1,
            "Process",
            "Example",
            _PROCESS_CFG,
            layout="Activity-881701: {left: 10, top: 20}\n",
        )
        _entity(conn, "pm_activity", 1, "Activity", "A1", f"ActivityDialog:\n  '1': {DIALOG}\n")
        _entity(conn, "pm_activity", 2, "Activity", "A2", "ActivityDialog: {}\n")
        _entity(
            conn,
            "pm_activity_dialog",
            1,
            "ActivityDialog",
            "Set title",
            "DescriptionShort: Short\nFieldOrder: [Title]\nFields:\n  Title:\n    Display: '1'\n"
            "Interface: [AgentInterface]\nPermission: ''\n",
        )
        _entity(conn, "pm_transition", 1, "Transition", "T1", "ConditionLinking: and\n")
        _entity(
            conn,
            "pm_transition_action",
            1,
            "TransitionAction",
            "X1",
            _ACTION_CFG.format(state="closed successful"),
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _cleanup_statements():
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()
    clear_revision_cache()


@pytest.fixture(autouse=True)
def _fresh_state() -> None:
    clear_revision_cache()
    source_module._warned_live = False


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def mariadb_factory(
    mariadb_znuny_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    _seed(mariadb_znuny_url)
    sync_engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraCacheInvalidation.__table__, TiqoraEventOutbox.__table__],
    )
    sync_engine.dispose()
    marks = _max_ids(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    _delete_rows_above(mariadb_znuny_url, marks)


# Child tables first. ``ticket_number_counter`` and the tiqora_* queues are written
# by create_ticket and start/submit; the pristine snapshot holds no ticket rows.
_TICKET_TABLES = (
    "dynamic_field_value",
    "ticket_history",
    "ticket",
    "ticket_number_counter",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
)


def _max_ids(sync_url: str) -> dict[str, int]:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        marks = {
            t: int(conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {t}")).scalar_one())
            for t in _TICKET_TABLES
        }
    engine.dispose()
    return marks


def _delete_rows_above(sync_url: str, marks: dict[str, int]) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for table in _TICKET_TABLES:
            conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": marks[table]})
    engine.dispose()


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _record_current(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session, session.begin():
        info = await record_revision(
            session,
            "process",
            dump=await build_process_dump(session),
            source="initial",
            deployed_by=None,
        )
    return info.id


async def _ticket_state_name(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> str:
    async with factory() as session:
        return str(
            (
                await session.execute(
                    text(
                        "SELECT s.name FROM ticket t"
                        " JOIN ticket_state s ON s.id = t.ticket_state_id WHERE t.id = :tid"
                    ),
                    {"tid": ticket_id},
                )
            ).scalar_one()
        )


async def _new_ticket(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            _sysconfig(),
            params=TicketIn(title="Source test", queue_id=1, state_id=1, priority_id=3, owner_id=1),
            user_id=1,
        )


async def _run_process(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> None:
    sysconfig = _sysconfig()
    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=PROCESS,
            user_id=1,
            sysconfig=sysconfig,
        )
    async with factory() as session, session.begin():
        await submit_activity_dialog(
            session,
            ticket_id=ticket_id,
            activity_dialog_entity_id=DIALOG,
            field_values={"Title": "Via revision"},
            user_id=1,
            sysconfig=sysconfig,
        )


async def _draft_edit(factory: async_sessionmaker[AsyncSession]) -> None:
    """What a Znuny designer user does after the deploy: edit the action, add a new process."""
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE pm_transition_action SET config = :cfg WHERE entity_id = :eid"),
            {"cfg": _ACTION_CFG.format(state="open"), "eid": ACTION},
        )
        await session.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (881702, :eid, 'Draft only', 'S1', '{}', 'Description: x\n', :t, 1, :t, 1)"
            ),
            {"eid": DRAFT_ONLY, "t": T0},
        )
        await session.execute(
            text(
                "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
                " change_time) VALUES ('TransitionAction', :eid, 'created', :t, :t)"
            ),
            {"eid": ACTION, "t": T0},
        )


async def test_engine_runs_newest_revision_not_live_rows(
    mariadb_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _record_current(mariadb_factory)
    await _draft_edit(mariadb_factory)

    ticket_id = await _new_ticket(mariadb_factory)
    await _run_process(mariadb_factory, ticket_id)

    assert await _ticket_state_name(mariadb_factory, ticket_id) == "closed successful"


async def test_live_rows_are_used_while_no_revision_exists(
    mariadb_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _draft_edit(mariadb_factory)  # no revision recorded: the draft is what runs

    ticket_id = await _new_ticket(mariadb_factory)
    await _run_process(mariadb_factory, ticket_id)

    assert await _ticket_state_name(mariadb_factory, ticket_id) == "open"


async def test_get_process_unknown_in_revision_returns_none(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _record_current(factory)
    await _draft_edit(factory)  # DRAFT_ONLY exists in pm_process but not in the revision

    async with factory() as session:
        repository = ProcessRepository(session)
        assert await repository.get_process(DRAFT_ONLY) is None
        assert await repository.get_activity_dialog("ActivityDialog-881799") is None
        deployed = await repository.get_process(PROCESS)
        assert deployed is not None
        assert deployed.name == "Example"
        assert deployed.state_entity_id == "S1"
        assert deployed.config.start_activity == ACT_A
        assert deployed.layout.positions == {}  # layout is cosmetic and not part of a revision
        assert sorted(deployed.activities) == [ACT_A, ACT_B]
        transition = deployed.activities[ACT_A].outgoing_transitions[0]
        assert transition.entity_id == TRANSITION
        assert transition.target_activity_entity_id == ACT_B
        assert [a.config.config for a in transition.actions] == [{"State": "closed successful"}]
        dialog = await repository.get_activity_dialog(DIALOG)
        assert dialog is not None
        assert dialog.name == "Set title"
        assert dialog.config.field_order == ["Title"]


async def test_list_processes_carries_state_name_from_both_sources(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        live = [
            p for p in await ProcessRepository(session).list_processes() if p.entity_id == PROCESS
        ]
    assert [(p.name, p.state_entity_id, p.state_name) for p in live] == [
        ("Example", "S1", "Active")
    ]

    await _record_current(factory)
    await _draft_edit(factory)
    async with factory() as session:
        deployed = await ProcessRepository(session).list_processes()
    mine = [p for p in deployed if p.entity_id in (PROCESS, DRAFT_ONLY)]
    assert [(p.entity_id, p.state_name) for p in mine] == [(PROCESS, "Active")]
    assert mine[0].id == 881701


async def test_live_source_reads_layout_and_yaml(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        source = LiveProcessSource(session)
        loaded = await source.load("Process", [PROCESS, "Process-missing"])
        assert source.revision_id is None
        assert await source.load("Activity", []) == {}
    assert list(loaded) == [PROCESS]
    record = loaded[PROCESS]
    assert record.layout == {"Activity-881701": {"left": 10, "top": 20}}
    assert record.data["StartActivity"] == ACT_A
    assert record.state_entity_id == "S1"


async def test_revision_source_maps_dump_keys(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    revision_id = await _record_current(factory)
    async with factory() as session:
        revision = await load_active_revision(session, "process")
        assert revision is not None
        source = RevisionProcessSource(revision)
        records = await source.load("Process", [PROCESS])
        activities = await source.load("Activity", [ACT_A, "Activity-missing"])
    assert source.revision_id == revision_id
    assert records[PROCESS].id == 881701
    assert records[PROCESS].state_entity_id == "S1"
    assert list(activities) == [ACT_A]
    assert activities[ACT_A].name == "A1"


async def test_active_source_switches_to_revision_and_warns_once_while_live(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_module.logger, "disabled", False)
    # A handler on the module logger itself: other tests reconfigure logging
    # propagation, which would hide the record from caplog's root handler.
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.WARNING)
    source_module.logger.addHandler(handler)
    try:
        async with factory() as session:
            first = await active_process_source(session)
            second = await active_process_source(session)
    finally:
        source_module.logger.removeHandler(handler)
    assert isinstance(first, LiveProcessSource)
    assert isinstance(second, LiveProcessSource)
    warnings = [r for r in records if r.getMessage() == "process_definitions_running_live"]
    assert len(warnings) == 1

    revision_id = await _record_current(factory)
    async with factory() as session:
        third = await active_process_source(session)
    assert isinstance(third, RevisionProcessSource)
    assert third.revision_id == revision_id
