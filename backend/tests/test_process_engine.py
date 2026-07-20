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
