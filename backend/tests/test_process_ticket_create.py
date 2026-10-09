"""New process tickets and enrolling through the start dialog (plan D10, Task 13).

Seed ids live in the 888000-888499 band. Tickets created by the tests get auto-increment ids; they
all land in this module's queues (``Process::DefaultQueue`` points at one of them) and are removed
by queue. Znuny references: ``AgentTicketProcess.pm`` ``_StoreActivityDialog`` (:5104-5290 create,
:5300ff enroll, :5967 owner), ``CustomerTicketProcess.pm:3660-3800``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1 import process as process_api
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.deployed import clear_revision_cache
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID
from tiqora.process.actor import ProcessActor
from tiqora.process.process_ticket import create_process_ticket
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT1, AGENT2, AGENT_RO = 888010, 888011, 888012  # AGENT_RO only has "ro" on the queue group
GROUP = 888020
QUEUE_RAW, QUEUE_OTHER = 888030, 888031
Q_RAW, Q_OTHER = "Raw-888", "Other-888"
TICKET_1, TICKET_2 = 888040, 888041  # existing tickets (enroll)
DF_REGION = 888050
CUSTOMER_ROW, CUSTOMER_LOGIN, CUSTOMER_COMPANY = 888001, "cu-888001", "cust-888"
SYSCONFIG_DEFAULT_ID, SYSCONFIG_MODIFIED_ID = 888480, 888481

_SEEDED_URLS: list[str] = []
_SNAPSHOT: dict[str, int] = {}
_MY_QUEUES = f"{QUEUE_RAW}, {QUEUE_OTHER}"
_MY_TICKETS = f"SELECT id FROM ticket WHERE queue_id IN ({_MY_QUEUES})"


def process_eid(n: int) -> str:
    return f"Process-{888100 + n}"


def dialog_eid(n: int) -> str:
    return f"ActivityDialog-{888200 + n}"


def activity_eid(n: int, suffix: str) -> str:
    return f"Activity-{888300 + 2 * n + (1 if suffix == 'B' else 0)}"


def _cleanup(conn: Any) -> None:
    for stmt in (
        f"DELETE FROM ticket_history WHERE ticket_id IN ({_MY_TICKETS})",
        f"DELETE FROM article_data_mime WHERE article_id IN (SELECT id FROM article "
        f"WHERE ticket_id IN ({_MY_TICKETS}))",
        f"DELETE FROM article WHERE ticket_id IN ({_MY_TICKETS})",
        f"DELETE FROM dynamic_field_value WHERE object_id IN ({_MY_TICKETS})",
        f"DELETE FROM ticket WHERE queue_id IN ({_MY_QUEUES})",
        "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 888040 AND 888049",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 888040 AND 888049",
        "DELETE FROM ticket WHERE id BETWEEN 888040 AND 888049",
        f"DELETE FROM sysconfig_modified WHERE id = {SYSCONFIG_MODIFIED_ID}",
        f"DELETE FROM sysconfig_default WHERE id = {SYSCONFIG_DEFAULT_ID}",
        "DELETE FROM dynamic_field WHERE id BETWEEN 888050 AND 888059",
        "DELETE FROM queue WHERE id BETWEEN 888030 AND 888039",
        "DELETE FROM group_user WHERE group_id BETWEEN 888020 AND 888029",
        "DELETE FROM permission_groups WHERE id BETWEEN 888020 AND 888029",
        "DELETE FROM customer_user WHERE id BETWEEN 888001 AND 888009",
        "DELETE FROM users WHERE id BETWEEN 888010 AND 888019",
        "DELETE FROM pm_process WHERE id BETWEEN 888100 AND 888199",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 888200 AND 888299",
        "DELETE FROM pm_activity WHERE id BETWEEN 888300 AND 888399",
        "DELETE FROM pm_transition WHERE id BETWEEN 888400 AND 888449",
        "DELETE FROM pm_transition_action WHERE id BETWEEN 888450 AND 888479",
    ):
        conn.execute(text(stmt))


_TIQORA_DDL = (
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
)


def _clean_tiqora_rows(conn: Any) -> None:
    for table in ("tiqora_event_outbox", "tiqora_cache_invalidation"):
        conn.execute(text(f"DELETE FROM {table} WHERE ticket_id IN ({_MY_TICKETS})"))
        conn.execute(text(f"DELETE FROM {table} WHERE ticket_id BETWEEN 888040 AND 888049"))


_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)

BOTH = ["AgentInterface", "CustomerInterface"]

# Region (2), Article (2), Owner (1) -- the brief's start dialog.
START_FIELDS: dict[str, str] = {
    "DynamicField_Region888": "Display: '2'",
    "Article": "{Display: '2', Config: {CommunicationChannel: Internal}}",
    "Owner": "Display: '1'",
}
_NON_EMPTY_TITLE = (
    "Condition:\n  '1':\n    Fields:\n      Title:\n        Match: .\n        Type: Regexp\n"
    "    Type: and\n  Type: and\nConditionLinking: and\n"
)


def _add_process(
    conn: Any,
    n: int,
    fields: dict[str, str],
    *,
    interfaces: list[str] | None = None,
    state: str = "S1",
    action_state: str | None = "open",
) -> str:
    """Process ``n``: start dialog *fields*, one transition A -> B (fires for any titled ticket)
    with ``TicketStateSet`` *action_state* (no action when ``None``)."""
    order = "".join(f"- {name}\n" for name in fields)
    body = "".join(f"  {name}:\n    {yaml}\n" for name, yaml in fields.items())
    iface = "".join(f"- {i}\n" for i in (interfaces or ["AgentInterface"]))
    dialog_cfg = (
        f"DescriptionShort: Start {n}\nFieldOrder:\n{order}Fields:\n{body}Interface:\n{iface}"
        "Permission: ''\nSubmitButtonText: Go\n"
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {"id": 888200 + n, "eid": dialog_eid(n), "name": f"Start {n}", "cfg": dialog_cfg},
    )
    act_a, act_b, trans, action = (
        activity_eid(n, "A"),
        activity_eid(n, "B"),
        f"T-{888400 + n}",
        f"TA-{888450 + n}",
    )
    action_ref = f"      TransitionAction:\n      - {action}\n" if action_state else ""
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": 888100 + n,
            "eid": process_eid(n),
            "name": f"Process 888-{n}",
            "st": state,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: {dialog_eid(n)}\n"
                f"Path:\n  {act_a}:\n    {trans}:\n      ActivityEntityID: {act_b}\n{action_ref}"
                f"  {act_b}: {{}}\n"
            ),
        },
    )
    for eid, name, cfg, row in (
        (act_a, f"A{n}", f"ActivityDialog:\n  '1': {dialog_eid(n)}\n", 888300 + 2 * n),
        (act_b, f"B{n}", "ActivityDialog: {}\n", 888301 + 2 * n),
    ):
        conn.execute(
            text(_PM_INSERT.format(table="pm_activity")),
            {"id": row, "eid": eid, "name": name, "cfg": cfg},
        )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 888400 + n, "eid": trans, "name": f"T{n}", "cfg": _NON_EMPTY_TITLE},
    )
    if action_state:
        conn.execute(
            text(_PM_INSERT.format(table="pm_transition_action")),
            {
                "id": 888450 + n,
                "eid": action,
                "name": f"TA{n}",
                "cfg": "Config:\n  State: "
                + action_state
                + "\nModule: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet\n",
            },
        )
    return process_eid(n)


def _seed(conn: Any) -> None:
    for uid, login in ((AGENT1, "agent-888a"), (AGENT2, "agent-888b"), (AGENT_RO, "agent-888c")):
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Ada', 'Agent888', 1, :t, 1, :t, 1)"
            ),
            {"id": uid, "login": login, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'proc-ticket-888', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP, "t": T0},
    )
    for uid, keys in ((AGENT1, ("rw", "create")), (AGENT2, ("rw",)), (AGENT_RO, ("ro",))):
        for key in keys:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": GROUP, "k": key, "t": T0},
            )
    for qid, name in ((QUEUE_RAW, Q_RAW), (QUEUE_OTHER, Q_OTHER)):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": name, "g": GROUP, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
            " field_type, object_type, config, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 0, 'Region888', 'Region', 1, 'Dropdown',"
            " 'Ticket', '---\nPossibleValues:\n  eu: Europe\n  us: America\n', 1, :t, 1, :t, 1)"
        ),
        {"id": DF_REGION, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :login, 'cu888@example.org', :cust, 'Cora', 'Cust', 1, :t, 1, :t, 1)"
        ),
        {"id": CUSTOMER_ROW, "login": CUSTOMER_LOGIN, "cust": CUSTOMER_COMPANY, "t": T0},
    )
    for tid in (TICKET_1, TICKET_2):
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :tn, 'T888', :q, 1, 1, :u, :u, 3, 1, 0, 0, 0, 0, 0, 0,"
                " 0, :t, 1, :t, 1)"
            ),
            {"id": tid, "tn": f"2026030488{tid}", "q": QUEUE_RAW, "u": AGENT1, "t": T0},
        )


def _set_sysconfig(url: str, name: str, value: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM sysconfig_modified WHERE id = {SYSCONFIG_MODIFIED_ID}"))
        conn.execute(text(f"DELETE FROM sysconfig_default WHERE id = {SYSCONFIG_DEFAULT_ID}"))
        conn.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
                " is_readonly, is_required, is_valid, has_configlevel,"
                " user_modification_possible, user_modification_active, xml_content_raw,"
                " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 'x', 'x', 0, 0, 1, 1, 0, 0, 0, 'x', 'x', 'Ticket.xml', :e, 0,"
                " '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": SYSCONFIG_DEFAULT_ID, "n": name, "e": yaml_encode_effective(value)},
        )
        conn.execute(
            text(
                "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id,"
                " is_valid, user_modification_active, effective_value, is_dirty, reset_to_default,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :did, :n, NULL, 1, 0, :e, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {
                "id": SYSCONFIG_MODIFIED_ID,
                "did": SYSCONFIG_DEFAULT_ID,
                "n": name,
                "e": yaml_encode_effective(value),
            },
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in set(_SEEDED_URLS):
        engine = create_engine(url)
        with engine.begin() as conn:
            _clean_tiqora_rows(conn)
            _cleanup(conn)
        engine.dispose()
        delete_rows_above(url, _SNAPSHOT)  # the ticket number counter rows
    _SEEDED_URLS.clear()
    _SNAPSHOT.clear()
    clear_revision_cache()


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> str:
    _SEEDED_URLS.append(mariadb_znuny_url)
    if not _SNAPSHOT:
        _SNAPSHOT.update(snapshot_max_ids(mariadb_znuny_url))
    clear_revision_cache()
    engine = create_engine(mariadb_znuny_url)
    TiqoraBase.metadata.create_all(engine, tables=[TiqoraSettings.__table__])
    with engine.begin() as conn:
        for ddl in _TIQORA_DDL:
            conn.execute(text(ddl))
        _clean_tiqora_rows(conn)
        _cleanup(conn)
        _seed(conn)
    engine.dispose()
    _set_sysconfig(mariadb_znuny_url, "Process::DefaultQueue", Q_RAW)
    return mariadb_znuny_url


def _build(url: str, setup: Callable[[Any], None]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        setup(conn)
    engine.dispose()
    clear_revision_cache()


def _rows(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    engine.dispose()
    return rows


@pytest.fixture
async def engine(sync_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(sync_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    yield eng
    await eng.dispose()


def _make_client(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, user_id: int, login: str
) -> AsyncClient:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    # create_ticket numbers the ticket on its own connection (the router asks for the factory).
    monkeypatch.setattr(process_api, "get_session_factory", lambda *a, **kw: factory)
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    user = AuthenticatedUser(
        id=user_id, login=login, first_name="Ada", last_name="Agent888", auth_method="session"
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async with _make_client(engine, monkeypatch, AGENT1, "agent-888a") as c:
        yield c


@pytest.fixture
async def client_ro(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async with _make_client(engine, monkeypatch, AGENT_RO, "agent-888c") as c:
        yield c


PROCESSES = "/api/v1/process"


def _ticket_count(url: str) -> int:
    return int(_rows(url, f"SELECT COUNT(*) FROM ticket WHERE queue_id IN ({_MY_QUEUES})")[0][0])


def _ticket(url: str, ticket_id: int) -> Any:
    return _rows(
        url,
        "SELECT t.title, q.name, s.name, l.name, p.name, t.user_id, t.responsible_user_id,"
        " t.customer_id, t.customer_user_id FROM ticket t"
        " JOIN queue q ON q.id = t.queue_id JOIN ticket_state s ON s.id = t.ticket_state_id"
        " JOIN ticket_lock_type l ON l.id = t.ticket_lock_id"
        " JOIN ticket_priority p ON p.id = t.ticket_priority_id WHERE t.id = :t",
        t=ticket_id,
    )[0]


def _history(url: str, ticket_id: int) -> list[tuple[str, str]]:
    return [
        (str(r[0]), str(r[1]))
        for r in _rows(
            url,
            "SELECT ht.name, h.name FROM ticket_history h"
            " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
            " WHERE h.ticket_id = :t ORDER BY h.id",
            t=ticket_id,
        )
    ]


def _df(url: str, ticket_id: int, name: str) -> list[str]:
    return [
        str(r[0])
        for r in _rows(
            url,
            "SELECT v.value_text FROM dynamic_field_value v JOIN dynamic_field f"
            " ON f.id = v.field_id WHERE v.object_id = :t AND f.name = :n",
            t=ticket_id,
            n=name,
        )
    ]


def _basic(conn: Any) -> None:
    _add_process(conn, 1, START_FIELDS)


VALUES: dict[str, Any] = {
    "DynamicField_Region888": "eu",
    "Subject": "Laptop",
    "Body": "Bitte bestellen",
    "Owner": str(AGENT2),
}


# --- agent: POST /process/tickets ------------------------------------------------------------


@pytest.mark.db
async def test_create_process_ticket_uses_process_defaults_and_runs_transition(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": VALUES},
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    title, queue, state, lock, priority, owner, responsible, _cid, _cuid = _ticket(
        sync_url, out["ticket_id"]
    )
    # Process::DefaultQueue -> Raw-888, DefaultLock "unlock", DefaultPriority "3 normal"; the
    # transition's TicketStateSet moved new -> open; the chosen Owner got the ticket.
    assert (queue, state, lock, priority) == (Q_RAW, "open", "unlock", "3 normal")
    assert owner == AGENT2
    assert responsible == 1
    assert title == "Laptop"  # Subject fallback
    assert (
        out["ticket_number"]
        == _rows(sync_url, "SELECT tn FROM ticket WHERE id = :t", t=out["ticket_id"])[0][0]
    )
    assert out["state"]["activity_entity_id"] == activity_eid(1, "B")
    assert out["activity_changed"] is True
    assert out["unsupported_actions"] == []
    assert _df(sync_url, out["ticket_id"], "Region888") == ["eu"]
    assert _df(sync_url, out["ticket_id"], "ProcessManagementProcessID") == [process_eid(1)]
    assert _df(sync_url, out["ticket_id"], "ProcessManagementActivityID") == [activity_eid(1, "B")]

    # F12: the Znuny order. The transition stores the new activity BEFORE its actions run
    # (Process.pm:501, actions from :558; Task 9 ruling), so the activity update precedes
    # StateUpdate (F12 listed them the other way round).
    history = _history(sync_url, out["ticket_id"])
    assert [h[0] for h in history] == [
        "NewTicket",
        "TicketDynamicFieldUpdate",  # ProcessManagementProcessID
        "TicketDynamicFieldUpdate",  # ProcessManagementActivityID (start activity)
        "TicketDynamicFieldUpdate",  # Region888
        "AddNote",
        "Misc",  # "Reset of unlock time." -- written by the article (MIMEBase.pm:578-583)
        "OwnerUpdate",
        "TicketDynamicFieldUpdate",  # ProcessManagementActivityID (activity B)
        "StateUpdate",
        "SetPendingTime",  # the state change to a non-pending state resets the pending time
    ]
    assert "ProcessManagementProcessID" in history[1][1]
    assert activity_eid(1, "A") in history[2][1]
    assert "Region888" in history[3][1]
    assert activity_eid(1, "B") in history[7][1]
    # AgentTicketProcess.pm:5650-5654: an Internal dialog article is AddNote "%%Note", not the
    # usual "%% <subject>" (found by tests/golden/test_process_deploy.py on Znuny 6.5).
    assert history[4] == ("AddNote", "%%Note")
    # The article carries the submitted subject/body.
    article = _rows(
        sync_url,
        "SELECT m.a_subject, m.a_body FROM article_data_mime m JOIN article a"
        " ON a.id = m.article_id WHERE a.ticket_id = :t",
        t=out["ticket_id"],
    )
    assert [(r[0], r[1]) for r in article] == [("Laptop", "Bitte bestellen")]


@pytest.mark.db
async def test_default_title_is_process_name_and_timestamp(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 2, {"DynamicField_Region888": "Display: '2'"}))
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={
            "process_entity_id": process_eid(2),
            "field_values": {"DynamicField_Region888": "us"},
        },
    )
    assert resp.status_code == 201, resp.text
    title = _ticket(sync_url, resp.json()["ticket_id"])[0]
    # "<process name> - <YYYY-MM-DD HH:MM:SS> (<OTRSTimeZone>)"; the test DB uses UTC.
    import re

    assert re.fullmatch(r"Process 888-2 - \d{4}-\d\d-\d\d \d\d:\d\d:\d\d \(UTC\)", title), title


@pytest.mark.db
async def test_title_renders_tags_from_the_submitted_values(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 2, {"Title": "Display: '1'"}, action_state=None))
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={
            "process_entity_id": process_eid(2),
            "field_values": {"Title": "In <OTRS_TICKET_Queue> as <OTRS_TICKET_State>"},
        },
    )
    assert resp.status_code == 201, resp.text
    # F13: Znuny renders the title with the resolved Queue / State names (:5183-5195).
    assert _ticket(sync_url, resp.json()["ticket_id"])[0] == f"In {Q_RAW} as new"


@pytest.mark.db
async def test_explicit_queue_and_responsible_from_the_dialog(
    client: AsyncClient, sync_url: str
) -> None:
    _build(
        sync_url,
        lambda c: _add_process(
            c,
            2,
            {"Title": "Display: '1'", "QueueID": "Display: '2'", "Responsible": "Display: '1'"},
            action_state=None,
        ),
    )
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={
            "process_entity_id": process_eid(2),
            "field_values": {
                "Title": "Moved",
                "Queue": str(QUEUE_OTHER),
                "Responsible": "agent-888b",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    ticket_id = resp.json()["ticket_id"]
    _title, queue, _s, _l, _p, owner, responsible, _cid, _cuid = _ticket(sync_url, ticket_id)
    assert queue == Q_OTHER
    assert responsible == AGENT2
    assert owner == 1  # no Owner chosen: stays the Znuny root user
    # create_ticket consumed Queue/Responsible: no second Move / ResponsibleUpdate row.
    types = [h[0] for h in _history(sync_url, ticket_id)]
    assert "Move" not in types and "ResponsibleUpdate" not in types


@pytest.mark.db
async def test_missing_required_field_is_400_and_nothing_written(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    before = _ticket_count(sync_url)
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={
            "process_entity_id": process_eid(1),
            "field_values": {"Subject": "No region", "Body": "x"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "DynamicField_Region888" in resp.json()["detail"]
    assert _ticket_count(sync_url) == before


@pytest.mark.db
async def test_needs_create_permission_on_queue(client_ro: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _basic)
    before = _ticket_count(sync_url)
    resp = await client_ro.post(
        f"{PROCESSES}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": VALUES},
    )
    assert resp.status_code == 403, resp.text
    assert "create" in resp.json()["detail"]
    assert _ticket_count(sync_url) == before  # rolled back, no ticket row


@pytest.mark.db
async def test_fadeaway_process_cannot_create(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, START_FIELDS, state="S3"))
    before = _ticket_count(sync_url)
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": VALUES},
    )
    assert resp.status_code == 409, resp.text
    assert _ticket_count(sync_url) == before


@pytest.mark.db
async def test_invalid_default_queue_is_clear_error(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _basic)
    _set_sysconfig(sync_url, "Process::DefaultQueue", "Nope")
    before = _ticket_count(sync_url)
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": VALUES},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Process::DefaultQueue fehlt oder ist ungültig."
    assert _ticket_count(sync_url) == before


@pytest.mark.db
async def test_unknown_process_is_404(client: AsyncClient, sync_url: str) -> None:
    resp = await client.post(
        f"{PROCESSES}/tickets", json={"process_entity_id": "Process-nope", "field_values": {}}
    )
    assert resp.status_code == 404, resp.text


# --- agent: enroll an existing ticket through the start dialog ---------------------------------


@pytest.mark.db
async def test_enroll_applies_start_dialog_and_transitions(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    resp = await client.post(
        f"{PROCESSES}/ticket/{TICKET_1}/start",
        json={"process_entity_id": process_eid(1), "field_values": VALUES},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["activity_entity_id"] == activity_eid(1, "B")
    assert resp.json()["process_entity_id"] == process_eid(1)
    _t, _q, state, _l, _p, owner, _r, _cid, _cuid = _ticket(sync_url, TICKET_1)
    assert state == "open"  # the transition's TicketStateSet (the ticket was "new")
    assert owner == AGENT2  # Owner field of the start dialog
    assert _df(sync_url, TICKET_1, "Region888") == ["eu"]
    assert _df(sync_url, TICKET_1, "ProcessManagementActivityID") == [activity_eid(1, "B")]
    assert [h[0] for h in _history(sync_url, TICKET_1)] == [
        "TicketDynamicFieldUpdate",
        "TicketDynamicFieldUpdate",
        "TicketDynamicFieldUpdate",
        "AddNote",
        "Misc",
        "OwnerUpdate",
        "TicketDynamicFieldUpdate",
        "StateUpdate",  # new -> open: a real change
        "SetPendingTime",
    ]


@pytest.mark.db
async def test_enroll_requires_mandatory_start_fields(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _basic)
    resp = await client.post(
        f"{PROCESSES}/ticket/{TICKET_2}/start", json={"process_entity_id": process_eid(1)}
    )
    assert resp.status_code == 400, resp.text
    assert "DynamicField_Region888" in resp.json()["detail"]
    assert "Article" in resp.json()["detail"]
    # Nothing was started: no process dynamic fields, no history.
    assert _df(sync_url, TICKET_2, "ProcessManagementProcessID") == []
    assert _history(sync_url, TICKET_2) == []


@pytest.mark.db
async def test_enroll_a_ticket_that_runs_a_process_is_409(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    body = {"process_entity_id": process_eid(1), "field_values": VALUES}
    assert (await client.post(f"{PROCESSES}/ticket/{TICKET_1}/start", json=body)).status_code == 200
    again = await client.post(f"{PROCESSES}/ticket/{TICKET_1}/start", json=body)
    assert again.status_code == 409, again.text


# --- customer interface: the customer is the acting one -----------------------------------------


async def _create_as(
    engine: AsyncEngine, sync_url: str, actor: ProcessActor, values: dict[str, Any], n: int
) -> int:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session, session.begin():
        result = await create_process_ticket(
            session,
            factory,
            SysConfig(session),
            actor=actor,
            process_entity_id=process_eid(n),
            field_values=values,
        )
    return result.ticket_id


@pytest.mark.db
async def test_customer_submission_fills_customer_and_ignores_submitted_ones(
    engine: AsyncEngine, sync_url: str
) -> None:
    # The customer form drops CustomerUserID/CustomerID; the engine fills them from the actor
    # (CustomerTicketProcess.pm:3675-3676) and never trusts submitted values.
    _build(
        sync_url,
        lambda c: _add_process(
            c,
            2,
            {"Title": "Display: '1'", "CustomerUserID": "Display: '2'"},
            interfaces=["CustomerInterface"],
            action_state=None,
        ),
    )
    actor = ProcessActor.customer(login=CUSTOMER_LOGIN, customer_id=CUSTOMER_COMPANY)
    ticket_id = await _create_as(
        engine,
        sync_url,
        actor,
        {"Title": "From the portal <OTRS_TICKET_Queue>", "CustomerUserID": "somebody-else"},
        2,
    )
    title, queue, _s, _l, _p, owner, _r, customer_id, customer_user_id = _ticket(
        sync_url, ticket_id
    )
    assert (customer_id, customer_user_id) == (CUSTOMER_COMPANY, CUSTOMER_LOGIN)
    assert owner == PORTAL_SYSTEM_USER_ID  # CustomerPanelUserID
    # CustomerTicketProcess never renders the title: the tag stays literal.
    assert (title, queue) == ("From the portal <OTRS_TICKET_Queue>", Q_RAW)


@pytest.mark.db
async def test_customer_default_title_has_no_timezone(engine: AsyncEngine, sync_url: str) -> None:
    _build(
        sync_url,
        lambda c: _add_process(
            c, 2, {"DynamicField_Region888": "Display: '1'"}, interfaces=["CustomerInterface"]
        ),
    )
    actor = ProcessActor.customer(login=CUSTOMER_LOGIN, customer_id=CUSTOMER_COMPANY)
    ticket_id = await _create_as(engine, sync_url, actor, {}, 2)
    import re

    # CustomerTicketProcess.pm:3690 -- "$ProcessName - $CurrentTimeStamp", no zone suffix.
    assert re.fullmatch(
        r"Process 888-2 - \d{4}-\d\d-\d\d \d\d:\d\d:\d\d", _ticket(sync_url, ticket_id)[0]
    )


@pytest.mark.db
async def test_agent_customer_user_brings_its_customer_id(
    engine: AsyncEngine, sync_url: str
) -> None:
    _build(
        sync_url,
        lambda c: _add_process(
            c, 2, {"Title": "Display: '1'", "CustomerUserID": "Display: '2'"}, action_state=None
        ),
    )
    ticket_id = await _create_as(
        engine,
        sync_url,
        ProcessActor.agent(AGENT1),
        {"Title": "By phone", "CustomerUserID": CUSTOMER_LOGIN},
        2,
    )
    _t, _q, _s, _l, _p, _o, _r, customer_id, customer_user_id = _ticket(sync_url, ticket_id)
    # TicketCustomerSet: a customer user without CustomerID takes the user's own company.
    assert (customer_id, customer_user_id) == (CUSTOMER_COMPANY, CUSTOMER_LOGIN)


# --- review round: same-state guard, FieldOrder, rollback --------------------------------------


def _types(url: str, ticket_id: int) -> list[str]:
    return [h[0] for h in _history(url, ticket_id)]


@pytest.mark.db
async def test_transition_to_the_current_state_writes_no_state_history(
    client: AsyncClient, sync_url: str
) -> None:
    # TicketStateSet.pm:163 + Ticket.pm:4531-4539: same state -> no StateUpdate, no pending reset.
    _build(
        sync_url,
        lambda c: _add_process(c, 2, {"Title": "Display: '1'"}, action_state="new"),
    )
    resp = await client.post(
        f"{PROCESSES}/ticket/{TICKET_2}/start", json={"process_entity_id": process_eid(2)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["activity_entity_id"] == activity_eid(2, "B")
    assert _ticket(sync_url, TICKET_2)[2] == "new"
    assert _types(sync_url, TICKET_2) == ["TicketDynamicFieldUpdate"] * 3


@pytest.mark.db
async def test_enroll_sets_owner_at_its_field_order_position(
    client: AsyncClient, sync_url: str
) -> None:
    # Submit/enroll store Owner in the save loop (:5800-5860), here before the article.
    _build(
        sync_url,
        lambda c: _add_process(
            c,
            2,
            {
                "Owner": "Display: '1'",
                "Article": "{Display: '2', Config: {CommunicationChannel: Internal}}",
            },
            action_state=None,
        ),
    )
    resp = await client.post(
        f"{PROCESSES}/ticket/{TICKET_1}/start",
        json={
            "process_entity_id": process_eid(2),
            "field_values": {"Owner": str(AGENT2), "Subject": "S", "Body": "B"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert _types(sync_url, TICKET_1) == [
        "TicketDynamicFieldUpdate",
        "TicketDynamicFieldUpdate",
        "OwnerUpdate",
        "AddNote",
        "Misc",
        "TicketDynamicFieldUpdate",  # activity B
    ]
    assert _ticket(sync_url, TICKET_1)[5] == AGENT2


@pytest.mark.db
async def test_create_stores_dynamic_fields_before_the_article(
    client: AsyncClient, sync_url: str
) -> None:
    # AgentTicketProcess.pm:5262-5290: a new ticket gets every dynamic field first, whatever
    # the FieldOrder says (the article is listed before the Region here).
    _build(
        sync_url,
        lambda c: _add_process(
            c,
            2,
            {
                "Article": "{Display: '2', Config: {CommunicationChannel: Internal}}",
                "DynamicField_Region888": "Display: '2'",
            },
            action_state=None,
        ),
    )
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={
            "process_entity_id": process_eid(2),
            "field_values": {"DynamicField_Region888": "us", "Subject": "S", "Body": "B"},
        },
    )
    assert resp.status_code == 201, resp.text
    history = _history(sync_url, resp.json()["ticket_id"])
    assert [h[0] for h in history] == [
        "NewTicket",
        "TicketDynamicFieldUpdate",  # process
        "TicketDynamicFieldUpdate",  # start activity
        "TicketDynamicFieldUpdate",  # Region888, before the article
        "AddNote",
        "Misc",
        "TicketDynamicFieldUpdate",  # activity B
    ]
    assert "Region888" in history[3][1]


@pytest.mark.db
async def test_failure_after_create_ticket_rolls_the_ticket_back(
    client: AsyncClient, sync_url: str
) -> None:
    # The transition action names an unknown state: the error surfaces after create_ticket,
    # start_process and the dialog ran, and nothing of it may stay behind.
    _build(
        sync_url,
        lambda c: _add_process(c, 2, {"Title": "Display: '1'"}, action_state="No such state"),
    )
    before = _ticket_count(sync_url)
    resp = await client.post(
        f"{PROCESSES}/tickets",
        json={"process_entity_id": process_eid(2), "field_values": {"Title": "Doomed"}},
    )
    assert resp.status_code == 400, resp.text
    assert _ticket_count(sync_url) == before
    assert _rows(sync_url, "SELECT COUNT(*) FROM ticket WHERE title = 'Doomed'")[0][0] == 0
