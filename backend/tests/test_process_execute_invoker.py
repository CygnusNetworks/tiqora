"""``ExecuteInvoker`` end to end through the process engine (process Task 31, ruling H1).

Nothing is patched: :func:`execute_transition_action` resolves the module from the real
``TRANSITION_ACTIONS`` registry, which runs the GenericInterface handler
(``tiqora.gi.process_action.execute_invoker_action``). The handler's own branches (user
override, missing parameters, takeover flag) are pinned in ``test_gi_execute_invoker.py``.

Seed band 897100-897109 (inside the process band): webservice 897100, ticket 897101. Jobs the
module creates are removed with ``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._gi_fixtures import (
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions import get_registry
from tiqora.gi.jobs import GI_INVOKE_KIND, GiInvokePayload
from tiqora.gi.process_action import execute_invoker_action
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.process.actions import resolve_action
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

WS = 897100
TICKET = 897101
MODULE = "Kernel::System::ProcessManagement::TransitionAction::ExecuteInvoker"
_SEEDED: dict[str, dict[str, int]] = {}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    cleanup_webservices(conn, [WS])
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
        insert_webservice(conn, ws_id=WS, name="P31Crm", config=config)
        queue_id = conn.execute(text("SELECT MIN(id) FROM queue")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'p31 invoker title', :q, 1, 1, 1, 1, 3, 1, 'p31-cust',"
                " 'p31.cust@example.org', 0, 0, 0, 0, 0, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"id": TICKET, "tn": f"p31exec{TICKET}", "q": queue_id},
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


def _jobs(sync_url: str) -> list[dict[str, Any]]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT kind, status, payload, created_by FROM tiqora_job"
                    " WHERE kind LIKE 'core.gi_invoke%' ORDER BY id"
                )
            ).all()
    finally:
        engine.dispose()
    return [
        {"kind": str(r[0]), "status": str(r[1]), "payload": json.loads(r[2]), "created_by": r[3]}
        for r in rows
    ]


async def _transition(sync_url: str, config: dict[str, Any], *, commit: bool = True) -> Any:
    """Run one transition action the way ``submit_activity_dialog`` does; returns the
    engine's "skipped" value (the short module name when the action is unsupported)."""
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            skipped = await execute_transition_action(
                session,
                action=TransitionActionConfig.from_dict({"Module": MODULE, "Config": config}),
                ticket_id=TICKET,
                process_entity_id="P31",
                activity_entity_id="A31",
                transition_entity_id="T31",
                user_id=3,
                sysconfig=SysConfig(session),
            )
            if commit:
                await session.commit()
            else:
                await session.rollback()
        return skipped
    finally:
        await engine.dispose()


def test_registry_runs_the_gi_handler() -> None:
    entry = resolve_action(MODULE)
    assert entry is not None and entry.run is execute_invoker_action
    assert [(p.name, p.kind, p.required) for p in entry.params] == [
        ("Webservice", "text", True),
        ("Invoker", "text", True),
        ("Asynchronous", "bool", False),
    ]
    # the queued job has a handler, so the worker runs what the dialog submit queued
    assert get_registry().get(JOB_HANDLERS, GI_INVOKE_KIND) is not None


@pytest.mark.parametrize("asynchronous", ["0", "1"])
async def test_transition_queues_the_invoker(url_fixture: str, asynchronous: str) -> None:
    skipped = await _transition(
        url_fixture,
        {
            "Webservice": "P31Crm",
            "Invoker": "SendTicket",
            "Asynchronous": asynchronous,
            "Note": "<OTRS_TICKET_Title>",
        },
    )
    assert skipped is None  # run, not reported as unsupported
    (job,) = _jobs(url_fixture)
    # always the background lane, whatever Asynchronous says
    assert job["kind"] == GI_INVOKE_KIND and job["status"] == "queued"
    assert job["created_by"] == 3
    payload = GiInvokePayload.model_validate(job["payload"])
    assert (payload.webservice_id, payload.invoker, payload.origin) == (
        WS,
        "SendTicket",
        "transition_action",
    )
    assert payload.asynchronous is True and payload.history_id is None
    assert payload.data["TicketID"] == TICKET
    # the engine replaced the smart tags before the handler saw the config
    assert payload.data["TransitionActionConfig"]["Note"] == "p31 invoker title"


async def test_job_rolls_back_with_the_dialog_submit(url_fixture: str) -> None:
    skipped = await _transition(
        url_fixture, {"Webservice": "P31Crm", "Invoker": "SendTicket"}, commit=False
    )
    assert skipped is None and _jobs(url_fixture) == []


async def test_unknown_webservice_is_handled_not_unsupported(url_fixture: str) -> None:
    skipped = await _transition(url_fixture, {"Webservice": "P31Nope", "Invoker": "SendTicket"})
    assert skipped is None  # Znuny logs and returns; the transition goes on
    assert _jobs(url_fixture) == []
