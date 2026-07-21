"""DB integration tests for ``/api/v1/process/*`` REST endpoints (subtask 3).

Seeds a minimal 2-activity / 1-dialog / 1-transition process directly into
``pm_process``/``pm_activity``/``pm_activity_dialog``/``pm_transition``/
``pm_transition_action`` (raw SQL — same approach as ``test_process_engine.py``,
subtask 2 — there is no admin write path for these tables yet), then drives
it through the REST layer with a real ``httpx.AsyncClient`` against
``create_app(Settings(environment="test"))``, ``get_current_user``/``get_db``
dependency overrides — mirroring ``test_calendar_api.py`` exactly (real
MariaDB testcontainer via ``mariadb_znuny_url``, not SQLite/mocked).
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 1, 1, 12, 0, 0)

PROCESS_CONFIG_YAML = """
Description: Test process
StartActivity: {activity_a}
StartActivityDialog: {dialog}
Path:
  {activity_a}:
    {transition}:
      ActivityEntityID: {activity_b}
      TransitionAction:
      - {action}
  {activity_b}: {{}}
"""

ACTIVITY_A_CONFIG_YAML = """
ActivityDialog:
  '1': {dialog}
"""

ACTIVITY_B_CONFIG_YAML = "ActivityDialog: {}\n"

ACTIVITY_DIALOG_CONFIG_YAML = """
DescriptionShort: Set title
DescriptionLong: Set the ticket title
FieldOrder:
- Title
Fields:
  Title:
    Display: '1'
    DescriptionShort: Ticket title
SubmitButtonText: Submit
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
    """Create tiqora_* tables in the testcontainer DB (Alembic not run there)
    — mirrors ``test_process_engine._seed_tiqora_tables``. ``update_dynamic_field``
    (called by ``start_process``/``submit_activity_dialog``) writes to
    ``tiqora_event_outbox``/``tiqora_cache_invalidation`` as part of its
    normal Znuny-fidelity side effects.
    """
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


def _seed(sync_url: str) -> dict[str, Any]:
    """Seed two agents (one with rw on the ticket's queue, one with none), a
    queue/ticket, and one minimal BPM process — mirrors
    ``test_calendar_service._seed`` / ``test_process_engine._seed_process``.
    """
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000

    process_entity_id = f"Process-{ns}"
    activity_a = f"Activity-a-{ns}"
    activity_b = f"Activity-b-{ns}"
    dialog_entity_id = f"ActivityDialog-{ns}"
    transition_entity_id = f"Transition-{ns}"
    action_entity_id = f"TransitionAction-{ns}"

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :login, 'x', 'Proc', 'Agent', 1, :t, 1, :t, 1),"
                " (:uid2, :login2, 'x', 'No', 'Access', 1, :t, 1, :t, 1)"
            ),
            {
                "uid": base + 1,
                "login": f"proc.agent.{ns}",
                "uid2": base + 2,
                "login2": f"proc.denied.{ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:g1, :n1, 1, :t, 1, :t, 1)"
            ),
            {"g1": base + 10, "n1": f"proc-allowed-{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'rw', :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "gid": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q1, :qn1, :g1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q1": base + 30, "qn1": f"ProcQueue-{ns}", "g1": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :tn, 'Process test ticket', :qid, 1, 1, :uid, :uid, 3, 4,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": base + 40, "tn": f"{base + 40}", "qid": base + 30, "uid": base + 1, "t": NOW},
        )

        now = "current_timestamp"
        conn.execute(
            text(
                "INSERT INTO pm_process (entity_id, name, state_entity_id, layout, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, 'S1', '{{}}', :cfg, {now}, 1, {now}, 1)"
            ),
            {
                "eid": process_entity_id,
                "name": f"Test Process {ns}",
                "cfg": PROCESS_CONFIG_YAML.format(
                    activity_a=activity_a,
                    activity_b=activity_b,
                    dialog=dialog_entity_id,
                    transition=transition_entity_id,
                    action=action_entity_id,
                ),
            },
        )
        for eid, name, cfg in (
            (activity_a, "Activity A", ACTIVITY_A_CONFIG_YAML.format(dialog=dialog_entity_id)),
            (activity_b, "Activity B", ACTIVITY_B_CONFIG_YAML),
        ):
            conn.execute(
                text(
                    "INSERT INTO pm_activity (entity_id, name, config,"
                    f" create_time, create_by, change_time, change_by)"
                    f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
                ),
                {"eid": eid, "name": name, "cfg": cfg},
            )
        conn.execute(
            text(
                "INSERT INTO pm_activity_dialog (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": dialog_entity_id, "name": "Set Title", "cfg": ACTIVITY_DIALOG_CONFIG_YAML},
        )
        conn.execute(
            text(
                "INSERT INTO pm_transition (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": transition_entity_id, "name": "T1", "cfg": TRANSITION_CONFIG_YAML},
        )
        conn.execute(
            text(
                "INSERT INTO pm_transition_action (entity_id, name, config,"
                f" create_time, create_by, change_time, change_by)"
                f" VALUES (:eid, :name, :cfg, {now}, 1, {now}, 1)"
            ),
            {"eid": action_entity_id, "name": "TA1", "cfg": TRANSITION_ACTION_CONFIG_YAML},
        )

    return {
        "agent_id": base + 1,
        "agent_login": f"proc.agent.{ns}",
        "denied_agent_id": base + 2,
        "denied_agent_login": f"proc.denied.{ns}",
        "ticket_id": base + 40,
        "process_entity_id": process_entity_id,
        "activity_a": activity_a,
        "activity_b": activity_b,
        "dialog_entity_id": dialog_entity_id,
        "transition_entity_id": transition_entity_id,
    }


async def _client_for(mariadb_znuny_url: str, ids: dict[str, Any], *, agent_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    login = ids["agent_login"] if agent_id == ids["agent_id"] else ids["denied_agent_login"]
    fake_user = AuthenticatedUser(
        id=agent_id,
        login=login,
        first_name="Proc",
        last_name="Agent",
        auth_method="session",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.db
@pytest.mark.asyncio
async def test_process_list_detail_and_ticket_lifecycle_via_rest(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids, agent_id=ids["agent_id"])
    async with AsyncSession(engine) as session:
        await _seed_tiqora_tables(session)

    async with client:
        # 1. list processes
        resp = await client.get("/api/v1/process/")
        assert resp.status_code == 200
        entity_ids = {p["entity_id"] for p in resp.json()}
        assert ids["process_entity_id"] in entity_ids

        # 2. get process detail
        resp = await client.get(f"/api/v1/process/{ids['process_entity_id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["entity_id"] == ids["process_entity_id"]
        assert detail["start_activity_entity_id"] == ids["activity_a"]
        activity_entity_ids = {a["entity_id"] for a in detail["activities"]}
        assert {ids["activity_a"], ids["activity_b"]} <= activity_entity_ids
        activity_a = next(a for a in detail["activities"] if a["entity_id"] == ids["activity_a"])
        assert [d["entity_id"] for d in activity_a["activity_dialogs"]] == [ids["dialog_entity_id"]]

        # 3. get unknown process -> 404
        resp = await client.get("/api/v1/process/Process-does-not-exist")
        assert resp.status_code == 404

        # 4. ticket state before starting a process: 200, all-None
        resp = await client.get(f"/api/v1/process/ticket/{ids['ticket_id']}/state")
        assert resp.status_code == 200
        state = resp.json()
        assert state["process_entity_id"] is None
        assert state["activity_entity_id"] is None
        assert state["available_dialogs"] == []

        # 5. unknown ticket -> 404
        resp = await client.get("/api/v1/process/ticket/999999999/state")
        assert resp.status_code == 404

        # 6. activity-dialog detail
        resp = await client.get(f"/api/v1/process/activity-dialog/{ids['dialog_entity_id']}")
        assert resp.status_code == 200
        dialog = resp.json()
        assert dialog["entity_id"] == ids["dialog_entity_id"]
        assert dialog["field_order"] == ["Title"]
        assert "Title" in dialog["fields"]
        assert dialog["fields"]["Title"]["display"] == "1"

        # unknown dialog -> 404
        resp = await client.get("/api/v1/process/activity-dialog/ActivityDialog-does-not-exist")
        assert resp.status_code == 404

        # 7. start the process
        resp = await client.post(
            f"/api/v1/process/ticket/{ids['ticket_id']}/start",
            json={"process_entity_id": ids["process_entity_id"]},
        )
        assert resp.status_code == 200, resp.text
        state = resp.json()
        assert state["process_entity_id"] == ids["process_entity_id"]
        assert state["activity_entity_id"] == ids["activity_a"]
        assert [d["entity_id"] for d in state["available_dialogs"]] == [ids["dialog_entity_id"]]
        assert state["available_transitions_count"] == 1

        # starting again -> 409 (already in process)
        resp = await client.post(
            f"/api/v1/process/ticket/{ids['ticket_id']}/start",
            json={"process_entity_id": ids["process_entity_id"]},
        )
        assert resp.status_code == 409

        # starting on an unknown ticket -> 404
        resp = await client.post(
            "/api/v1/process/ticket/999999999/start",
            json={"process_entity_id": "Process-does-not-exist"},
        )
        assert resp.status_code == 404

        # 8. submit the dialog -> advances activity
        resp = await client.post(
            f"/api/v1/process/ticket/{ids['ticket_id']}/submit",
            json={
                "activity_dialog_entity_id": ids["dialog_entity_id"],
                "field_values": {"Title": "Updated via REST"},
            },
        )
        assert resp.status_code == 200, resp.text
        submit_out = resp.json()
        assert submit_out["activity_changed"] is True
        assert submit_out["new_activity_entity_id"] == ids["activity_b"]
        assert submit_out["transition_entity_id"] == ids["transition_entity_id"]
        assert submit_out["unsupported_actions"] == []
        assert submit_out["state"]["activity_entity_id"] == ids["activity_b"]
        assert submit_out["state"]["available_dialogs"] == []

        # 9. ticket state now reflects the new activity
        resp = await client.get(f"/api/v1/process/ticket/{ids['ticket_id']}/state")
        assert resp.status_code == 200
        state = resp.json()
        assert state["activity_entity_id"] == ids["activity_b"]

        # 10. submitting an unknown dialog entity id -> 404
        resp = await client.post(
            f"/api/v1/process/ticket/{ids['ticket_id']}/submit",
            json={"activity_dialog_entity_id": "ActivityDialog-does-not-exist", "field_values": {}},
        )
        assert resp.status_code == 404

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_process_permission_denied_via_rest(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids, agent_id=ids["denied_agent_id"])

    async with client:
        # A user with no group membership on the ticket's queue is denied
        # even reading the ticket's process state (mirrors tickets.py's ro gate).
        resp = await client.get(f"/api/v1/process/ticket/{ids['ticket_id']}/state")
        assert resp.status_code == 403

        resp = await client.post(
            f"/api/v1/process/ticket/{ids['ticket_id']}/start",
            json={"process_entity_id": ids["process_entity_id"]},
        )
        assert resp.status_code == 403

    await engine.dispose()
