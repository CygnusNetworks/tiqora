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
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.process.engine import start_process, submit_activity_dialog
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

TRANSITION_CONFIG_YAML = "ConditionLinking: and\n"

TRANSITION_ACTION_CONFIG_YAML = """
Config:
  State: closed successful
Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet
"""


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


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
            ticket_id BIGINT NOT NULL,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
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
    engine = create_async_engine(url)
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
    engine = create_async_engine(url)
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
    engine = create_async_engine(url)
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

UNSUP_TRANSITION_CONFIG_YAML = "ConditionLinking: and\n"

# TicketSLASet is a documented deferred/unsupported TransitionAction module
# (see engine.py's module docstring) — never implemented, always skipped.
UNSUP_TRANSITION_ACTION_CONFIG_YAML = """
Config:
  SLA: some sla
Module: Kernel::System::ProcessManagement::TransitionAction::TicketSLASet
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
    (e.g. TicketSLASet) is skipped — logged, not applied, and its short
    module name is collected into ``unsupported_actions`` — while the
    transition itself still fires and the activity still advances (an
    unsupported action does not abort the whole submission).
    """
    url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(url)
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
    assert result.unsupported_actions == ["TicketSLASet"]
