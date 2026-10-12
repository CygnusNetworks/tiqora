"""``ExecuteInvoker`` process action (G14) on both dialects.

The action is dispatched through ``execute_transition_action`` with the handler registered the
way the process merge wires it (``_ACTION_HANDLERS``). Seed band 51390-51409: webservice
51390, ticket 51391. Jobs the module creates are removed with ``snapshot_marks`` /
``delete_above_marks``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from tests._gi_fixtures import (
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_GI_REQUESTER_ENABLED
from tiqora.gi.process_action import execute_invoker_action
from tiqora.process import engine as process_engine
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

WS = 51390
TICKET = 51391
MODULE = "Kernel::System::ProcessManagement::TransitionAction::ExecuteInvoker"
_SEEDED: dict[str, dict[str, int]] = {}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    cleanup_webservices(conn, [WS, WS + 1])
    conn.execute(text("DELETE FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'"))
    conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": TICKET})


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    if sync_url not in _SEEDED:
        TiqoraBase.metadata.create_all(engine)
        _SEEDED[sync_url] = snapshot_marks(sync_url, GI_MARK_TABLES)
    with engine.begin() as conn:
        _cleanup(conn)
        config = requester_config(invokers={"SendTicket": {"Type": "Ticket::Generic"}})
        insert_webservice(conn, ws_id=WS, name="GI51Crm", config=config)
        insert_webservice(conn, ws_id=WS + 1, name="GI51Old", config=config, valid=False)
        queue_id = conn.execute(text("SELECT MIN(id) FROM queue")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'gi51 invoker title', :q, 1, 1, 1, 1, 3, 1, 'gi51-cust',"
                " 'gi51.cust@example.org', 0, 0, 0, 0, 0, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"id": TICKET, "tn": f"gi51exec{TICKET}", "q": queue_id},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _SEEDED.items():
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


@pytest.fixture(autouse=True)
def _wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(process_engine._ACTION_HANDLERS, "ExecuteInvoker", execute_invoker_action)


def _jobs(sync_url: str) -> list[dict[str, Any]]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT kind, payload, created_by FROM tiqora_job"
                    " WHERE kind LIKE 'core.gi_invoke%' ORDER BY id"
                )
            ).all()
    finally:
        engine.dispose()
    return [{"kind": str(r[0]), "payload": json.loads(r[1]), "created_by": r[2]} for r in rows]


async def _run(sync_url: str, config: dict[str, Any], *, commit: bool = True) -> list[Any]:
    """Run the action in one session; returns ``[result, jobs visible in that session]``."""
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            skipped = await execute_transition_action(
                session,
                action=TransitionActionConfig.from_dict({"Module": MODULE, "Config": config}),
                ticket_id=TICKET,
                process_entity_id="P1",
                activity_entity_id="A1",
                transition_entity_id="T1",
                user_id=3,
                sysconfig=SysConfig(session),
            )
            visible = (
                await session.execute(
                    text("SELECT COUNT(*) FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'")
                )
            ).scalar_one()
            if commit:
                await session.commit()
            else:
                await session.rollback()
        return [skipped, int(visible)]
    finally:
        await engine.dispose()


async def test_execute_invoker_enqueues_in_caller_transaction(url_fixture: str) -> None:
    config = {
        "Webservice": "GI51Crm",
        "Invoker": "SendTicket",
        "Asynchronous": "0",
        "Note": "<OTRS_TICKET_Title>",
    }
    skipped, visible = await _run(url_fixture, config, commit=False)
    assert skipped is None
    assert visible == 1  # flushed in the caller's session ...
    assert _jobs(url_fixture) == []  # ... and gone after the rollback

    skipped, visible = await _run(url_fixture, config)
    assert skipped is None and visible == 1
    (job,) = _jobs(url_fixture)
    assert job["kind"] == "core.gi_invoke"  # Asynchronous "0" is ignored: always queued
    assert job["created_by"] == 3
    assert job["payload"]["webservice_id"] == WS
    assert job["payload"]["invoker"] == "SendTicket"
    assert job["payload"]["origin"] == "transition_action"
    assert job["payload"]["asynchronous"] is True
    assert job["payload"]["data"] == {
        "Origin": "TransitionAction",
        "TransitionActionConfig": {
            "Webservice": "GI51Crm",
            "Invoker": "SendTicket",
            "Asynchronous": "0",
            "Note": "gi51 invoker title",
        },
        "TicketID": TICKET,
    }


@pytest.mark.parametrize(
    "config",
    [
        {"Webservice": "GI51Nope", "Invoker": "SendTicket"},
        {"Webservice": "GI51Old", "Invoker": "SendTicket"},  # invalid webservice
        {"Webservice": "GI51Crm"},
        {"Webservice": "GI51Crm", "Invoker": ""},
        {"Invoker": "SendTicket"},
    ],
)
async def test_unknown_webservice_or_missing_invoker_is_noop(
    url_fixture: str, config: dict[str, Any]
) -> None:
    skipped, visible = await _run(url_fixture, config)
    assert skipped is None  # handled, not "unsupported"
    assert visible == 0
    assert _jobs(url_fixture) == []


async def test_user_id_override_and_dynamic_field_user_id(url_fixture: str) -> None:
    await _run(url_fixture, {"Webservice": "GI51Crm", "Invoker": "SendTicket", "UserID": "9"})
    # DynamicField_UserID moves to UserID after the override (Znuny order): data only.
    await _run(
        url_fixture,
        {"Webservice": "GI51Crm", "Invoker": "SendTicket", "DynamicField_UserID": "12"},
    )
    first, second = _jobs(url_fixture)
    assert first["created_by"] == 9
    assert "UserID" not in first["payload"]["data"]["TransitionActionConfig"]
    assert second["created_by"] == 3
    cfg = second["payload"]["data"]["TransitionActionConfig"]
    assert cfg["UserID"] == "12" and "DynamicField_UserID" not in cfg


async def test_runs_without_takeover_flag(url_fixture: str) -> None:
    """The follower's switch gates only event-driven calls, never a process action."""
    engine = create_engine(url_fixture)
    with Session(engine) as session:
        session.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key == KEY_GI_REQUESTER_ENABLED)
        )
        session.add(TiqoraSettings(key=KEY_GI_REQUESTER_ENABLED, value="0"))
        session.commit()
    try:
        await _run(url_fixture, {"Webservice": "GI51Crm", "Invoker": "SendTicket"})
        assert len(_jobs(url_fixture)) == 1
    finally:
        with Session(engine) as session:
            session.execute(
                delete(TiqoraSettings).where(TiqoraSettings.key == KEY_GI_REQUESTER_ENABLED)
            )
            session.commit()
        engine.dispose()
