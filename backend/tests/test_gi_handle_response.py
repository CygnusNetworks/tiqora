"""``Ticket::Generic`` HandleResponse / HandleError against real Znuny tables (both dialects).

Seed ids live in the GI band 51160-51219 (users, queues, tickets, ...); rows the write services
create (history, articles, new tickets, outbox) are removed with ``snapshot_marks`` /
``delete_above_marks``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Coroutine, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import GI_MARK_TABLES
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.gi.config import DebuggerConfig
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.invoker_generic import handle_error, handle_response
from tiqora.storage.backend import _as_bytes
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T0 = datetime(2026, 1, 1, 10, 0, 0)
AGENT, RESP = 51160, 51161
QUEUE, QUEUE_HOME = 51160, 51161
SERVICE, SLA = 51160, 51160
DF = 51160
SYSCONFIG_BASE = 51160
LOGIN = "gi51agent"
TICKETS = tuple(range(51160, 51180))
_SEEDED: dict[str, dict[str, int]] = {}
_TYPE_IDS: dict[str, int] = {}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup_seed(conn: Any) -> None:
    for stmt in (
        "DELETE FROM sysconfig_modified WHERE id BETWEEN 51160 AND 51169",
        "DELETE FROM sysconfig_default WHERE id BETWEEN 51160 AND 51169",
        "DELETE FROM dynamic_field_value WHERE field_id = 51160",
        "DELETE FROM dynamic_field WHERE id = 51160",
        "DELETE FROM ticket WHERE id BETWEEN 51160 AND 51179",
        "DELETE FROM sla WHERE id = 51160",
        "DELETE FROM service WHERE id = 51160",
        "DELETE FROM ticket_type WHERE name = 'GI51Type'",
        "DELETE FROM queue_preferences WHERE queue_id IN (51160, 51161)",
        "DELETE FROM queue WHERE id IN (51160, 51161)",
        "DELETE FROM permission_groups WHERE id = 51160",
        "DELETE FROM user_preferences WHERE user_id IN (51160, 51161)",
        "DELETE FROM users WHERE id IN (51160, 51161)",
    ):
        conn.execute(text(stmt))


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    engine = create_engine(sync_url)
    # The write services touch ``tiqora_*`` tables (outbox, cache invalidation).
    TiqoraBase.metadata.create_all(engine)
    _SEEDED[sync_url] = snapshot_marks(sync_url, GI_MARK_TABLES)
    with engine.begin() as conn:
        _cleanup_seed(conn)
        for uid, login in ((AGENT, LOGIN), (RESP, "gi51resp")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (51160, 'gi51-group', 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        for qid, name in ((QUEUE, "GI51Queue"), (QUEUE_HOME, "GI51Home")):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, comments,"
                    " calendar_name, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 51160, 1, 1, 1, 1, 0, 1, NULL, NULL, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO ticket_type (name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES ('GI51Type', 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        # ``ticket_type.id`` is a SMALLINT: let the database pick the id.
        _TYPE_IDS[sync_url] = int(
            conn.execute(text("SELECT id FROM ticket_type WHERE name = 'GI51Type'")).scalar_one()
        )
        conn.execute(
            text(
                "INSERT INTO service (id, name, valid_id, comments, create_time, create_by,"
                " change_time, change_by) VALUES (51160, 'GI51Service', 1, NULL, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (51160, 'GI51SLA', 10, 20, 30, 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        cfg = "---\nDefaultValue: ''\n"
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (51160, 0, 'Gi51Tag', 'Gi51Tag', 51160, 'Text', 'Ticket', :cfg, 1,"
                " :t, 1, :t, 1)"
            ),
            {"cfg": cfg if conn.dialect.name == "postgresql" else cfg.encode(), "t": T0},
        )
        for tid in TICKETS:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " service_id, sla_id, user_id, responsible_user_id, ticket_priority_id,"
                    " ticket_state_id, customer_id, customer_user_id, timeout, until_time,"
                    " escalation_time, escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, :tn, 'GI51 Start', :q, 1, 1, NULL, NULL, 1, 1, 3, 1, '', '',"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"{tid}01", "q": QUEUE_HOME, "t": T0},
            )
        set_sysconfig_rows(conn, SYSCONFIG_BASE, {"Ticket::Type::Default": "GI51Type"})
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _SEEDED.items():
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup_seed(conn)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _Sink:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> None:
        self.lines.append(kwargs)

    def errors(self) -> list[str]:
        return [str(x["summary"]) for x in self.lines if x["debug_level"] == "error"]


class _Run:
    def __init__(self) -> None:
        self.sink = _Sink()
        self.enqueued: list[dict[str, Any]] = []
        self.fail_enqueue = False
        self.result: dict[str, Any] = {}

    async def enqueue(self, payload: dict[str, Any]) -> None:
        if self.fail_enqueue:
            raise RuntimeError("queue down")
        self.enqueued.append(payload)


async def _respond(
    sync_url: str,
    ticket_id: int | None,
    data: Any,
    *,
    success: bool = True,
    error_message: str | None = None,
    error_variant: bool = False,
    commit_after: bool = False,
) -> _Run:
    run = _Run()
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            debugger = GiDebugger(
                sink=run.sink,
                config=DebuggerConfig(threshold="debug", test_mode=False),
                webservice_id=51160,
                communication_type="Requester",
                name="SendTicket",
            )
            common: dict[str, Any] = {
                "ticket_id": ticket_id,
                "data": data,
                "debugger": debugger,
                "enqueue": run.enqueue,
                "session_factory": factory,
                "sysconfig": SysConfig(session),
            }
            if error_variant:
                run.result = await handle_error(session, **common)
            else:
                run.result = await handle_response(
                    session, success=success, error_message=error_message, **common
                )
            if commit_after:
                # A caller that commits after a failed response must not persist its writes.
                await session.commit()
    finally:
        await engine.dispose()
    return run


async def _with_session(
    sync_url: str, fn: Callable[[AsyncSession], Coroutine[Any, Any, Any]]
) -> Any:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await fn(session)
    finally:
        await engine.dispose()


async def _ticket(sync_url: str, tid: int) -> dict[str, Any]:
    async def read(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(
                text(
                    "SELECT title, queue_id, ticket_state_id, user_id, responsible_user_id,"
                    " type_id, service_id, sla_id, ticket_priority_id, customer_id,"
                    " customer_user_id, change_by FROM ticket WHERE id = :t"
                ),
                {"t": tid},
            )
        ).one()
        return dict(row._mapping)

    result: dict[str, Any] = await _with_session(sync_url, read)
    return result


async def _history(sync_url: str, tid: int) -> list[tuple[str, int, str]]:
    async def read(session: AsyncSession) -> list[tuple[str, int, str]]:
        rows = (
            await session.execute(
                text(
                    "SELECT t.name, h.create_by, h.name FROM ticket_history h"
                    " JOIN ticket_history_type t ON t.id = h.history_type_id"
                    " WHERE h.ticket_id = :t ORDER BY h.id"
                ),
                {"t": tid},
            )
        ).all()
        return [(str(r[0]), int(r[1]), str(r[2])) for r in rows]

    result: list[tuple[str, int, str]] = await _with_session(sync_url, read)
    return result


async def test_title_state_queue_owner_actions(url_fixture: str) -> None:
    run = await _respond(
        url_fixture,
        51160,
        {
            "OTRS_TicketTitleUpdate": "Neu",
            "OTRS_TicketStateSet": "open",
            "OTRS_TicketQueueSet": "GI51Queue",
            "OTRS_TicketOwnerSet": LOGIN,
            "OTRS_TicketResponsibleSet": "gi51resp",
            "OTRS_TicketPrioritySet": "4 high",
            "OTRS_TicketTypeSet": "GI51Type",
            "OTRS_TicketServiceSet": "GI51Service",
            "OTRS_TicketSLASet": "GI51SLA",
            "OTRS_TicketCustomerSet": "GI51CUST;gi51c@example.org",
            "Other": "ordinary response data",
        },
    )
    assert run.result["Success"] == 1 and run.result["Data"]["Other"] == "ordinary response data"
    t = await _ticket(url_fixture, 51160)
    assert t["title"] == "Neu" and t["queue_id"] == QUEUE and t["ticket_state_id"] == 4
    assert t["user_id"] == AGENT and t["responsible_user_id"] == RESP
    assert (
        t["type_id"] == _TYPE_IDS[url_fixture] and t["service_id"] == SERVICE and t["sla_id"] == SLA
    )
    assert t["ticket_priority_id"] == 4
    assert (t["customer_id"], t["customer_user_id"]) == ("GI51CUST", "gi51c@example.org")
    assert t["change_by"] == 1
    history = await _history(url_fixture, 51160)
    types = {name for name, _by, _text in history}
    assert {
        "TitleUpdate",
        "StateUpdate",
        "Move",
        "OwnerUpdate",
        "ResponsibleUpdate",
        "PriorityUpdate",
        "TypeUpdate",
        "ServiceUpdate",
        "SLAUpdate",
        "CustomerUpdate",
    } <= types
    assert {by for _name, by, _text in history} == {1}


async def test_customer_set_only_given_parts(url_fixture: str) -> None:
    run = await _respond(url_fixture, 51161, {"OTRS_TicketCustomerSet": "OnlyCompany"})
    assert run.result["Success"] == 1
    t = await _ticket(url_fixture, 51161)
    assert (t["customer_id"], t["customer_user_id"]) == ("OnlyCompany", "")


async def test_dynamic_field_set_case_insensitive_and_unknown_skipped(url_fixture: str) -> None:
    run = await _respond(
        url_fixture,
        51162,
        {"otrs_ticketdynamicfieldset_Gi51Tag": "blue", "OTRS_TicketDynamicFieldSet_Gi51Nope": "x"},
    )
    assert run.result["Success"] == 1
    assert run.sink.errors() == []

    async def read(session: AsyncSession) -> list[str]:
        rows = (
            await session.execute(
                text(
                    "SELECT value_text FROM dynamic_field_value WHERE field_id = 51160"
                    " AND object_id = 51162"
                )
            )
        ).all()
        return [str(r[0]) for r in rows]

    assert await _with_session(url_fixture, read) == ["blue"]


async def test_article_add_with_base64_attachment(url_fixture: str) -> None:
    payload = b"\x00\x01binary\xffcontent"
    run = await _respond(
        url_fixture,
        51163,
        {
            "OTRS_TicketArticleAdd": {
                "Subject": "GI51 note",
                "Body": "Hello from the remote system",
                "Attachment": {
                    "Filename": "data.bin",
                    "ContentType": "application/octet-stream",
                    "Content": base64.b64encode(payload).decode(),
                },
                "UserID": 2,
                "NotAnArticleKey": "ignored",
            }
        },
    )
    assert run.result["Success"] == 1
    assert any("NotAnArticleKey" in str(x["summary"]) for x in run.sink.lines)

    async def read(session: AsyncSession) -> dict[str, Any]:
        row = (
            await session.execute(
                text(
                    "SELECT a.id, a.create_by, a.is_visible_for_customer, m.a_subject, m.a_body,"
                    " m.a_from, m.a_content_type, s.name FROM article a"
                    " JOIN article_data_mime m ON m.article_id = a.id"
                    " JOIN article_sender_type s ON s.id = a.article_sender_type_id"
                    " WHERE a.ticket_id = 51163"
                )
            )
        ).one()
        att = (
            await session.execute(
                text(
                    "SELECT filename, content_type, content FROM article_data_mime_attachment"
                    " WHERE article_id = :a"
                ),
                {"a": row[0]},
            )
        ).one()
        return {"article": tuple(row), "attachment": tuple(att)}

    got = await _with_session(url_fixture, read)
    _id, create_by, visible, subject, body, from_, ctype, sender = got["article"]
    assert (create_by, int(visible), subject, sender) == (1, 0, "GI51 note", "agent")
    assert body == "Hello from the remote system" and from_ == "Generic Interface"
    assert ctype.startswith("text/plain") and "utf-8" in ctype
    filename, att_type, content = got["attachment"]
    assert filename == "data.bin" and att_type == "application/octet-stream"
    assert _as_bytes(content) == payload
    assert "AddNote" in {name for name, _by, _t in await _history(url_fixture, 51163)}


async def test_article_add_requires_subject_and_body(url_fixture: str) -> None:
    run = await _respond(url_fixture, 51164, {"OTRS_TicketArticleAdd": {"Subject": "only"}})
    assert run.result == {
        "Success": 0,
        "ErrorMessage": "Missing parameter 'Body' on action 'OTRS_TicketArticleAdd'."
        " Failed to execute!",
    }


async def test_history_add_defaults(url_fixture: str) -> None:
    run = await _respond(
        url_fixture,
        51165,
        {"OTRS_TicketHistoryAdd": {"HistoryType": "", "HistoryComment": "comment text"}},
    )
    assert run.result["Success"] == 1
    assert await _history(url_fixture, 51165) == [("AddNote", 1, "comment text")]

    run = await _respond(
        url_fixture, 51165, {"OTRS_TicketHistoryAdd": {"HistoryType": "Misc", "Name": "named"}}
    )
    assert run.result["Success"] == 1
    assert (await _history(url_fixture, 51165))[-1] == ("Misc", 1, "named")

    run = await _respond(url_fixture, 51165, {"OTRS_TicketHistoryAdd": {"Name": "no type"}})
    assert run.result["Success"] == 0
    assert (
        "Missing parameter 'HistoryType' on action 'OTRS_TicketHistoryAdd'"
        in (run.result["ErrorMessage"])
    )


async def test_ticket_create_supported_keys_and_rejects_unknown(url_fixture: str) -> None:
    async def find(session: AsyncSession) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                text(
                    "SELECT queue_id, ticket_state_id, ticket_priority_id, user_id, type_id,"
                    " customer_id, customer_user_id, create_by FROM ticket"
                    " WHERE title LIKE 'GI51 created%'"
                )
            )
        ).all()
        return [dict(r._mapping) for r in rows]

    full = {
        "Title": "GI51 created",
        "Queue": "GI51Queue",
        "State": "new",
        "Priority": "3 normal",
        "Owner": LOGIN,
        "CustomerID": "GI51CUST",
        "CustomerUser": "gi51c@example.org",
    }
    run = await _respond(url_fixture, None, {"OTRS_TicketCreate": full})
    assert run.result["Success"] == 1
    created = await _with_session(url_fixture, find)
    assert len(created) == 1
    assert created[0]["queue_id"] == QUEUE and created[0]["user_id"] == AGENT
    assert created[0]["type_id"] == _TYPE_IDS[url_fixture]  # Ticket::Type::Default
    assert (created[0]["customer_id"], created[0]["customer_user_id"]) == (
        "GI51CUST",
        "gi51c@example.org",
    )
    assert created[0]["create_by"] == 1

    bad = {**full, "Title": "GI51 created bogus", "Bogus": "1"}
    run = await _respond(url_fixture, None, {"OTRS_TicketCreate": bad})
    assert run.result["Success"] == 0
    assert run.result["ErrorMessage"].startswith(
        "Error on response action 'OTRS_TicketCreate' with value 'HASH'"
    )
    assert any("Bogus" in str(x["content"]) for x in run.sink.lines)

    # Znuny's TicketCreate payload: UserID/TN ignored, CustomerNo wins, ArchiveFlag mapped.
    znuny = {
        **full,
        "Title": "GI51 created znuny",
        "UserID": 999,
        "TN": "2026101100001",
        "CustomerNo": "GI51NO",
        "ArchiveFlag": "y",
    }
    run = await _respond(url_fixture, None, {"OTRS_TicketCreate": znuny})
    assert run.result["Success"] == 1
    assert any("UserID" in str(x["summary"]) for x in run.sink.lines)

    async def find_znuny(session: AsyncSession) -> Any:
        return (
            await session.execute(
                text(
                    "SELECT customer_id, archive_flag, create_by, tn FROM ticket"
                    " WHERE title = 'GI51 created znuny'"
                )
            )
        ).one()

    row = await _with_session(url_fixture, find_znuny)
    assert (row[0], int(row[1]), int(row[2])) == ("GI51NO", 1, 1)
    assert row[3] != "2026101100001"
    await _with_session(url_fixture, _forget_znuny_ticket)

    missing_owner = {k: v for k, v in full.items() if k != "Owner"}
    missing_owner["Title"] = "GI51 created no owner"
    run = await _respond(url_fixture, None, {"OTRS_TicketCreate": missing_owner})
    assert run.result["Success"] == 0
    assert len(await _with_session(url_fixture, find)) == 1  # nothing else was created


async def _forget_znuny_ticket(session: AsyncSession) -> None:
    """Drop the extra ticket so the "nothing else was created" count stays exact."""
    await session.execute(
        text(
            "DELETE FROM ticket_history WHERE ticket_id IN"
            " (SELECT id FROM (SELECT id FROM ticket WHERE title = 'GI51 created znuny') x)"
        )
    )
    await session.execute(text("DELETE FROM ticket WHERE title = 'GI51 created znuny'"))
    await session.commit()


async def test_unchanged_values_write_nothing(url_fixture: str) -> None:
    values = {
        "OTRS_TicketQueueSet": "GI51Queue",
        "OTRS_TicketStateSet": "open",
        "OTRS_TicketPrioritySet": "4 high",
        "OTRS_TicketResponsibleSet": "gi51resp",
    }
    first = await _respond(url_fixture, 51163, values)
    assert first.result["Success"] == 1

    async def counts(session: AsyncSession) -> tuple[int, int, Any]:
        hist = (
            await session.execute(
                text("SELECT COUNT(*) FROM ticket_history WHERE ticket_id = 51163")
            )
        ).scalar_one()
        outbox = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_event_outbox WHERE ticket_id = 51163")
            )
        ).scalar_one()
        changed = (
            await session.execute(text("SELECT change_time FROM ticket WHERE id = 51163"))
        ).scalar_one()
        return int(hist), int(outbox), changed

    before = await _with_session(url_fixture, counts)
    assert before[0] > 0
    second = await _respond(url_fixture, 51163, values)
    assert second.result["Success"] == 1
    assert await _with_session(url_fixture, counts) == before


async def test_async_invoker_execution_enqueues(url_fixture: str) -> None:
    item = {"WebserviceID": 51160, "Invoker": "SendTicket", "Data": {"TicketID": 51166}}
    run = await _respond(url_fixture, 51166, {"OTRS_AsynchronousInvokerExecution": [item]})
    assert run.result["Success"] == 1
    assert run.enqueued == [
        {
            "webservice_id": 51160,
            "invoker": "SendTicket",
            "data": {"TicketID": 51166},
            "asynchronous": True,
            "origin": "response_action",
        }
    ]

    run = await _respond(url_fixture, 51166, {"OTRS_AsynchronousInvokerExecution": "SendTicket"})
    assert run.result["ErrorMessage"] == (
        "Parameter 'OTRS_AsynchronousInvokerExecution' needs to be an array with data."
    )
    run = await _respond(url_fixture, 51166, {"OTRS_AsynchronousInvokerExecution": ["x"]})
    assert run.result["ErrorMessage"] == (
        "Parameter 'OTRS_AsynchronousInvokerExecution' needs to be an array of hashes with data."
    )
    assert run.enqueued == []

    run = await _respond_failing_enqueue(url_fixture, item)
    assert run.result["ErrorMessage"] == (
        "Error executing asynchronous call of Kernel::GenericInterface::Requester::Run()."
    )


async def _respond_failing_enqueue(sync_url: str, item: dict[str, Any]) -> _Run:
    run = _Run()
    run.fail_enqueue = True
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            debugger = GiDebugger(
                sink=run.sink,
                config=DebuggerConfig(threshold="debug", test_mode=False),
                webservice_id=51160,
                communication_type="Requester",
            )
            run.result = await handle_response(
                session,
                ticket_id=51166,
                success=True,
                data={"OTRS_AsynchronousInvokerExecution": [item]},
                error_message=None,
                debugger=debugger,
                enqueue=run.enqueue,
                session_factory=factory,
                sysconfig=SysConfig(session),
            )
    finally:
        await engine.dispose()
    return run


async def test_article_create_event_unsupported_is_error(url_fixture: str) -> None:
    run = await _respond(url_fixture, 51167, {"OTRS_TicketArticleCreateEvent": "1,2"})
    assert run.result["Success"] == 0
    assert "nicht unterstützt" in run.result["ErrorMessage"]
    assert run.sink.errors() == [run.result["ErrorMessage"]]


async def test_failed_action_rolls_back_this_response(url_fixture: str) -> None:
    # Sorted order: Title (ok) < History (ok) < TypeSet (fails) - the first two must not survive.
    run = await _respond(
        url_fixture,
        51168,
        {
            "OTRS_TicketTitleUpdate": "must not stick",
            "OTRS_TicketHistoryAdd": {"HistoryType": "Misc", "Name": "must not stick"},
            "OTRS_TicketTypeSet": "NoSuchType",
        },
        commit_after=True,
    )
    assert run.result == {
        "Success": 0,
        "ErrorMessage": "Error on response action 'OTRS_TicketTypeSet' with value 'NoSuchType'."
        " Failed to execute!",
    }
    assert run.sink.errors() == [run.result["ErrorMessage"]]
    assert (await _ticket(url_fixture, 51168))["title"] == "GI51 Start"
    assert await _history(url_fixture, 51168) == []


async def test_response_error_without_message(url_fixture: str) -> None:
    run = await _respond(
        url_fixture, 51169, {"OTRS_TicketTitleUpdate": "from error data"}, success=False
    )
    assert run.result == {
        "Success": 0,
        "ErrorMessage": "Got response error, but no response error message!",
    }
    # Znuny runs the actions even when the response is an error.
    assert (await _ticket(url_fixture, 51169))["title"] == "from error data"

    run = await _respond(
        url_fixture,
        51169,
        {"OTRS_TicketTitleUpdate": "again"},
        success=False,
        error_message="remote said no",
    )
    assert run.result == {"Success": 0, "ErrorMessage": "remote said no"}


async def test_handle_error_runs_actions_and_reports_failure(url_fixture: str) -> None:
    run = await _respond(
        url_fixture, 51170, {"OTRS_TicketTitleUpdate": "via HandleError"}, error_variant=True
    )
    assert run.result["Success"] == 0
    assert run.result["ErrorMessage"] == "Got response error, but no response error message!"
    assert (await _ticket(url_fixture, 51170))["title"] == "via HandleError"


async def test_no_ticket_for_ticket_action_is_error(url_fixture: str) -> None:
    run = await _respond(url_fixture, None, {"OTRS_TicketTitleUpdate": "x"})
    assert run.result["Success"] == 0
    assert run.result["ErrorMessage"].startswith(
        "Error on response action 'OTRS_TicketTitleUpdate'"
    )


async def test_non_dict_data_passes_through() -> None:
    for data in (None, {}, "text", ["a"], 5):
        run = _Run()
        debugger = GiDebugger(
            sink=run.sink,
            config=DebuggerConfig(threshold="debug", test_mode=False),
            webservice_id=1,
            communication_type="Requester",
        )
        result = await handle_response(
            None,  # type: ignore[arg-type]  # never touched for non-dict data
            ticket_id=1,
            success=False,
            data=data,
            error_message=None,
            debugger=debugger,
            enqueue=run.enqueue,
            session_factory=None,  # type: ignore[arg-type]
            sysconfig=None,  # type: ignore[arg-type]
        )
        assert result == {"Success": 1, "Data": data}
        assert run.sink.lines == []
