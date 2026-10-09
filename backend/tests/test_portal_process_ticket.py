"""Customer portal: new process tickets and enrolling through the start dialog (plan Task 14).

Seed ids live in the 888600-888999 band (processes 8887xx, services 88866x, SLAs 88867x).
Znuny references: ``CustomerTicketProcess.pm`` (:3660-3800 create, :4548-4690 ``_GetSLAs`` /
``_GetServices`` / ``_GetQueues``), ``Ticket.pm`` ``TicketServiceList`` (:2503-2560),
``CustomerGroup.pm`` ``GroupMemberList`` (:266-281), ``Service.pm`` ``ServiceList`` (:188-205).
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
from tiqora.api.deps import get_db
from tiqora.api.portal import process as portal_process
from tiqora.api.portal.deps import get_current_customer, require_portal_enabled
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.deployed import clear_revision_cache
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID
from tiqora.process.dialog_form import customer_service_map
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

T0 = datetime(2026, 3, 4, 5, 6, 7)

OTHER_AGENT = 888610
GROUP_OK, GROUP_NO = 888620, 888621
QUEUE_OK, QUEUE_B, QUEUE_NO = 888630, 888631, 888632
Q_OK, Q_B, Q_NO = "Queue-OK-888", "Queue-B-888", "Queue-NO-888"
CUSTOMER_ROW, CUSTOMER_LOGIN, CUSTOMER_COMPANY = 888601, "cu-888601", "cust-888"
OTHER_ROW, OTHER_LOGIN, OTHER_COMPANY = 888602, "cu-888602", "cust-888b"
TICKET_OWN, TICKET_FOREIGN = 888640, 888641
SVC_MINE, SVC_OTHER, SVC_DEFAULT = 888660, 888661, 888662
SVC_PARENT_INVALID, SVC_KID = 888663, 888664
SLA_MINE, SLA_OTHER = 888670, 888671
SYSCONFIG_BASE = 888900

_SEEDED_URLS: list[str] = []
_SNAPSHOT: dict[str, int] = {}
_MY_QUEUES = f"{QUEUE_OK}, {QUEUE_B}, {QUEUE_NO}"
_MY_TICKETS = f"SELECT id FROM ticket WHERE queue_id IN ({_MY_QUEUES})"

PORTAL = "/api/portal/process"
BOTH = ["AgentInterface", "CustomerInterface"]
CUSTOMER_ONLY = ["CustomerInterface"]
AGENT_ONLY = ["AgentInterface"]


def process_eid(n: int) -> str:
    return f"Process-{888700 + n}"


def dialog_eid(n: int) -> str:
    return f"ActivityDialog-{888720 + n}"


def activity_eid(n: int, suffix: str) -> str:
    return f"Activity-{888740 + 2 * n + (1 if suffix == 'B' else 0)}"


def _cleanup(conn: Any) -> None:
    for stmt in (
        f"DELETE FROM ticket_history WHERE ticket_id IN ({_MY_TICKETS})",
        f"DELETE FROM article_data_mime WHERE article_id IN (SELECT id FROM article "
        f"WHERE ticket_id IN ({_MY_TICKETS}))",
        f"DELETE FROM article WHERE ticket_id IN ({_MY_TICKETS})",
        f"DELETE FROM dynamic_field_value WHERE object_id IN ({_MY_TICKETS})",
        f"DELETE FROM ticket WHERE queue_id IN ({_MY_QUEUES})",
        "DELETE FROM acl WHERE id BETWEEN 888950 AND 888959",
        "DELETE FROM acl_ticket_attribute_relations WHERE filename LIKE 'rel-888%'",
        "DELETE FROM sysconfig_modified WHERE id BETWEEN 888900 AND 888919",
        "DELETE FROM sysconfig_default WHERE id BETWEEN 888900 AND 888919",
        "DELETE FROM dynamic_field WHERE id BETWEEN 888650 AND 888659",
        "DELETE FROM service_sla WHERE service_id BETWEEN 888660 AND 888669",
        "DELETE FROM service_customer_user WHERE service_id BETWEEN 888660 AND 888669",
        "DELETE FROM service WHERE id BETWEEN 888660 AND 888669",
        "DELETE FROM sla WHERE id BETWEEN 888670 AND 888679",
        "DELETE FROM queue WHERE id BETWEEN 888630 AND 888639",
        "DELETE FROM group_customer_user WHERE group_id BETWEEN 888620 AND 888629",
        "DELETE FROM permission_groups WHERE id BETWEEN 888620 AND 888629",
        "DELETE FROM customer_user WHERE id BETWEEN 888601 AND 888609",
        "DELETE FROM users WHERE id BETWEEN 888610 AND 888619",
        "DELETE FROM pm_process WHERE id BETWEEN 888700 AND 888719",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 888720 AND 888739",
        "DELETE FROM pm_activity WHERE id BETWEEN 888740 AND 888769",
        "DELETE FROM pm_transition WHERE id BETWEEN 888770 AND 888779",
        "DELETE FROM pm_transition_action WHERE id BETWEEN 888780 AND 888789",
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
        conn.execute(text(f"DELETE FROM {table} WHERE ticket_id BETWEEN 888640 AND 888649"))


_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)
_NON_EMPTY_TITLE = (
    "Condition:\n  '1':\n    Fields:\n      Title:\n        Match: .\n        Type: Regexp\n"
    "    Type: and\n  Type: and\nConditionLinking: and\n"
)
ARTICLE = "{Display: '2', Config: {CommunicationChannel: Internal}}"


def _add_process(
    conn: Any,
    n: int,
    fields: dict[str, str],
    *,
    interfaces: list[str] | None = None,
    state: str = "S1",
) -> str:
    """Process ``n``: start dialog *fields*, one transition A -> B that sets the state ``open``."""
    order = "".join(f"- {name}\n" for name in fields)
    body = "".join(f"  {name}:\n    {yaml}\n" for name, yaml in fields.items())
    iface = "".join(f"- {i}\n" for i in (interfaces or CUSTOMER_ONLY))
    dialog_cfg = (
        f"DescriptionShort: Start {n}\nFieldOrder:\n{order}Fields:\n{body}Interface:\n{iface}"
        "Permission: ''\nSubmitButtonText: Go\n"
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {"id": 888720 + n, "eid": dialog_eid(n), "name": f"Start {n}", "cfg": dialog_cfg},
    )
    act_a, act_b = activity_eid(n, "A"), activity_eid(n, "B")
    trans, action = f"T-{888770 + n}", f"TA-{888780 + n}"
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": 888700 + n,
            "eid": process_eid(n),
            "name": f"Process 888-{n}",
            "st": state,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: {dialog_eid(n)}\n"
                f"Path:\n  {act_a}:\n    {trans}:\n      ActivityEntityID: {act_b}\n"
                f"      TransitionAction:\n      - {action}\n  {act_b}: {{}}\n"
            ),
        },
    )
    for eid, name, cfg, row in (
        (act_a, f"A{n}", f"ActivityDialog:\n  '1': {dialog_eid(n)}\n", 888740 + 2 * n),
        (act_b, f"B{n}", "ActivityDialog: {}\n", 888741 + 2 * n),
    ):
        conn.execute(
            text(_PM_INSERT.format(table="pm_activity")),
            {"id": row, "eid": eid, "name": name, "cfg": cfg},
        )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 888770 + n, "eid": trans, "name": f"T{n}", "cfg": _NON_EMPTY_TITLE},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition_action")),
        {
            "id": 888780 + n,
            "eid": action,
            "name": f"TA{n}",
            "cfg": "Config:\n  State: open\n"
            "Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet\n",
        },
    )
    return process_eid(n)


def _seed(conn: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:id, 'agent-888x', 'x', 'Ada', 'Agent888', 1, :t, 1, :t, 1)"
        ),
        {"id": OTHER_AGENT, "t": T0},
    )
    for gid, name in ((GROUP_OK, "cust-create-888"), (GROUP_NO, "cust-none-888")):
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, "t": T0},
        )
    for qid, name, gid in (
        (QUEUE_OK, Q_OK, GROUP_OK),
        (QUEUE_B, Q_B, GROUP_OK),
        (QUEUE_NO, Q_NO, GROUP_NO),
    ):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": name, "g": gid, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO group_customer_user (user_id, group_id, permission_key, permission_value,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'create', 1, :t, 1, :t, 1)"
        ),
        {"u": CUSTOMER_LOGIN, "g": GROUP_OK, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
            " field_type, object_type, config, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 0, 'Region888', 'Region', 1, 'Dropdown',"
            " 'Ticket', '---\nPossibleValues:\n  eu: Europe\n  us: America\n', 1, :t, 1, :t, 1)"
        ),
        {"id": 888650, "t": T0},
    )
    for row, login, company in (
        (CUSTOMER_ROW, CUSTOMER_LOGIN, CUSTOMER_COMPANY),
        (OTHER_ROW, OTHER_LOGIN, OTHER_COMPANY),
    ):
        conn.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by) VALUES (:id, :login,"
                " :email, :cust, 'Cora', 'Cust', 1, :t, 1, :t, 1)"
            ),
            {
                "id": row,
                "login": login,
                "email": f"{login}@example.org",
                "cust": company,
                "t": T0,
            },
        )
    # Services: the customer has SVC_MINE; SVC_DEFAULT is assigned to '<DEFAULT>' (Znuny's
    # fallback for customers without any assignment); the child of an invalid parent is hidden.
    for sid, name, valid in (
        (SVC_MINE, "Svc888-Mine", 1),
        (SVC_OTHER, "Svc888-Other", 1),
        (SVC_DEFAULT, "Svc888-Default", 1),
        (SVC_PARENT_INVALID, "Svc888-Par", 2),
        (SVC_KID, "Svc888-Par::Kid", 1),
    ):
        conn.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :n, :v, :t, 1, :t, 1)"
            ),
            {"id": sid, "n": name, "v": valid, "t": T0},
        )
    for sid, name in ((SLA_MINE, "Sla888-Mine"), (SLA_OTHER, "Sla888-Other")):
        conn.execute(
            text(
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 0, 0, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": sid, "n": name, "t": T0},
        )
    for service, sla in ((SVC_MINE, SLA_MINE), (SVC_OTHER, SLA_OTHER)):
        conn.execute(
            text("INSERT INTO service_sla (service_id, sla_id) VALUES (:s, :l)"),
            {"s": service, "l": sla},
        )
    for login, service in (
        (CUSTOMER_LOGIN, SVC_MINE),
        (CUSTOMER_LOGIN, SVC_KID),
        ("<DEFAULT>", SVC_DEFAULT),
    ):
        conn.execute(
            text(
                "INSERT INTO service_customer_user (customer_user_login, service_id, create_time,"
                " create_by) VALUES (:l, :s, :t, 1)"
            ),
            {"l": login, "s": service, "t": T0},
        )
    for tid, customer_user, company in (
        (TICKET_OWN, CUSTOMER_LOGIN, CUSTOMER_COMPANY),
        (TICKET_FOREIGN, OTHER_LOGIN, OTHER_COMPANY),
    ):
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, service_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by) VALUES (:id, :tn,"
                " 'T888', :q, 1, 1, 1, 1, 3, 4, :cust, :cu, :svc, 0, 0, 0, 0, 0, 0, 0, :t, 1,"
                " :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"2026030488{tid}",
                "q": QUEUE_OK,
                "cust": company,
                "cu": customer_user,
                "svc": SVC_MINE,
                "t": T0,
            },
        )


def _set_sysconfig(url: str, settings: dict[str, Any]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sysconfig_modified WHERE id BETWEEN 888900 AND 888919"))
        conn.execute(text("DELETE FROM sysconfig_default WHERE id BETWEEN 888900 AND 888919"))
        for i, (name, value) in enumerate(settings.items()):
            conn.execute(
                text(
                    "INSERT INTO sysconfig_default (id, name, description, navigation,"
                    " is_invisible, is_readonly, is_required, is_valid, has_configlevel,"
                    " user_modification_possible, user_modification_active, xml_content_raw,"
                    " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                    " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 'x', 'x', 0, 0, 1, 1, 0, 0, 0, 'x', 'x', 'Ticket.xml',"
                    " :e, 0, '0', current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": SYSCONFIG_BASE + i, "n": name, "e": yaml_encode_effective(value)},
            )
            conn.execute(
                text(
                    "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id,"
                    " is_valid, user_modification_active, effective_value, is_dirty,"
                    " reset_to_default, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :did, :n, NULL, 1, 0, :e, 0, 0, current_timestamp, 1,"
                    " current_timestamp, 1)"
                ),
                {
                    "id": SYSCONFIG_BASE + i,
                    "did": SYSCONFIG_BASE + i,
                    "n": name,
                    "e": yaml_encode_effective(value),
                },
            )
    engine.dispose()
    clear_revision_cache()


def _add_acl(url: str, n: int, name: str, change: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
                " config_match, config_change, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, NULL, '', 1, 0, '{}', :c, :t, 1, :t, 1)"
            ),
            {"id": 888950 + n, "name": name, "c": change, "t": T0},
        )
    engine.dispose()
    clear_revision_cache()


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
    _set_sysconfig(mariadb_znuny_url, {"Process::DefaultQueue": Q_OK})
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
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, login: str, company: str
) -> AsyncClient:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    # create_ticket numbers the ticket on its own connection (the router asks for the factory).
    monkeypatch.setattr(portal_process, "get_session_factory", lambda *a, **kw: factory)
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_portal_enabled] = lambda: None
    customer = AuthenticatedCustomer(
        id=CUSTOMER_ROW,
        login=login,
        email=f"{login}@example.org",
        customer_id=company,
        first_name="Cora",
        last_name="Cust",
    )
    app.dependency_overrides[get_current_customer] = lambda: customer
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def portal(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async with _make_client(engine, monkeypatch, CUSTOMER_LOGIN, CUSTOMER_COMPANY) as c:
        yield c


@pytest.fixture
async def portal_other(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async with _make_client(engine, monkeypatch, OTHER_LOGIN, OTHER_COMPANY) as c:
        yield c


def _ticket(url: str, ticket_id: int) -> Any:
    return _rows(
        url,
        "SELECT t.title, q.name, t.user_id, t.responsible_user_id, t.customer_id,"
        " t.customer_user_id, t.create_by FROM ticket t JOIN queue q ON q.id = t.queue_id"
        " WHERE t.id = :t",
        t=ticket_id,
    )[0]


def _ticket_count(url: str) -> int:
    return int(_rows(url, f"SELECT COUNT(*) FROM ticket WHERE queue_id IN ({_MY_QUEUES})")[0][0])


def _by_name(form: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in form["fields"]}


def _labels(field: dict[str, Any]) -> list[str]:
    return [o["label"] for o in field["options"]]


START = {"Title": "Display: '1'", "Article": ARTICLE, "Owner": "Display: '1'"}
VALUES = {"Title": "Printer broken", "Subject": "Printer", "Body": "It does not print"}


# --- POST /tickets -----------------------------------------------------------------------------


@pytest.mark.db
async def test_customer_creates_process_ticket_with_forced_identity(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, START | {"CustomerUserID": "Display: '2'"}))
    resp = await portal.post(
        f"{PORTAL}/tickets",
        json={
            "process_entity_id": process_eid(1),
            "field_values": VALUES
            | {
                "CustomerUserID": "somebody-else",
                "CustomerID": "other-co",
                "Owner": str(OTHER_AGENT),
            },
        },
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    title, queue, owner, responsible, customer_id, customer_user, create_by = _ticket(
        sync_url, out["ticket_id"]
    )
    assert (customer_user, customer_id) == (CUSTOMER_LOGIN, CUSTOMER_COMPANY)
    assert owner == 1 and owner == PORTAL_SYSTEM_USER_ID  # submitted Owner ignored
    assert create_by == 1
    assert (title, queue) == ("Printer broken", Q_OK)
    assert out["activity_changed"] is True
    assert out["state"]["activity_entity_id"] == activity_eid(1, "B")
    article = _rows(
        sync_url,
        "SELECT st.name, a.create_by, m.a_subject FROM article a"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
        " JOIN article_data_mime m ON m.article_id = a.id WHERE a.ticket_id = :t",
        t=out["ticket_id"],
    )
    assert [tuple(r) for r in article] == [("customer", 1, "Printer")]
    # The customer can read the ticket it created: the process state is part of the answer.
    assert out["state"]["process_entity_id"] == process_eid(1)


@pytest.mark.db
async def test_customer_cannot_use_agent_only_process(portal: AsyncClient, sync_url: str) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, START, interfaces=AGENT_ONLY))
    resp = await portal.post(
        f"{PORTAL}/tickets", json={"process_entity_id": process_eid(1), "field_values": VALUES}
    )
    assert resp.status_code == 409, resp.text
    assert _ticket_count(sync_url) == 2  # only the two seeded tickets


@pytest.mark.db
async def test_customer_acl_hides_process(portal: AsyncClient, sync_url: str) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, START))
    _add_acl(sync_url, 1, "hide-p1-888", f"PossibleNot:\n  Process: [{process_eid(1)}]\n")
    resp = await portal.post(
        f"{PORTAL}/tickets", json={"process_entity_id": process_eid(1), "field_values": VALUES}
    )
    assert resp.status_code == 409, resp.text
    assert _ticket_count(sync_url) == 2


@pytest.mark.db
async def test_customer_missing_required_field_is_400_and_creates_nothing(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, {"Title": "Display: '2'"}))
    resp = await portal.post(
        f"{PORTAL}/tickets", json={"process_entity_id": process_eid(1), "field_values": {}}
    )
    assert resp.status_code == 400, resp.text
    assert _ticket_count(sync_url) == 2


# --- queue list and the create-group rule ------------------------------------------------------


QUEUE_FIELDS = {"Title": "Display: '1'", "Queue": "Display: '2'", "Article": ARTICLE}


@pytest.mark.db
async def test_customer_queue_list_and_create_follow_the_customer_create_groups(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, QUEUE_FIELDS))
    # CustomerGroupSupport off (default): every valid queue is offered (GroupMemberList = all).
    form = (await portal.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    offered = set(_labels(_by_name(form)["Queue"]))
    assert {Q_OK, Q_B, Q_NO} <= offered

    # On: only the groups the customer holds "create" in (CustomerTicketProcess.pm:4626-4690).
    _set_sysconfig(
        sync_url,
        {
            "Process::DefaultQueue": Q_OK,
            "CustomerGroupSupport": "1",
            "CustomerGroupAlwaysGroups": [],
        },
    )
    form = (await portal.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    offered = set(_labels(_by_name(form)["Queue"]))
    assert {Q_OK, Q_B} <= offered
    assert Q_NO not in offered

    ok = await portal.post(
        f"{PORTAL}/tickets",
        json={
            "process_entity_id": process_eid(1),
            "field_values": VALUES | {"Queue": str(QUEUE_B)},
        },
    )
    assert ok.status_code == 201, ok.text
    assert _ticket(sync_url, ok.json()["ticket_id"])[1] == Q_B
    before = _ticket_count(sync_url)
    denied = await portal.post(
        f"{PORTAL}/tickets",
        json={
            "process_entity_id": process_eid(1),
            "field_values": VALUES | {"Queue": str(QUEUE_NO)},
        },
    )
    assert denied.status_code == 403, denied.text
    assert _ticket_count(sync_url) == before  # rolled back


@pytest.mark.db
async def test_customer_without_create_group_gets_no_queue_options(
    portal_other: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, QUEUE_FIELDS))
    _set_sysconfig(
        sync_url,
        {
            "Process::DefaultQueue": Q_OK,
            "CustomerGroupSupport": "1",
            "CustomerGroupAlwaysGroups": [],
        },
    )
    form = (await portal_other.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    assert _by_name(form)["Queue"]["options"] == []


# --- service / SLA narrowing -------------------------------------------------------------------


SERVICE_FIELDS = {"Title": "Display: '1'", "Service": "Display: '1'", "SLA": "Display: '1'"}


@pytest.mark.db
async def test_customer_service_and_sla_lists_are_the_customers_own(
    portal: AsyncClient, portal_other: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, SERVICE_FIELDS))
    form = (await portal.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    fields = _by_name(form)
    # Svc888-Par::Kid is assigned but its parent is invalid (KeepChildren off): hidden.
    assert _labels(fields["Service"]) == ["Svc888-Mine"]
    assert _labels(fields["SLA"]) == ["Sla888-Mine"]

    # No assignment at all: the '<DEFAULT>' services (Service.pm:1222-1235).
    other = (await portal_other.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    assert _labels(_by_name(other)["Service"]) == ["Svc888-Default"]
    assert _labels(_by_name(other)["SLA"]) == []


@pytest.mark.db
async def test_customer_service_map_keep_children(engine: AsyncEngine, sync_url: str) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        assert await customer_service_map(session, CUSTOMER_LOGIN, SysConfig(session)) == {
            SVC_MINE: "Svc888-Mine"
        }
    _set_sysconfig(sync_url, {"Process::DefaultQueue": Q_OK, "Ticket::Service::KeepChildren": "1"})
    async with factory() as session:
        assert await customer_service_map(session, CUSTOMER_LOGIN, SysConfig(session)) == {
            SVC_MINE: "Svc888-Mine",
            SVC_KID: "Svc888-Par::Kid",
        }


# --- Ticket Attribute Relations (customer parity with the agent source) ------------------------


@pytest.mark.db
async def test_customer_queue_options_follow_ticket_attribute_relations(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, QUEUE_FIELDS))
    before = (
        await portal.get(f"{PORTAL}/{process_eid(1)}/start-form?ticket_id={TICKET_OWN}")
    ).json()
    assert {Q_OK, Q_B} <= set(_labels(_by_name(before)["Queue"]))

    # The ticket's Service (Svc888-Mine) only allows Queue-B-888.
    _build(
        sync_url,
        lambda c: c.execute(
            text(
                "INSERT INTO acl_ticket_attribute_relations (filename, attribute_1, attribute_2,"
                " acl_data, priority, create_time, create_by, change_time, change_by)"
                " VALUES ('rel-888.csv', 'Service', 'Queue', :d, 1, :t, 1, :t, 1)"
            ),
            {"d": f"Service;Queue\nSvc888-Mine;{Q_B}\n", "t": T0},
        ),
    )
    after = (
        await portal.get(f"{PORTAL}/{process_eid(1)}/start-form?ticket_id={TICKET_OWN}")
    ).json()
    assert _labels(_by_name(after)["Queue"]) == [Q_B]


# --- POST /ticket/{id}/start -------------------------------------------------------------------


@pytest.mark.db
async def test_customer_enrolls_own_ticket_through_the_start_dialog(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, {"Title": "Display: '1'", "Article": ARTICLE}))
    resp = await portal.post(
        f"{PORTAL}/ticket/{TICKET_OWN}/start",
        json={"process_entity_id": process_eid(1), "field_values": {"Subject": "S", "Body": "B"}},
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    # Enrolling runs the transitions (Title 'T888' is non-empty): the ticket moves on to B.
    assert state["process_entity_id"] == process_eid(1)
    assert state["activity_entity_id"] == activity_eid(1, "B")
    assert (
        _rows(sync_url, "SELECT ticket_state_id FROM ticket WHERE id = :t", t=TICKET_OWN)[0][0]
        == _rows(sync_url, "SELECT id FROM ticket_state WHERE name = 'open'")[0][0]
    )
    senders = _rows(
        sync_url,
        "SELECT st.name FROM article a JOIN article_sender_type st"
        " ON st.id = a.article_sender_type_id WHERE a.ticket_id = :t",
        t=TICKET_OWN,
    )
    assert [r[0] for r in senders] == ["customer"]


@pytest.mark.db
async def test_customer_enroll_needs_ticket_access(portal: AsyncClient, sync_url: str) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, {"Title": "Display: '1'"}))
    body = {"process_entity_id": process_eid(1), "field_values": {}}
    foreign = await portal.post(f"{PORTAL}/ticket/{TICKET_FOREIGN}/start", json=body)
    assert foreign.status_code == 403, foreign.text
    missing = await portal.post(f"{PORTAL}/ticket/{TICKET_FOREIGN + 9000}/start", json=body)
    assert missing.status_code == 404, missing.text
    # Nothing started on the foreign ticket.
    assert (
        _rows(
            sync_url,
            "SELECT COUNT(*) FROM dynamic_field_value WHERE object_id = :t",
            t=TICKET_FOREIGN,
        )[0][0]
        == 0
    )


@pytest.mark.db
async def test_customer_enroll_missing_required_field_starts_nothing(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, {"Title": "Display: '2'"}))
    resp = await portal.post(
        f"{PORTAL}/ticket/{TICKET_OWN}/start",
        json={"process_entity_id": process_eid(1), "field_values": {}},
    )
    assert resp.status_code == 400, resp.text
    assert (
        _rows(
            sync_url, "SELECT COUNT(*) FROM dynamic_field_value WHERE object_id = :t", t=TICKET_OWN
        )[0][0]
        == 0
    )


# --- review fixes -----------------------------------------------------------------------------


def _support_on(url: str, **extra: Any) -> None:
    _set_sysconfig(
        url,
        {"Process::DefaultQueue": Q_OK, "CustomerGroupSupport": "1"} | extra,
    )


@pytest.mark.db
async def test_hidden_default_queue_outside_the_create_groups_is_not_checked(
    portal: AsyncClient, sync_url: str
) -> None:
    # The process designer routes to Queue-NO with a hidden Queue field (Display 0): Znuny only
    # looks the value up (CustomerTicketProcess.pm:4333-4344), so the customer still creates.
    fields = {"Title": "Display: '1'", "Queue": f"{{Display: '0', DefaultValue: {Q_NO}}}"}
    _build(sync_url, lambda c: _add_process(c, 1, fields))
    _support_on(sync_url, CustomerGroupAlwaysGroups=[])
    resp = await portal.post(
        f"{PORTAL}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": {"Title": "Routed"}},
    )
    assert resp.status_code == 201, resp.text
    assert _ticket(sync_url, resp.json()["ticket_id"])[1] == Q_NO


@pytest.mark.db
async def test_visible_queue_with_a_forged_value_is_still_checked(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, QUEUE_FIELDS))
    _support_on(sync_url, CustomerGroupAlwaysGroups=[])
    resp = await portal.post(
        f"{PORTAL}/tickets",
        json={"process_entity_id": process_eid(1), "field_values": VALUES | {"Queue": Q_NO}},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.db
async def test_always_groups_default_users_group_is_added(
    portal_other: AsyncClient, sync_url: str
) -> None:
    # Default CustomerGroupAlwaysGroups = ['users']: the stock "users" group counts for everyone.
    def setup(conn: Any) -> None:
        _add_process(conn, 1, QUEUE_FIELDS)
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (888633, 'Queue-Users-888',"
                " (SELECT id FROM permission_groups WHERE name = 'users'), 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {"t": T0},
        )

    _build(sync_url, setup)
    _set_sysconfig(sync_url, {"Process::DefaultQueue": Q_OK, "CustomerGroupSupport": "1"})
    form = (await portal_other.get(f"{PORTAL}/{process_eid(1)}/start-form")).json()
    offered = _labels(_by_name(form)["Queue"])
    assert "Queue-Users-888" in offered
    assert not {Q_OK, Q_B, Q_NO} & set(offered)  # the customer holds no create group


@pytest.mark.db
async def test_default_services_when_all_assigned_services_are_invalid(
    engine: AsyncEngine, sync_url: str
) -> None:
    _build(
        sync_url,
        lambda c: c.execute(
            text("UPDATE service SET valid_id = 2 WHERE id IN (:a, :b)"),
            {"a": SVC_MINE, "b": SVC_KID},
        ),
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Both assigned services are invalid: no valid assignment is left, so the
        # '<DEFAULT>' services apply (Service.pm:1198 filters valid_id before the fallback).
        assert await customer_service_map(session, CUSTOMER_LOGIN, SysConfig(session)) == {
            SVC_DEFAULT: "Svc888-Default"
        }


@pytest.mark.db
async def test_customer_enroll_with_group_support_needs_rw_in_the_queue_group(
    portal: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, lambda c: _add_process(c, 1, {"Title": "Display: '1'"}))
    _support_on(sync_url, CustomerGroupAlwaysGroups=[])
    body = {"process_entity_id": process_eid(1), "field_values": {}}
    # The customer only holds "create" in the ticket queue's group, the dialog needs "rw".
    denied = await portal.post(f"{PORTAL}/ticket/{TICKET_OWN}/start", json=body)
    assert denied.status_code in (403, 409), denied.text
    assert (
        _rows(
            sync_url, "SELECT COUNT(*) FROM dynamic_field_value WHERE object_id = :t", t=TICKET_OWN
        )[0][0]
        == 0
    )
    _build(
        sync_url,
        lambda c: c.execute(
            text(
                "UPDATE group_customer_user SET permission_key = 'rw' WHERE user_id = :u"
                " AND group_id = :g"
            ),
            {"u": CUSTOMER_LOGIN, "g": GROUP_OK},
        ),
    )
    allowed = await portal.post(f"{PORTAL}/ticket/{TICKET_OWN}/start", json=body)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["process_entity_id"] == process_eid(1)
