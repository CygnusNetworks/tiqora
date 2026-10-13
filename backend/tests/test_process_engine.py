"""DB integration test for the ProcessManagement (BPM) engine (subtask 2).

Seeds a minimal 2-activity / 1-dialog / 1-transition process directly into
``pm_process``/``pm_activity``/``pm_activity_dialog``/``pm_transition``/
``pm_transition_action`` (raw SQL — there is no admin write path for these
tables yet), then drives it through ``start_process`` and
``submit_activity_dialog`` and asserts the ticket actually advances activity,
its state/title change, and the corresponding Znuny history rows and
Dynamic Field values are written.

Follows the same ``mariadb_znuny_url`` fixture / ``_seed_tiqora_tables`` /
``_make_sysconfig`` / ``_make_ticket`` pattern as ``test_ticket_write_service.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.process.actor import ProcessActor
from tiqora.process.config import (
    ActivityDialogConfig,
    ActivityDialogFieldConfig,
    TransitionActionConfig,
)
from tiqora.process.engine import (
    _action_ticket_article_create,
    _action_ticket_customer_set,
    _action_ticket_lock_set,
    _action_ticket_owner_set,
    _action_ticket_priority_set,
    _action_ticket_queue_set,
    _action_ticket_responsible_set,
    _action_ticket_state_set,
    _action_ticket_title_set,
    _apply_dialog_field_changes,
    apply_activity_dialog,
    execute_transition_action,
    start_process,
    submit_activity_dialog,
)
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ActivityDialogNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketNotInProcess,
    UnresolvedFieldValue,
)
from tiqora.process.ticket_state import get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

PROCESS_ENTITY_ID = "Process-test1"
ACTIVITY_A = "Activity-a"
ACTIVITY_B = "Activity-b"
ACTIVITY_DIALOG_1 = "ActivityDialog-ad1"
TRANSITION_1 = "Transition-t1"
TRANSITION_ACTION_1 = "TransitionAction-ta1"

PROCESS_CONFIG_YAML = f"""
Description: Test process
StartActivity: {ACTIVITY_A}
StartActivityDialog: {ACTIVITY_DIALOG_1}
Path:
  {ACTIVITY_A}:
    {TRANSITION_1}:
      ActivityEntityID: {ACTIVITY_B}
      TransitionAction:
      - {TRANSITION_ACTION_1}
  {ACTIVITY_B}: {{}}
"""

ACTIVITY_A_CONFIG_YAML = f"""
ActivityDialog:
  '1': {ACTIVITY_DIALOG_1}
"""

ACTIVITY_B_CONFIG_YAML = "ActivityDialog: {}\n"

ACTIVITY_DIALOG_CONFIG_YAML = """
DescriptionShort: Set title
FieldOrder:
- Title
Fields:
  Title:
    Display: '1'
Interface:
- AgentInterface
Permission: ''
"""

# Znuny never fires a transition without a Condition (Transition.pm:322-327), so the fixtures
# carry a real one: a non-empty Title, which every dialog submission here sets.
_TITLE_NONEMPTY_CONDITION = """Condition:
  '1':
    Fields:
      Title:
        Match: .
        Type: Regexp
    Type: and
  Type: and
ConditionLinking: and
"""
TRANSITION_CONFIG_YAML = _TITLE_NONEMPTY_CONDITION

TRANSITION_ACTION_CONFIG_YAML = """
Config:
  State: closed successful
Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet
"""


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


# Every table this module writes, children first: the module deletes what it committed
# (rows above the ids present before it ran), so it also passes on its own.
_WRITTEN_TABLES = (
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "dynamic_field_value",
    "ticket",
    "ticket_number_counter",
    "dynamic_field",
    "pm_transition_action",
    "pm_transition",
    "pm_activity_dialog",
    "pm_activity",
    "pm_process",
    "sla",
    "service",
    "ticket_type",
    "users",
)


@pytest.fixture(scope="module", autouse=True)
def _delete_written_rows(mariadb_znuny_url: str) -> Iterator[None]:
    marks = snapshot_marks(mariadb_znuny_url, _WRITTEN_TABLES)
    yield
    delete_above_marks(mariadb_znuny_url, marks)


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    """Create tiqora_* tables in testcontainer DB (Alembic not run there)."""
    ddls = [
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_cache_invalidation (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id BIGINT NULL,
            cache_type VARCHAR(100) NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        "ALTER TABLE tiqora_cache_invalidation MODIFY ticket_id BIGINT NULL",
        "ALTER TABLE tiqora_cache_invalidation ADD COLUMN cache_type VARCHAR(100) NULL",
    ]
    for ddl in ddls:
        with contextlib.suppress(Exception):
            await session.execute(text(ddl))
    await session.commit()


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _make_ticket(factory: async_sessionmaker[AsyncSession], sysconfig: SysConfig) -> int:
    async with factory() as session, session.begin():
        return await create_ticket(
            session,
            factory,
            sysconfig,
            params=TicketIn(
                title="BPM test ticket", queue_id=1, state_id=1, priority_id=3, owner_id=1
            ),
            user_id=1,
        )


async def _seed_process(session: AsyncSession) -> None:
    now = "current_timestamp"
    await session.execute(
        text(
            "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, 'S1', '{{}}', :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": PROCESS_ENTITY_ID, "name": "Test Process", "cfg": PROCESS_CONFIG_YAML},
    )
    for eid, name, cfg in (
        (ACTIVITY_A, "Activity A", ACTIVITY_A_CONFIG_YAML),
        (ACTIVITY_B, "Activity B", ACTIVITY_B_CONFIG_YAML),
    ):
        await session.execute(
            text(
                "INSERT INTO pm_activity (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": eid, "name": name, "cfg": cfg},
        )
    await session.execute(
        text(
            "INSERT INTO pm_activity_dialog (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": ACTIVITY_DIALOG_1, "name": "Set Title", "cfg": ACTIVITY_DIALOG_CONFIG_YAML},
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": TRANSITION_1, "name": "T1", "cfg": TRANSITION_CONFIG_YAML},
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition_action (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": TRANSITION_ACTION_1, "name": "TA1", "cfg": TRANSITION_ACTION_CONFIG_YAML},
    )
    await session.commit()


@pytest.mark.db
async def test_start_process_and_submit_activity_dialog_advances_activity(
    mariadb_znuny_url: str,
) -> None:
    url = _mysql_async(mariadb_znuny_url)
    engine = _track_engine(create_async_engine(url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _seed_process(session)

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=PROCESS_ENTITY_ID,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        state = await get_ticket_process_state(session, ticket_id)
        assert state is not None
        assert state.process_entity_id == PROCESS_ENTITY_ID
        assert state.activity_entity_id == ACTIVITY_A

    async with factory() as session, session.begin():
        result = await submit_activity_dialog(
            session,
            ticket_id=ticket_id,
            activity_dialog_entity_id=ACTIVITY_DIALOG_1,
            field_values={"Title": "Updated via BPM dialog"},
            user_id=1,
            sysconfig=sysconfig,
        )

    assert result.activity_changed is True
    assert result.new_activity_entity_id == ACTIVITY_B
    assert result.transition_entity_id == TRANSITION_1
    assert result.unsupported_actions == []

    async with factory() as session:
        # Ticket advanced activity + state, and the dialog-submitted title landed.
        state = await get_ticket_process_state(session, ticket_id)
        assert state is not None
        assert state.activity_entity_id == ACTIVITY_B

        row = (
            await session.execute(
                text("SELECT title, ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).one()
        assert row[0] == "Updated via BPM dialog"
        assert row[1] == 2  # 'closed successful'

        # History: ordinary TitleUpdate + StateUpdate rows, plus
        # TicketDynamicFieldUpdate rows for the two process DFs — NOT a
        # synthetic "ProcessManagement" history type (see engine.py docstring).
        hist_types = (
            (
                await session.execute(
                    text(
                        "SELECT ht.name FROM ticket_history h"
                        " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                        " WHERE h.ticket_id = :tid"
                    ),
                    {"tid": ticket_id},
                )
            )
            .scalars()
            .all()
        )
        assert "TitleUpdate" in hist_types
        assert "StateUpdate" in hist_types
        assert hist_types.count("TicketDynamicFieldUpdate") >= 3  # 2 from start + 1 from advance
        assert "ProcessManagement" not in hist_types


# ---------------------------------------------------------------------------
# Conditional transition: positive (matches) + negative (does not match)
# ---------------------------------------------------------------------------

COND_ACTIVITY_DIALOG_CONFIG_YAML = """
DescriptionShort: Set title
FieldOrder:
- Title
Fields:
  Title:
    Display: '1'
Interface:
- AgentInterface
Permission: ''
"""

# The transition only fires when the submitted Title matches exactly —
# exercises a real Condition block (Type: String), not an unconditional one.
COND_TRANSITION_CONFIG_YAML = """
Condition:
  '1':
    Fields:
      Title:
        Match: Advance Me
        Type: String
    Type: and
ConditionLinking: and
"""

COND_TRANSITION_ACTION_CONFIG_YAML = """
Config:
  State: closed successful
Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet
"""


@dataclass(frozen=True)
class _CondProcessIds:
    process: str
    activity_a: str
    activity_b: str
    dialog: str
    transition: str
    action: str


async def _seed_cond_process(session: AsyncSession, *, suffix: str) -> _CondProcessIds:
    """Seed a fresh condition-testing process with entity ids namespaced by
    *suffix* — the DB fixture is session-scoped, so each caller needs its
    own unique entity ids to avoid a duplicate-key collision.
    """
    ids = _CondProcessIds(
        process=f"Process-testcond-{suffix}",
        activity_a=f"Activity-cond-a-{suffix}",
        activity_b=f"Activity-cond-b-{suffix}",
        dialog=f"ActivityDialog-cond-ad1-{suffix}",
        transition=f"Transition-cond-t1-{suffix}",
        action=f"TransitionAction-cond-ta1-{suffix}",
    )
    process_config_yaml = f"""
Description: Test process with a condition
StartActivity: {ids.activity_a}
StartActivityDialog: {ids.dialog}
Path:
  {ids.activity_a}:
    {ids.transition}:
      ActivityEntityID: {ids.activity_b}
      TransitionAction:
      - {ids.action}
  {ids.activity_b}: {{}}
"""
    activity_a_config_yaml = f"""
ActivityDialog:
  '1': {ids.dialog}
"""
    activity_b_config_yaml = "ActivityDialog: {}\n"

    now = "current_timestamp"
    await session.execute(
        text(
            "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, 'S1', '{{}}', :cfg, {now}, 1, {now}, 1)"
        ),
        {
            "eid": ids.process,
            "name": "Test Process Cond",
            "cfg": process_config_yaml,
        },
    )
    for eid, name, cfg in (
        (ids.activity_a, "Activity Cond A", activity_a_config_yaml),
        (ids.activity_b, "Activity Cond B", activity_b_config_yaml),
    ):
        await session.execute(
            text(
                "INSERT INTO pm_activity (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": eid, "name": name, "cfg": cfg},
        )
    await session.execute(
        text(
            "INSERT INTO pm_activity_dialog (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": ids.dialog, "name": "Set Title", "cfg": COND_ACTIVITY_DIALOG_CONFIG_YAML},
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": ids.transition, "name": "Cond T1", "cfg": COND_TRANSITION_CONFIG_YAML},
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition_action (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {
            "eid": ids.action,
            "name": "Cond TA1",
            "cfg": COND_TRANSITION_ACTION_CONFIG_YAML,
        },
    )
    await session.commit()
    return ids


@pytest.mark.db
async def test_transition_with_matching_condition_advances_and_applies_action(
    mariadb_znuny_url: str,
) -> None:
    """Positive case: the submitted Title matches the transition's Condition
    block -> the transition fires, its TicketStateSet action applies, and
    the ticket advances to the target activity.
    """
    url = _mysql_async(mariadb_znuny_url)
    engine = _track_engine(create_async_engine(url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_cond_process(session, suffix="pos")

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        result = await submit_activity_dialog(
            session,
            ticket_id=ticket_id,
            activity_dialog_entity_id=ids.dialog,
            field_values={"Title": "Advance Me"},
            user_id=1,
            sysconfig=sysconfig,
        )

    assert result.activity_changed is True
    assert result.new_activity_entity_id == ids.activity_b
    assert result.transition_entity_id == ids.transition
    assert result.unsupported_actions == []

    async with factory() as session:
        state = await get_ticket_process_state(session, ticket_id)
        assert state is not None
        assert state.activity_entity_id == ids.activity_b

        row = (
            await session.execute(
                text("SELECT title, ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).one()
        assert row[0] == "Advance Me"
        assert row[1] == 2  # 'closed successful'


@pytest.mark.db
async def test_transition_with_non_matching_condition_stays_on_same_activity(
    mariadb_znuny_url: str,
) -> None:
    """Negative case: the submitted Title does NOT match the transition's
    Condition block -> the dialog submission still succeeds (no error), but
    the ticket stays on the same activity, no TicketStateSet action runs,
    and no transition is reported as matched.
    """
    url = _mysql_async(mariadb_znuny_url)
    engine = _track_engine(create_async_engine(url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_cond_process(session, suffix="neg")

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        result = await submit_activity_dialog(
            session,
            ticket_id=ticket_id,
            activity_dialog_entity_id=ids.dialog,
            field_values={"Title": "Do Not Advance"},
            user_id=1,
            sysconfig=sysconfig,
        )

    assert result.activity_changed is False
    assert result.new_activity_entity_id is None
    assert result.transition_entity_id is None
    assert result.unsupported_actions == []

    async with factory() as session:
        # Ticket stayed on the start activity — the condition never matched
        # so the transition never fired and its TicketStateSet action never ran.
        state = await get_ticket_process_state(session, ticket_id)
        assert state is not None
        assert state.activity_entity_id == ids.activity_a

        row = (
            await session.execute(
                text("SELECT title, ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).one()
        # The dialog's Title field change is applied regardless of the
        # transition outcome (field application happens before transition
        # evaluation) — but the state was NOT set to 'closed successful'.
        assert row[0] == "Do Not Advance"
        assert row[1] != 2


# ---------------------------------------------------------------------------
# Unsupported TransitionAction module: skipped, reported, does not block
# ---------------------------------------------------------------------------

PROCESS_ENTITY_ID_UNSUP = "Process-testunsup"
UNSUP_ACTIVITY_A = "Activity-unsup-a"
UNSUP_ACTIVITY_B = "Activity-unsup-b"
UNSUP_ACTIVITY_DIALOG = "ActivityDialog-unsup-ad1"
UNSUP_TRANSITION = "Transition-unsup-t1"
UNSUP_TRANSITION_ACTION = "TransitionAction-unsup-ta1"

UNSUP_PROCESS_CONFIG_YAML = f"""
Description: Test process with an unsupported action
StartActivity: {UNSUP_ACTIVITY_A}
StartActivityDialog: {UNSUP_ACTIVITY_DIALOG}
Path:
  {UNSUP_ACTIVITY_A}:
    {UNSUP_TRANSITION}:
      ActivityEntityID: {UNSUP_ACTIVITY_B}
      TransitionAction:
      - {UNSUP_TRANSITION_ACTION}
  {UNSUP_ACTIVITY_B}: {{}}
"""

UNSUP_ACTIVITY_A_CONFIG_YAML = f"""
ActivityDialog:
  '1': {UNSUP_ACTIVITY_DIALOG}
"""

UNSUP_ACTIVITY_B_CONFIG_YAML = "ActivityDialog: {}\n"

UNSUP_ACTIVITY_DIALOG_CONFIG_YAML = """
DescriptionShort: Set title
FieldOrder:
- Title
Fields:
  Title:
    Display: '1'
Interface:
- AgentInterface
Permission: ''
"""

UNSUP_TRANSITION_CONFIG_YAML = _TITLE_NONEMPTY_CONDITION

# A test-only module registered with ``run=None`` (see the test) — used here to assert
# skip-and-report behaviour for modules without a Tiqora handler.
UNSUP_TRANSITION_ACTION_CONFIG_YAML = """
Config:
  ConfigItemID: '1'
Module: Kernel::System::ProcessManagement::TransitionAction::TestOnlyUnrun
"""


async def _seed_unsupported_action_process(session: AsyncSession) -> None:
    now = "current_timestamp"
    await session.execute(
        text(
            "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, 'S1', '{{}}', :cfg, {now}, 1, {now}, 1)"
        ),
        {
            "eid": PROCESS_ENTITY_ID_UNSUP,
            "name": "Test Process Unsup",
            "cfg": UNSUP_PROCESS_CONFIG_YAML,
        },
    )
    for eid, name, cfg in (
        (UNSUP_ACTIVITY_A, "Activity Unsup A", UNSUP_ACTIVITY_A_CONFIG_YAML),
        (UNSUP_ACTIVITY_B, "Activity Unsup B", UNSUP_ACTIVITY_B_CONFIG_YAML),
    ):
        await session.execute(
            text(
                "INSERT INTO pm_activity (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": eid, "name": name, "cfg": cfg},
        )
    await session.execute(
        text(
            "INSERT INTO pm_activity_dialog (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {
            "eid": UNSUP_ACTIVITY_DIALOG,
            "name": "Set Title",
            "cfg": UNSUP_ACTIVITY_DIALOG_CONFIG_YAML,
        },
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": UNSUP_TRANSITION, "name": "Unsup T1", "cfg": UNSUP_TRANSITION_CONFIG_YAML},
    )
    await session.execute(
        text(
            "INSERT INTO pm_transition_action (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {
            "eid": UNSUP_TRANSITION_ACTION,
            "name": "Unsup TA1",
            "cfg": UNSUP_TRANSITION_ACTION_CONFIG_YAML,
        },
    )
    await session.commit()


@pytest.mark.db
async def test_unsupported_transition_action_module_is_skipped_and_reported(
    mariadb_znuny_url: str,
) -> None:
    """A TransitionAction whose Module is not one of the implemented handlers
    (a test-only ``run=None`` module) is skipped — logged, not applied, and its short
    module name is collected into ``unsupported_actions`` — while the
    transition itself still fires and the activity still advances (an
    unsupported action does not abort the whole submission).
    """
    from tiqora.extensions.core import get_registry, register_core, set_registry_for_tests
    from tiqora.extensions.registry import Registry
    from tiqora.process.actions.registry import TRANSITION_ACTIONS, TransitionActionModule

    registry = Registry()
    register_core(registry)
    registry.add(TRANSITION_ACTIONS, TransitionActionModule(name="TestOnlyUnrun", run=None))
    previous = get_registry()
    set_registry_for_tests(registry)
    try:
        await _check_unsupported_action_is_reported(mariadb_znuny_url)
    finally:
        set_registry_for_tests(previous)


async def _check_unsupported_action_is_reported(mariadb_znuny_url: str) -> None:
    url = _mysql_async(mariadb_znuny_url)
    engine = _track_engine(create_async_engine(url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _seed_unsupported_action_process(session)

    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=PROCESS_ENTITY_ID_UNSUP,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        result = await submit_activity_dialog(
            session,
            ticket_id=ticket_id,
            activity_dialog_entity_id=UNSUP_ACTIVITY_DIALOG,
            field_values={"Title": "Whatever"},
            user_id=1,
            sysconfig=sysconfig,
        )

    assert result.activity_changed is True
    assert result.new_activity_entity_id == UNSUP_ACTIVITY_B
    assert result.unsupported_actions == ["TestOnlyUnrun"]


# ---------------------------------------------------------------------------
# _action_ticket_*_set executors: error/edge branches (unresolvable
# name/id, missing required Config key) — not just the happy path.
# ---------------------------------------------------------------------------


# Async engines own a connection pool; created and left undisposed they leak
# idle DB connections against the shared testcontainer for the life of the
# process. Each test registers its engine(s) here and the autouse fixture
# below disposes them at teardown — inside the test's own event loop, so a
# module-scoped shared engine (which would straddle the function-scoped
# asyncio loops) is deliberately avoided.
_ENGINES_TO_DISPOSE: list[AsyncEngine] = []


def _track_engine(engine: AsyncEngine) -> AsyncEngine:
    _ENGINES_TO_DISPOSE.append(engine)
    return engine


@pytest.fixture(autouse=True)
async def _dispose_test_engines() -> AsyncIterator[None]:
    yield
    while _ENGINES_TO_DISPOSE:
        await _ENGINES_TO_DISPOSE.pop().dispose()


def _make_sysconfig_and_factory(url: str) -> tuple[async_sessionmaker[AsyncSession], SysConfig]:
    engine = _track_engine(create_async_engine(_mysql_async(url)))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, _make_sysconfig()


@pytest.mark.db
async def test_action_ticket_state_set_unknown_state_name_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_state_set(
                session, {"State": "no-such-state-xyz"}, ticket_id, 1, sysconfig
            )


@pytest.mark.db
async def test_action_ticket_state_set_missing_config_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_state_set(session, {}, ticket_id, 1, sysconfig)


@pytest.mark.db
async def test_action_ticket_queue_set_unknown_queue_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_queue_set(
                session, {"Queue": "no-such-queue-xyz"}, ticket_id, 1, sysconfig
            )


@pytest.mark.db
async def test_action_ticket_owner_set_unknown_owner_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_owner_set(
                session, {"Owner": "no-such-user-xyz"}, ticket_id, 1, sysconfig
            )


@pytest.mark.db
async def test_action_ticket_priority_set_unknown_priority_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_priority_set(
                session, {"Priority": "no-such-priority-xyz"}, ticket_id, 1, sysconfig
            )


@pytest.mark.db
async def test_action_ticket_title_set_missing_title_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_title_set(session, {}, ticket_id, 1, sysconfig)


@pytest.mark.db
async def test_action_ticket_title_set_empty_title_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_title_set(session, {"Title": ""}, ticket_id, 1, sysconfig)


@pytest.mark.db
async def test_action_ticket_responsible_set_unknown_responsible_raises(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_responsible_set(
                session, {"Responsible": "no-such-user-xyz"}, ticket_id, 1, sysconfig
            )


@pytest.mark.db
async def test_action_ticket_lock_set_missing_config_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_lock_set(session, {}, ticket_id, 1, sysconfig)


@pytest.mark.db
async def test_action_ticket_lock_set_lock_id_resolves_lock_convention(
    mariadb_znuny_url: str,
) -> None:
    """LockID=2 (Znuny convention) means "lock", not "unlock" — exercises the
    ``LockID`` numeric branch instead of the ``Lock`` string branch already
    covered by the happy-path DB test above.
    """
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await _action_ticket_lock_set(session, {"LockID": 2}, ticket_id, 1, sysconfig)

    async with factory() as session:
        lock_id = (
            await session.execute(
                text("SELECT ticket_lock_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()
        assert lock_id == 2


@pytest.mark.db
async def test_action_ticket_article_create_missing_sender_type_raises(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_article_create(session, {"Body": "hello"}, ticket_id, 1, sysconfig)


@pytest.mark.db
async def test_execute_transition_action_propagates_required_field_missing(
    mariadb_znuny_url: str,
) -> None:
    """``execute_transition_action`` dispatch (not just the handler directly)
    lets a handler's RequiredFieldMissing propagate to the caller.
    """
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    action = TransitionActionConfig(
        module="Kernel::System::ProcessManagement::TransitionAction::TicketStateSet",
        config={},
    )
    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await execute_transition_action(
                session,
                action=action,
                ticket_id=ticket_id,
                process_entity_id="Process-irrelevant",
                activity_entity_id="Activity-irrelevant",
                transition_entity_id="Transition-irrelevant",
                user_id=1,
                sysconfig=sysconfig,
            )


# ---------------------------------------------------------------------------
# _apply_dialog_field_changes: called directly (no process needed) to
# exercise the Article-bundle and DynamicField_ branches.
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_apply_dialog_field_changes_creates_article_when_present(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    dialog_config = ActivityDialogConfig(
        fields={"Article": ActivityDialogFieldConfig(config={"CommunicationChannel": "Internal"})}
    )

    async with factory() as session, session.begin():
        await _apply_dialog_field_changes(
            session,
            dialog_config=dialog_config,
            field_values={"Subject": "Edge case subject", "Body": "Edge case body"},
            ticket_id=ticket_id,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        subjects = (
            (
                await session.execute(
                    text(
                        "SELECT a_subject FROM article_data_mime adm"
                        " JOIN article a ON a.id = adm.article_id WHERE a.ticket_id = :tid"
                    ),
                    {"tid": ticket_id},
                )
            )
            .scalars()
            .all()
        )
        assert "Edge case subject" in subjects


@pytest.mark.db
async def test_apply_dialog_field_changes_applies_dynamic_field(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await session.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (9101, 0, 'TiqoraEngineEdgeField', 'Edge Field', 500,"
                " 'Text', 'Ticket', 1, current_timestamp, 1, current_timestamp, 1)"
            )
        )
        await session.commit()
    ticket_id = await _make_ticket(factory, sysconfig)

    dialog_config = ActivityDialogConfig()

    async with factory() as session, session.begin():
        await _apply_dialog_field_changes(
            session,
            dialog_config=dialog_config,
            field_values={"DynamicField_TiqoraEngineEdgeField": "edge-value"},
            ticket_id=ticket_id,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        value = (
            await session.execute(
                text("SELECT value_text FROM dynamic_field_value WHERE field_id = 9101")
            )
        ).scalar_one()
        assert value == "edge-value"


@pytest.mark.db
async def test_apply_dialog_field_changes_unresolvable_queue_raises(
    mariadb_znuny_url: str,
) -> None:
    """A dialog field value that names a queue which doesn't resolve to any
    known Queue now raises ``UnresolvedFieldValue`` instead of being
    silently ignored (fixed — previously the ticket's queue was left
    unchanged with no error surfaced to the caller)."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session:
        before_queue_id = (
            await session.execute(
                text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()

    dialog_config = ActivityDialogConfig()
    async with factory() as session, session.begin():
        with pytest.raises(UnresolvedFieldValue, match="Queue"):
            await _apply_dialog_field_changes(
                session,
                dialog_config=dialog_config,
                field_values={"Queue": "no-such-queue-xyz"},
                ticket_id=ticket_id,
                user_id=1,
                sysconfig=sysconfig,
            )

    async with factory() as session:
        after_queue_id = (
            await session.execute(
                text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()
        assert after_queue_id == before_queue_id


@pytest.mark.db
async def test_apply_dialog_field_changes_skips_blank_optional_fields(
    mariadb_znuny_url: str,
) -> None:
    """A blank ("") value for an optional Queue/State/Priority/Owner/Responsible
    field is skipped, not treated as unresolvable. The frontend seeds every
    declared field with "" and submits them unfiltered, so a dialog whose
    optional fields the agent left empty must still submit (regression: this
    used to raise ``UnresolvedFieldValue`` -> HTTP 400 on every such submit)."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session:
        before = (
            await session.execute(
                text("SELECT queue_id, ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).one()

    dialog_config = ActivityDialogConfig()
    async with factory() as session, session.begin():
        # No exception despite the blank State/Owner/Responsible values.
        await _apply_dialog_field_changes(
            session,
            dialog_config=dialog_config,
            field_values={"State": "", "Owner": "", "Responsible": "", "Title": "Renamed"},
            ticket_id=ticket_id,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session:
        after = (
            await session.execute(
                text("SELECT queue_id, ticket_state_id, title FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).one()
    # Blank fields left the ticket untouched; the one non-blank field applied.
    assert after[0] == before[0]
    assert after[1] == before[1]
    assert after[2] == "Renamed"


# ---------------------------------------------------------------------------
# submit_activity_dialog: validation/error branches
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_submit_activity_dialog_ticket_not_in_process_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        with pytest.raises(TicketNotInProcess):
            await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id="ActivityDialog-whatever",
                field_values={},
                user_id=1,
                sysconfig=sysconfig,
            )


@dataclass(frozen=True)
class _EdgeProcessIds:
    process: str
    activity_a: str
    dialog_1: str
    dialog_perm: str
    dialog_orphan: str


async def _seed_edge_process(session: AsyncSession, *, suffix: str) -> _EdgeProcessIds:
    """Seed a fresh dialog-edge-case process namespaced by *suffix* — the DB
    fixture is session-scoped, so each caller needs its own unique entity ids
    to avoid a duplicate-key collision (see ``_seed_cond_process`` above).
    """
    ids = _EdgeProcessIds(
        process=f"Process-testedge-{suffix}",
        activity_a=f"Activity-edge-a-{suffix}",
        dialog_1=f"ActivityDialog-edge-ad1-{suffix}",
        dialog_perm=f"ActivityDialog-edge-adperm-{suffix}",
        dialog_orphan=f"ActivityDialog-edge-adorphan-{suffix}",
    )
    process_config_yaml = f"""
Description: Test process for dialog edge cases
StartActivity: {ids.activity_a}
StartActivityDialog: {ids.dialog_1}
Path:
  {ids.activity_a}: {{}}
"""
    activity_a_config_yaml = f"""
ActivityDialog:
  '1': {ids.dialog_1}
  '2': {ids.dialog_perm}
"""
    dialog_1_config_yaml = """
DescriptionShort: Edge dialog with a required field
FieldOrder:
- Title
Fields:
  Title:
    Display: '2'
Interface:
- AgentInterface
Permission: ''
"""
    # 'bogus_perm' is not one of PermissionEngine.PERMISSION_KEYS, so
    # PermissionEngine.check() always returns False for it — deterministically
    # exercises the ProcessPermissionDenied branch without seeding group_user rows.
    dialog_perm_config_yaml = """
DescriptionShort: Edge dialog requiring an unsatisfiable permission
FieldOrder: []
Fields: {}
Interface:
- AgentInterface
Permission: bogus_perm
"""
    dialog_orphan_config_yaml = """
DescriptionShort: Edge dialog not referenced by any activity
FieldOrder: []
Fields: {}
Interface:
- AgentInterface
Permission: ''
"""

    now = "current_timestamp"
    await session.execute(
        text(
            "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, 'S1', '{{}}', :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": ids.process, "name": "Test Process Edge", "cfg": process_config_yaml},
    )
    await session.execute(
        text(
            "INSERT INTO pm_activity (entity_id, name, config,"
            f" create_time, create_by, change_time, change_by)"
            f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
        ),
        {"eid": ids.activity_a, "name": "Activity Edge A", "cfg": activity_a_config_yaml},
    )
    for eid, name, cfg in (
        (ids.dialog_1, "Edge Dialog 1", dialog_1_config_yaml),
        (ids.dialog_perm, "Edge Dialog Perm", dialog_perm_config_yaml),
        (ids.dialog_orphan, "Edge Dialog Orphan", dialog_orphan_config_yaml),
    ):
        await session.execute(
            text(
                "INSERT INTO pm_activity_dialog (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": eid, "name": name, "cfg": cfg},
        )
    await session.commit()
    return ids


@pytest.mark.db
async def test_submit_activity_dialog_missing_required_field_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_edge_process(session, suffix="reqfield")
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id=ids.dialog_1,
                field_values={},
                user_id=1,
                sysconfig=sysconfig,
            )


@pytest.mark.db
async def test_submit_activity_dialog_unknown_dialog_id_raises_not_found(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_edge_process(session, suffix="notfound")
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        with pytest.raises(ActivityDialogNotFound):
            await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id="ActivityDialog-does-not-exist-anywhere",
                field_values={},
                user_id=1,
                sysconfig=sysconfig,
            )


@pytest.mark.db
async def test_submit_activity_dialog_dialog_not_offered_by_activity_raises_not_available(
    mariadb_znuny_url: str,
) -> None:
    """The dialog exists in pm_activity_dialog, but the ticket's current
    activity does not list it under its ``ActivityDialog`` config.
    """
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_edge_process(session, suffix="notavail")
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        with pytest.raises(ActivityDialogNotAvailable):
            await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id=ids.dialog_orphan,
                field_values={},
                user_id=1,
                sysconfig=sysconfig,
            )


@pytest.mark.db
async def test_submit_activity_dialog_permission_denied_raises(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        ids = await _seed_edge_process(session, suffix="permdenied")
    ticket_id = await _make_ticket(factory, sysconfig)

    async with factory() as session, session.begin():
        await start_process(
            session,
            ticket_id=ticket_id,
            process_entity_id=ids.process,
            user_id=1,
            sysconfig=sysconfig,
        )

    async with factory() as session, session.begin():
        with pytest.raises(ProcessPermissionDenied):
            await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id=ids.dialog_perm,
                field_values={},
                user_id=1,
                sysconfig=sysconfig,
            )


# ---------------------------------------------------------------------------
# apply_activity_dialog: Znuny field semantics against the DB
# (seed ids 888501-888502, type 30501; the module is in the DB-leak baseline and is restored)
# ---------------------------------------------------------------------------

_DLG_TYPE_ID = 30501  # ticket.type_id is SMALLINT
_DLG_SERVICE_ID = 888501
_DLG_SLA_ID = 888501
_DLG_USER_ID = 888501
_DLG_OTHER_USER_ID = 888502


async def _seed_dialog_refs(session: AsyncSession) -> None:
    await _seed_tiqora_tables(session)
    await session.execute(
        text(
            "INSERT IGNORE INTO ticket_type (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:i, 'DlgTypeX', 1, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"i": _DLG_TYPE_ID},
    )
    await session.execute(
        text(
            "INSERT IGNORE INTO service (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:i, 'DlgServiceX', 1, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"i": _DLG_SERVICE_ID},
    )
    await session.execute(
        text(
            "INSERT IGNORE INTO sla (id, name, first_response_time, update_time,"
            " solution_time, valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:i, 'DlgSlaX', 0, 0, 0, 1, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"i": _DLG_SLA_ID},
    )
    for uid, login in ((_DLG_USER_ID, "dlg-agent-a"), (_DLG_OTHER_USER_ID, "dlg-agent-b")):
        await session.execute(
            text(
                "INSERT IGNORE INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, :l, 'x', 'Dlg', 'Agent', 1, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"i": uid, "l": login},
        )
    await session.commit()


async def _ticket_row(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> Any:
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT type_id, service_id, sla_id, ticket_lock_id, user_id, queue_id"
                    " FROM ticket WHERE id = :t"
                ),
                {"t": ticket_id},
            )
        ).one()


async def _history_types(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT ht.name FROM ticket_history th"
                        " JOIN ticket_history_type ht ON ht.id = th.history_type_id"
                        " WHERE th.ticket_id = :t ORDER BY th.id"
                    ),
                    {"t": ticket_id},
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.db
async def test_dialog_applies_type_service_sla_lock(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    before = await _ticket_row(factory, ticket_id)
    assert before.ticket_lock_id == 1

    dialog = ActivityDialogConfig.from_yaml(
        """
FieldOrder: [TypeID, ServiceID, SLAID, LockID]
Fields:
  TypeID: {Display: '1'}
  ServiceID: {Display: '1'}
  SLAID: {Display: '1'}
  LockID: {Display: '1'}
"""
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={
                "Type": "DlgTypeX",
                "Service": "DlgServiceX",
                "SLA": str(_DLG_SLA_ID),
                "Lock": "lock",
            },
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    after = await _ticket_row(factory, ticket_id)
    assert (after.type_id, after.service_id, after.sla_id) == (
        _DLG_TYPE_ID,
        _DLG_SERVICE_ID,
        _DLG_SLA_ID,
    )
    assert after.ticket_lock_id == 2
    hist = await _history_types(factory, ticket_id)
    assert {"TypeUpdate", "ServiceUpdate", "SLAUpdate", "Lock"} <= set(hist)

    dialog_unlock = ActivityDialogConfig.from_yaml(
        "FieldOrder: [Lock]\nFields:\n  Lock: {Display: '0', DefaultValue: unlock}\n"
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog_unlock,
            field_values={"Lock": "lock"},  # hidden: the default wins over the request
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    assert (await _ticket_row(factory, ticket_id)).ticket_lock_id == 1


@pytest.mark.db
async def test_hidden_default_queue_is_applied_over_submitted_value(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
        junk_id = (
            await session.execute(text("SELECT id FROM queue WHERE name = 'Junk' LIMIT 1"))
        ).scalar_one()
    ticket_id = await _make_ticket(factory, sysconfig)
    assert (await _ticket_row(factory, ticket_id)).queue_id != junk_id

    dialog = ActivityDialogConfig.from_yaml(
        "FieldOrder: [QueueID]\nFields:\n  QueueID: {Display: '0', DefaultValue: Junk}\n"
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={"QueueID": "Raw"},
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    assert (await _ticket_row(factory, ticket_id)).queue_id == junk_id


@pytest.mark.db
async def test_article_visibility_comes_from_dialog_config_not_request(
    mariadb_znuny_url: str,
) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)

    dialog = ActivityDialogConfig.from_yaml(
        """
FieldOrder: [Article]
Fields:
  Article:
    Display: '2'
    Config: {CommunicationChannel: Internal, IsVisibleForCustomer: '0'}
"""
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={
                "Article": {"Subject": "Cfg subject", "Body": "Cfg body"},
                "IsVisibleForCustomer": True,
                "SenderType": "customer",
            },
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a.is_visible_for_customer, st.name FROM article a"
                    " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                    " JOIN article_data_mime m ON m.article_id = a.id"
                    " WHERE a.ticket_id = :t AND m.a_subject = 'Cfg subject'"
                ),
                {"t": ticket_id},
            )
        ).one()
    assert row[0] == 0
    assert row[1] == "agent"

    # Config '1' -> visible; customer interface -> sender type customer.
    dialog_vis = ActivityDialogConfig.from_yaml(
        """
FieldOrder: [Article]
Fields:
  Article:
    Display: '1'
    Config: {CommunicationChannel: Internal, IsVisibleForCustomer: '1'}
"""
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog_vis,
            field_values={"Subject": "Cust subject", "Body": "Cust body"},
            actor=ProcessActor.customer(login="c@example.org", customer_id="ACME"),
            sysconfig=sysconfig,
        )
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a.is_visible_for_customer, st.name FROM article a"
                    " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                    " JOIN article_data_mime m ON m.article_id = a.id"
                    " WHERE a.ticket_id = :t AND m.a_subject = 'Cust subject'"
                ),
                {"t": ticket_id},
            )
        ).one()
    assert row[0] == 1
    assert row[1] == "customer"


@pytest.mark.db
async def test_article_with_only_subject_is_not_created(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    dialog = ActivityDialogConfig.from_yaml(
        "FieldOrder: [Article]\nFields:\n  Article: {Display: '1'}\n"
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={"Subject": "only subject", "Body": ""},
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    async with factory() as session:
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM article_data_mime m JOIN article a ON a.id = m.article_id"
                    " WHERE a.ticket_id = :t AND m.a_subject = 'only subject'"
                ),
                {"t": ticket_id},
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.db
async def test_required_lock_locks_and_sets_owner(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)  # owner 1, unlocked
    dialog = ActivityDialogConfig.from_yaml(
        "RequiredLock: '1'\nFieldOrder: []\nFields: {}\nInterface: [AgentInterface]\n"
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={},
            actor=ProcessActor.agent(_DLG_USER_ID),
            sysconfig=sysconfig,
        )
    row = await _ticket_row(factory, ticket_id)
    assert row.ticket_lock_id == 2
    assert row.user_id == _DLG_USER_ID
    hist = await _history_types(factory, ticket_id)
    assert hist.count("OwnerUpdate") == 1
    assert "Lock" in hist

    # Owner already is the actor and the ticket is unlocked: lock only, no OwnerUpdate row.
    ticket2 = await _make_ticket(factory, sysconfig)
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket2,
            dialog=dialog,
            field_values={},
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    row2 = await _ticket_row(factory, ticket2)
    assert (row2.ticket_lock_id, row2.user_id) == (2, 1)
    assert "OwnerUpdate" not in await _history_types(factory, ticket2)

    # Locked by the owner: allowed again, nothing new written.
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket2,
            dialog=dialog,
            field_values={},
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )


@pytest.mark.db
async def test_required_lock_refuses_foreign_lock(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    async with factory() as session:
        await session.execute(
            text("UPDATE ticket SET ticket_lock_id = 2, user_id = :u WHERE id = :t"),
            {"u": _DLG_OTHER_USER_ID, "t": ticket_id},
        )
        await session.commit()
    dialog = ActivityDialogConfig.from_yaml(
        "RequiredLock: '1'\nFieldOrder: [Title]\nFields:\n  Title: {Display: '1'}\n"
    )
    async with factory() as session, session.begin():
        with pytest.raises(ProcessPermissionDenied):
            await apply_activity_dialog(
                session,
                ticket_id=ticket_id,
                dialog=dialog,
                field_values={"Title": "must not apply"},
                actor=ProcessActor.agent(_DLG_USER_ID),
                sysconfig=sysconfig,
            )
    async with factory() as session:
        title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": ticket_id})
        ).scalar_one()
    assert title != "must not apply"

    # RequiredLock is an agent-interface rule: the customer interface is not blocked.
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=ActivityDialogConfig.from_yaml(
                "RequiredLock: '1'\nFieldOrder: []\nFields: {}\n"
            ),
            field_values={},
            actor=ProcessActor.customer(login="c@example.org", customer_id="ACME"),
            sysconfig=sysconfig,
        )


@pytest.mark.db
async def test_skip_fields_and_no_required_lock_on_create(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    before_title = "BPM test ticket"
    dialog = ActivityDialogConfig.from_yaml(
        "RequiredLock: '1'\nFieldOrder: [Title, DynamicField_Skip]\n"
        "Fields:\n  Title: {Display: '2'}\n  DynamicField_Skip: {Display: '1'}\n"
    )
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={"Title": "consumed by create"},
            actor=ProcessActor.agent(_DLG_USER_ID),
            sysconfig=sysconfig,
            skip_fields=frozenset({"Title"}),
            apply_required_lock=False,
        )
    row = await _ticket_row(factory, ticket_id)
    assert row.ticket_lock_id == 1  # RequiredLock not applied
    async with factory() as session:
        title = (
            await session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": ticket_id})
        ).scalar_one()
    assert title == before_title  # skipped field not written again

    # Required check still sees the skipped field.
    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing, match="Title"):
            await apply_activity_dialog(
                session,
                ticket_id=ticket_id,
                dialog=dialog,
                field_values={},
                actor=ProcessActor.agent(1),
                sysconfig=sysconfig,
                skip_fields=frozenset({"Title"}),
                apply_required_lock=False,
            )


# ---------------------------------------------------------------------------
# Dialog PendingTime (AgentTicketProcess.pm:1253-1330, 5016-5036, 5554-5575;
# Ticket.pm:3954-4095 TicketPendingTimeSet). Uses only Znuny's stock states
# ("new" 1, "open" 4, "pending reminder" 6) — no extra seed rows.
# ---------------------------------------------------------------------------

_PT_VALUE = "2026-10-12T08:00:00Z"
_PT_EPOCH = 1791792000  # 2026-10-12 08:00:00 UTC
_PT_HISTORY = "%%2026-10-12 08:00"

_PT_DIALOG = """
FieldOrder: [State, PendingTime]
Fields:
  State: {Display: '2'}
  PendingTime: {Display: '1'}
"""


async def _pending_state(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> Any:
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT t.until_time, t.ticket_state_id, t.change_by FROM ticket t"
                    " WHERE t.id = :t"
                ),
                {"t": ticket_id},
            )
        ).one()


async def _pending_history(
    factory: async_sessionmaker[AsyncSession], ticket_id: int
) -> list[tuple[str, int]]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT th.name, th.create_by FROM ticket_history th"
                    " JOIN ticket_history_type ht ON ht.id = th.history_type_id"
                    " WHERE th.ticket_id = :t AND ht.name = 'SetPendingTime' ORDER BY th.id"
                ),
                {"t": ticket_id},
            )
        ).all()
    return [(str(r[0]), int(r[1])) for r in rows]


async def _submit_pending(
    mariadb_znuny_url: str,
    yaml_cfg: str,
    values: dict[str, Any],
    *,
    agent_id: int = 1,
) -> tuple[async_sessionmaker[AsyncSession], int]:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=ActivityDialogConfig.from_yaml(yaml_cfg),
            field_values=values,
            actor=ProcessActor.agent(agent_id),
            sysconfig=sysconfig,
        )
    return factory, ticket_id


@pytest.mark.db
async def test_pending_time_applied_when_target_state_is_pending(mariadb_znuny_url: str) -> None:
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, _PT_DIALOG, {"State": "pending reminder", "PendingTime": _PT_VALUE}
    )
    row = await _pending_state(factory, ticket_id)
    assert row.until_time == _PT_EPOCH
    assert row.ticket_state_id == 6
    assert await _pending_history(factory, ticket_id) == [(_PT_HISTORY, 1)]


@pytest.mark.db
async def test_pending_time_offset_is_converted_to_utc_and_seconds_dropped(
    mariadb_znuny_url: str,
) -> None:
    # 10:00:42+02:00 == 08:00 UTC; Znuny stores minutes only (seconds ":00", Ticket.pm:4034).
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url,
        _PT_DIALOG,
        {"State": "pending reminder", "PendingTime": "2026-10-12T10:00:42+02:00"},
    )
    assert (await _pending_state(factory, ticket_id)).until_time == _PT_EPOCH
    assert await _pending_history(factory, ticket_id) == [(_PT_HISTORY, 1)]


@pytest.mark.db
async def test_naive_pending_time_is_utc(mariadb_znuny_url: str) -> None:
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url,
        _PT_DIALOG,
        {"State": "pending reminder", "PendingTime": "2026-10-12T08:00:00"},
    )
    assert (await _pending_state(factory, ticket_id)).until_time == _PT_EPOCH


@pytest.mark.db
async def test_pending_time_ignored_for_open_state(mariadb_znuny_url: str) -> None:
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, _PT_DIALOG, {"State": "open", "PendingTime": _PT_VALUE}
    )
    row = await _pending_state(factory, ticket_id)
    assert row.until_time == 0
    assert row.ticket_state_id == 4
    # Only the reset row of the state change to a non-pending state (golden-validated in
    # change_state); the dialog PendingTime itself wrote nothing.
    assert await _pending_history(factory, ticket_id) == [("%%00-00-00 00:00", 1)]
    # An invalid value is not even read for a non-pending target (AgentTicketProcess.pm:1303).
    factory2, ticket_id2 = await _submit_pending(
        mariadb_znuny_url, _PT_DIALOG, {"State": "open", "PendingTime": "garbage"}
    )
    assert (await _pending_state(factory2, ticket_id2)).until_time == 0


@pytest.mark.db
async def test_pending_time_uses_dialog_state_default_when_state_hidden(
    mariadb_znuny_url: str,
) -> None:
    hidden = """
FieldOrder: [State, PendingTime]
Fields:
  State: {Display: '0', DefaultValue: pending reminder}
  PendingTime: {Display: '1'}
"""
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, hidden, {"State": "open", "PendingTime": _PT_VALUE}
    )
    row = await _pending_state(factory, ticket_id)
    assert row.ticket_state_id == 6  # the hidden default wins over the request
    assert row.until_time == _PT_EPOCH
    assert await _pending_history(factory, ticket_id) == [(_PT_HISTORY, 1)]


@pytest.mark.db
async def test_pending_time_falls_back_to_default_for_visible_empty_state(
    mariadb_znuny_url: str,
) -> None:
    # Fallback 1 (AgentTicketProcess.pm:1273): no submitted State -> the field's DefaultValue.
    visible = """
FieldOrder: [State, PendingTime]
Fields:
  State: {Display: '1', DefaultValue: pending reminder}
  PendingTime: {Display: '1'}
"""
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, visible, {"State": "", "PendingTime": _PT_VALUE}
    )
    assert (await _pending_state(factory, ticket_id)).until_time == _PT_EPOCH


@pytest.mark.db
async def test_pending_time_falls_back_to_dialog_state_id_default(
    mariadb_znuny_url: str,
) -> None:
    # Fallback 2 (AgentTicketProcess.pm:1279-1284): the StateID field's DefaultValue is an id.
    cfg = """
FieldOrder: [StateID, PendingTime]
Fields:
  StateID: {Display: '1', DefaultValue: '6'}
  PendingTime: {Display: '1'}
"""
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, cfg, {"StateID": "", "PendingTime": _PT_VALUE}
    )
    assert (await _pending_state(factory, ticket_id)).until_time == _PT_EPOCH
    assert await _pending_history(factory, ticket_id) == [(_PT_HISTORY, 1)]


@pytest.mark.db
async def test_target_state_ignores_unknown_numeric_state_and_uses_state_id_default(
    mariadb_znuny_url: str,
) -> None:
    from tiqora.process.engine import _dialog_target_state_id  # noqa: PLC2701

    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    dialog = ActivityDialogConfig.from_yaml(
        "FieldOrder: [StateID]\nFields:\n  StateID: {Display: '1', DefaultValue: '6'}\n"
    )
    async with factory() as session:
        # 999999 is no state: StateGet ID finds nothing, so fallback 2 (the id default) wins.
        unknown = await _dialog_target_state_id(
            session, ticket_id=ticket_id, dialog=dialog, values={"State": "999999"}
        )
        known = await _dialog_target_state_id(
            session, ticket_id=ticket_id, dialog=dialog, values={"State": "4"}
        )
    assert unknown == 6
    assert known == 4


@pytest.mark.db
async def test_pending_time_uses_current_ticket_state_without_state_value(
    mariadb_znuny_url: str,
) -> None:
    # Fallback 3 (:1291): no State submitted/default -> the ticket's own state, here "new".
    factory, ticket_id = await _submit_pending(
        mariadb_znuny_url, _PT_DIALOG.replace("'2'", "'1'", 1), {"PendingTime": _PT_VALUE}
    )
    assert (await _pending_state(factory, ticket_id)).until_time == 0  # "new" is not pending
    assert await _pending_history(factory, ticket_id) == []


@pytest.mark.db
async def test_invalid_pending_time_is_400(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    for bad in ("not-a-date", "2026-13-45T99:00:00Z", 12345):
        async with factory() as session, session.begin():
            with pytest.raises(UnresolvedFieldValue, match="PendingTime"):
                await apply_activity_dialog(
                    session,
                    ticket_id=ticket_id,
                    dialog=ActivityDialogConfig.from_yaml(_PT_DIALOG),
                    field_values={"State": "pending reminder", "PendingTime": bad},
                    actor=ProcessActor.agent(1),
                    sysconfig=sysconfig,
                )
    row = await _pending_state(factory, ticket_id)
    assert (row.until_time, row.ticket_state_id) == (0, 1)  # nothing written


@pytest.mark.db
async def test_required_pending_time_missing(mariadb_znuny_url: str) -> None:
    required = _PT_DIALOG.replace("PendingTime: {Display: '1'}", "PendingTime: {Display: '2'}")
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing, match="PendingTime"):
            await apply_activity_dialog(
                session,
                ticket_id=ticket_id,
                dialog=ActivityDialogConfig.from_yaml(required),
                field_values={"State": "pending reminder", "PendingTime": ""},
                actor=ProcessActor.agent(1),
                sysconfig=sysconfig,
            )
    assert (await _pending_state(factory, ticket_id)).ticket_state_id == 1

    # Required only matters for a pending target: with "open" the dialog goes through.
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=ActivityDialogConfig.from_yaml(required),
            field_values={"State": "open"},
            actor=ProcessActor.agent(1),
            sysconfig=sysconfig,
        )
    assert (await _pending_state(factory, ticket_id)).ticket_state_id == 4


@pytest.mark.db
async def test_customer_interface_never_applies_pending_time(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    required = _PT_DIALOG.replace("PendingTime: {Display: '1'}", "PendingTime: {Display: '2'}")
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=ActivityDialogConfig.from_yaml(required),
            field_values={"State": "pending reminder", "PendingTime": _PT_VALUE},
            actor=ProcessActor.customer(login="customer@example.org", customer_id="EX-1"),
            sysconfig=sysconfig,
        )
    row = await _pending_state(factory, ticket_id)
    assert row.ticket_state_id == 6
    assert row.until_time == 0  # CustomerTicketProcess.pm:75 skips PendingTime
    assert await _pending_history(factory, ticket_id) == []


# ---------------------------------------------------------------------------
# Task 16b: clearing dynamic fields, required checks for empties, untouched current values.
# ---------------------------------------------------------------------------

_CLEAR_DF = "DlgClearField"
_CLEAR_DIALOG = (
    "FieldOrder: [Queue, Priority, Responsible, DynamicField_DlgClearField]\n"
    "Fields:\n  Queue: {Display: '1'}\n  Priority: {Display: '1'}\n  Responsible: {Display: '1'}\n"
    "  DynamicField_DlgClearField: {Display: '%s'}\n"
)


async def _seed_clear_field(session: AsyncSession) -> None:
    await _seed_dialog_refs(session)
    await session.execute(
        text(
            "INSERT IGNORE INTO dynamic_field (id, internal_field, name, label, field_order,"
            " field_type, object_type, config, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (9302, 0, :n, 'Clear me', 500, 'Text', 'Ticket', '---\n', 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"n": _CLEAR_DF},
    )
    await session.commit()


async def _df_rows(factory: async_sessionmaker[AsyncSession], ticket_id: int) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT value_text FROM dynamic_field_value"
                        " WHERE field_id = 9302 AND object_id = :t"
                    ),
                    {"t": ticket_id},
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.db
async def test_explicit_empty_list_clears_a_dynamic_field(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_clear_field(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    dialog = ActivityDialogConfig.from_yaml(_CLEAR_DIALOG % "1")
    actor = ProcessActor.agent(_DLG_USER_ID)

    async def submit(values: dict[str, Any]) -> None:
        async with factory() as session, session.begin():
            await apply_activity_dialog(
                session,
                ticket_id=ticket_id,
                dialog=dialog,
                field_values=values,
                actor=actor,
                sysconfig=sysconfig,
            )

    await submit({f"DynamicField_{_CLEAR_DF}": ["kept?"]})
    assert await _df_rows(factory, ticket_id) == ["kept?"]
    await submit({})  # key absent: the field is untouched (the old frontend behaviour)
    assert await _df_rows(factory, ticket_id) == ["kept?"]
    await submit({f"DynamicField_{_CLEAR_DF}": []})  # explicit empty: cleared like Znuny
    assert await _df_rows(factory, ticket_id) == []


@pytest.mark.db
async def test_required_dynamic_field_still_rejects_empty_list(mariadb_znuny_url: str) -> None:
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_clear_field(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    dialog = ActivityDialogConfig.from_yaml(_CLEAR_DIALOG % "2")
    for value in ([], [""]):
        async with factory() as session, session.begin():
            with pytest.raises(RequiredFieldMissing, match=_CLEAR_DF):
                await apply_activity_dialog(
                    session,
                    ticket_id=ticket_id,
                    dialog=dialog,
                    field_values={f"DynamicField_{_CLEAR_DF}": value},
                    actor=ProcessActor.agent(_DLG_USER_ID),
                    sysconfig=sysconfig,
                )
    assert await _df_rows(factory, ticket_id) == []


@pytest.mark.db
async def test_unchanged_queue_priority_responsible_write_nothing(mariadb_znuny_url: str) -> None:
    """The dialog preselects the ticket's current values, so they come back unchanged
    (Znuny's TicketQueueSet/PrioritySet/ResponsibleSet return early for the same value)."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_clear_field(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    async with factory() as session:
        queue_id, prio_id, resp_id = (
            await session.execute(
                text(
                    "SELECT queue_id, ticket_priority_id, responsible_user_id FROM ticket"
                    " WHERE id = :t"
                ),
                {"t": ticket_id},
            )
        ).one()
    before = await _history_types(factory, ticket_id)
    dialog = ActivityDialogConfig.from_yaml(_CLEAR_DIALOG % "1")
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={
                "Queue": str(queue_id),
                "Priority": str(prio_id),
                "Responsible": str(resp_id),
            },
            actor=ProcessActor.agent(_DLG_USER_ID),
            sysconfig=sysconfig,
        )
    assert await _history_types(factory, ticket_id) == before


# ---------------------------------------------------------------------------
# Same-value guards for Title and customer (final review I1)
# ---------------------------------------------------------------------------

_OLD_CHANGE_TIME = "2020-01-01 00:00:00"


async def _age_ticket(
    factory: async_sessionmaker[AsyncSession],
    ticket_id: int,
    *,
    customer_id: str | None = None,
    customer_user_id: str | None = None,
) -> None:
    """Pin change_time to a known old value (a bump is then visible) and set the customer."""
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE ticket SET change_time = :ct, customer_id = :c, customer_user_id = :u"
                " WHERE id = :t"
            ),
            {"ct": _OLD_CHANGE_TIME, "c": customer_id, "u": customer_user_id, "t": ticket_id},
        )


async def _customer_and_change_time(
    factory: async_sessionmaker[AsyncSession], ticket_id: int
) -> tuple[Any, Any, str]:
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT customer_id, customer_user_id, change_time FROM ticket WHERE id = :t"),
                {"t": ticket_id},
            )
        ).one()
    return row[0], row[1], str(row[2])


async def _history_names(
    factory: async_sessionmaker[AsyncSession], ticket_id: int, history_type: str
) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT th.name FROM ticket_history th"
                        " JOIN ticket_history_type ht ON ht.id = th.history_type_id"
                        " WHERE th.ticket_id = :t AND ht.name = :n ORDER BY th.id"
                    ),
                    {"t": ticket_id, "n": history_type},
                )
            )
            .scalars()
            .all()
        )


_TITLE_CUSTOMER_DIALOG = (
    "FieldOrder: [Title, CustomerUserID, CustomerID]\n"
    "Fields:\n  Title: {Display: '1'}\n  CustomerUserID: {Display: '1'}\n"
    "  CustomerID: {Display: '1'}\n"
    "Interface: [AgentInterface]\n"
)


@pytest.mark.db
async def test_unchanged_title_and_customer_write_nothing(mariadb_znuny_url: str) -> None:
    """A dialog submitted with the prefilled Title and customer writes no TitleUpdate /
    CustomerUpdate row and does not bump change_time (Ticket.pm:1883 TicketTitleUpdate,
    AgentTicketProcess.pm:5770-5783)."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_dialog_refs(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    await _age_ticket(factory, ticket_id, customer_id="cust-a", customer_user_id="cu-a")
    before = await _history_types(factory, ticket_id)
    dialog = ActivityDialogConfig.from_yaml(_TITLE_CUSTOMER_DIALOG)
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={
                "Title": "BPM test ticket",
                "CustomerUserID": "cu-a",
                "CustomerID": "cust-a",
            },
            actor=ProcessActor.agent(_DLG_USER_ID),
            sysconfig=sysconfig,
        )
    assert await _history_types(factory, ticket_id) == before
    assert await _customer_and_change_time(factory, ticket_id) == (
        "cust-a",
        "cu-a",
        _OLD_CHANGE_TIME,
    )

    # A changed customer user still writes (both columns, one CustomerUpdate row).
    async with factory() as session, session.begin():
        await apply_activity_dialog(
            session,
            ticket_id=ticket_id,
            dialog=dialog,
            field_values={
                "Title": "BPM test ticket",
                "CustomerUserID": "cu-b",
                "CustomerID": "cust-a",
            },
            actor=ProcessActor.agent(_DLG_USER_ID),
            sysconfig=sysconfig,
        )
    assert await _history_names(factory, ticket_id, "CustomerUpdate") == [
        "%%CustomerID=cust-a;CustomerUser=cu-b;"
    ]
    assert await _history_names(factory, ticket_id, "TitleUpdate") == []


@pytest.mark.db
async def test_title_set_action_same_title_is_noop(mariadb_znuny_url: str) -> None:
    """TransitionAction TicketTitleSet with the current title: TicketTitleUpdate returns
    early (no UPDATE, no history)."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    await _age_ticket(factory, ticket_id)
    async with factory() as session, session.begin():
        await _action_ticket_title_set(
            session, {"Title": "BPM test ticket"}, ticket_id, 1, sysconfig
        )
    assert await _history_names(factory, ticket_id, "TitleUpdate") == []
    assert (await _customer_and_change_time(factory, ticket_id))[2] == _OLD_CHANGE_TIME

    async with factory() as session, session.begin():
        await _action_ticket_title_set(session, {"Title": "Renamed"}, ticket_id, 1, sysconfig)
    assert await _history_names(factory, ticket_id, "TitleUpdate") == ["%%BPM test ticket%%Renamed"]


@pytest.mark.db
async def test_customer_set_action_per_key_like_znuny(mariadb_znuny_url: str) -> None:
    """TransitionAction TicketCustomerSet (TicketCustomerSet.pm:103-190): only ``User`` set
    leaves customer_id alone; an equal value is skipped; each changed key is its own
    TicketCustomerSet call with its own history row."""
    factory, sysconfig = _make_sysconfig_and_factory(mariadb_znuny_url)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    ticket_id = await _make_ticket(factory, sysconfig)
    await _age_ticket(factory, ticket_id, customer_id="cust-a", customer_user_id="cu-a")

    async with factory() as session, session.begin():
        await _action_ticket_customer_set(
            session, {"CustomerID": "cust-a", "User": "cu-a"}, ticket_id, 1, sysconfig
        )
    assert await _history_names(factory, ticket_id, "CustomerUpdate") == []
    assert await _customer_and_change_time(factory, ticket_id) == (
        "cust-a",
        "cu-a",
        _OLD_CHANGE_TIME,
    )

    async with factory() as session, session.begin():
        await _action_ticket_customer_set(session, {"User": "cu-b"}, ticket_id, 1, sysconfig)
    customer_id, customer_user_id, _ = await _customer_and_change_time(factory, ticket_id)
    assert (customer_id, customer_user_id) == ("cust-a", "cu-b")
    assert await _history_names(factory, ticket_id, "CustomerUpdate") == ["%%CustomerUser=cu-b;"]

    async with factory() as session, session.begin():
        await _action_ticket_customer_set(
            session, {"CustomerID": "cust-c", "CustomerUserID": "cu-c"}, ticket_id, 1, sysconfig
        )
    assert await _history_names(factory, ticket_id, "CustomerUpdate") == [
        "%%CustomerUser=cu-b;",
        "%%CustomerID=cust-c;",
        "%%CustomerUser=cu-c;",
    ]

    async with factory() as session, session.begin():
        with pytest.raises(RequiredFieldMissing):
            await _action_ticket_customer_set(session, {"No": ""}, ticket_id, 1, sysconfig)
