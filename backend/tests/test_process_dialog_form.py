"""Server-built activity dialog forms (agent + portal): field kinds, order, ACL-filtered options.

Seed ids live in the 889000-889999 band. Znuny references: ``AgentTicketProcess.pm`` (every
option list is a ``TicketAcl`` call with ``Action => AgentTicketProcess``, ``_RenderLock``
:4013 has no ACL), ``CustomerTicketProcess.pm:75-82`` (skipped fields), ``ActivityDialog.pm``.
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

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.portal.deps import get_current_customer, require_portal_enabled
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.deployed import clear_revision_cache
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.customer_auth import AuthenticatedCustomer

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT_A, AGENT_B = 889010, 889011  # both rw on the queues' group
GROUP = 889020
QUEUE_SUPPORT, QUEUE_JUNK = 889030, 889031
Q_SUPPORT, Q_JUNK = "Support-889", "Junk-889"
TICKET_1, TICKET_2 = 889040, 889041
TICKET_FOREIGN_QUEUE, TICKET_FOREIGN_CUSTOMER = 889042, 889043
GROUP_FOREIGN, QUEUE_FOREIGN = 889021, 889032
DF_REGION, DF_REGISTRY, DF_NOTE, DF_MULTI, DF_FLAG, DF_DATE, DF_DATETIME = range(889050, 889057)
CUSTOMER_ID_ROW = 889001
CUSTOMER_LOGIN = "cu-889001"
CUSTOMER_COMPANY = "cust-889"
ACL_BASE = 889450

BOTH = ["AgentInterface", "CustomerInterface"]
AGENT_ONLY = ["AgentInterface"]

_SEEDED_URLS: list[str] = []

_DFS = (
    (
        DF_REGION,
        "Region889",
        "Region (889)",
        "Dropdown",
        "---\nPossibleValues:\n  eu: Europe\n  us: America\n  as: Asia\nDefaultValue: eu\n",
    ),
    (DF_REGISTRY, "Registry889", "Registry", "Database", "---\nDatabase: x\n"),
    (DF_NOTE, "Note889", "Note", "TextArea", "---\nRows: 4\n"),
    (DF_MULTI, "Tags889", "Tags", "Multiselect", "---\nPossibleValues:\n  a: Alpha\n  b: Beta\n"),
    (DF_FLAG, "Flag889", "Flag", "Checkbox", "---\nDefaultValue: 0\n"),
    (DF_DATE, "Due889", "Due", "Date", "---\nDefaultValue: 0\n"),
    (DF_DATETIME, "At889", "At", "DateTime", "---\nDefaultValue: 0\n"),
)


def process_eid(n: int) -> str:
    return f"Process-{889100 + n}"


def dialog_eid(n: int) -> str:
    return f"ActivityDialog-{889200 + n}"


def _cleanup(conn: Any) -> None:
    for stmt in (
        f"DELETE FROM acl WHERE id BETWEEN {ACL_BASE} AND {ACL_BASE + 49}",
        "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 889040 AND 889049",
        "DELETE FROM dynamic_field WHERE id BETWEEN 889050 AND 889069",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 889040 AND 889049",
        "DELETE FROM ticket WHERE id BETWEEN 889040 AND 889049",
        "DELETE FROM queue WHERE id BETWEEN 889030 AND 889039",
        "DELETE FROM group_user WHERE group_id BETWEEN 889020 AND 889029",
        "DELETE FROM permission_groups WHERE id BETWEEN 889020 AND 889029",
        "DELETE FROM customer_user WHERE id BETWEEN 889001 AND 889009",
        "DELETE FROM users WHERE id BETWEEN 889010 AND 889019",
        "DELETE FROM pm_process WHERE id BETWEEN 889100 AND 889199",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 889200 AND 889299",
        "DELETE FROM pm_activity WHERE id BETWEEN 889300 AND 889399",
        "DELETE FROM pm_transition WHERE id BETWEEN 889400 AND 889449",
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
        conn.execute(text(f"DELETE FROM {table} WHERE ticket_id BETWEEN 889040 AND 889049"))


_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)


def _add_dialog(
    conn: Any,
    n: int,
    fields: dict[str, str],
    interfaces: list[str] | None = None,
    *,
    permission: str = "",
) -> str:
    """Dialog ``n``; *fields* maps the field name to its YAML body (``Display: '2'`` ...) in
    the order of the dict (= FieldOrder)."""
    order = "".join(f"- {name}\n" for name in fields)
    body = "".join(f"  {name}:\n    {yaml}\n" for name, yaml in fields.items())
    iface = "".join(f"- {i}\n" for i in (interfaces or BOTH))
    cfg = (
        f"DescriptionShort: Dialog {n}\nDescriptionLong: Long {n}\nFieldOrder:\n{order}"
        f"Fields:\n{body}Interface:\n{iface}Permission: '{permission}'\n"
        "SubmitAdviceText: Advice\nSubmitButtonText: Go\n"
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {"id": 889200 + n, "eid": dialog_eid(n), "name": f"Dialog {n}", "cfg": cfg},
    )
    return dialog_eid(n)


def _add_process(
    conn: Any, n: int, *, start_dialog: int, dialogs: list[int], state: str = "S1"
) -> str:
    act_a, act_b, trans = (
        f"Activity-{889300 + 2 * n}",
        f"Activity-{889301 + 2 * n}",
        f"T-{889400 + n}",
    )
    dialog_cfg = "".join(f"  '{i + 1}': {dialog_eid(d)}\n" for i, d in enumerate(dialogs))
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": 889100 + n,
            "eid": process_eid(n),
            "name": f"Process {n}",
            "st": state,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: {dialog_eid(start_dialog)}\n"
                f"Path:\n  {act_a}:\n    {trans}:\n      ActivityEntityID: {act_b}\n"
                f"  {act_b}: {{}}\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {
            "id": 889300 + 2 * n,
            "eid": act_a,
            "name": f"A{n}",
            "cfg": f"ActivityDialog:\n{dialog_cfg}",
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {"id": 889301 + 2 * n, "eid": act_b, "name": f"B{n}", "cfg": "ActivityDialog: {}\n"},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 889400 + n, "eid": trans, "name": f"T{n}", "cfg": "ConditionLinking: and\n"},
    )
    return process_eid(n)


def _seed(conn: Any) -> None:
    for uid, login, first in ((AGENT_A, "agent-889a", "Ada"), (AGENT_B, "agent-889b", "Bo")):
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', :f, 'Agent889', 1, :t, 1, :t, 1)"
            ),
            {"id": uid, "login": login, "f": first, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'dialogform-889', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP, "t": T0},
    )
    for uid in (AGENT_A, AGENT_B):
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"u": uid, "g": GROUP, "t": T0},
        )
    for qid, name in ((QUEUE_SUPPORT, Q_SUPPORT), (QUEUE_JUNK, Q_JUNK)):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": name, "g": GROUP, "t": T0},
        )
    for dfid, name, label, ftype, cfg in _DFS:
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 0, :n, :l, 1, :ft, 'Ticket', :cfg, 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": dfid, "n": name, "l": label, "ft": ftype, "cfg": cfg, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'dialogform-889-foreign', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP_FOREIGN, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:id, 'Foreign-889', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
        ),
        {"id": QUEUE_FOREIGN, "g": GROUP_FOREIGN, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :login, 'cu889@example.org', :cust, 'Cora', 'Cust', 1, :t, 1, :t, 1)"
        ),
        {"id": CUSTOMER_ID_ROW, "login": CUSTOMER_LOGIN, "cust": CUSTOMER_COMPANY, "t": T0},
    )
    for tid in (TICKET_1, TICKET_2, TICKET_FOREIGN_QUEUE, TICKET_FOREIGN_CUSTOMER):
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'T889', :q, 1, 1, :u, :u, 3, 4, :cust, :cu, 0, 0, 0, 0, 0, 0,"
                " 0, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"2026030488{tid}",
                "q": QUEUE_FOREIGN if tid == TICKET_FOREIGN_QUEUE else QUEUE_SUPPORT,
                "u": AGENT_A,
                "cust": "other-co" if tid == TICKET_FOREIGN_CUSTOMER else CUSTOMER_COMPANY,
                "cu": "someone-else" if tid == TICKET_FOREIGN_CUSTOMER else CUSTOMER_LOGIN,
                "t": T0,
            },
        )


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in set(_SEEDED_URLS):
        engine = create_engine(url)
        with engine.begin() as conn:
            _cleanup(conn)
            _clean_tiqora_rows(conn)
        engine.dispose()
    _SEEDED_URLS.clear()
    clear_revision_cache()


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> str:
    _SEEDED_URLS.append(mariadb_znuny_url)
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
    return mariadb_znuny_url


def _build(url: str, setup: Callable[[Any], None]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        setup(conn)
    engine.dispose()


def _add_acl(
    url: str,
    n: int,
    name: str,
    change: str,
    action: str = "AgentTicketProcess",
    process_props: str = "",
) -> None:
    """*process_props*: ``Properties: Process`` lines, e.g. ``ProcessEntityID: [Process-1]``."""
    props = "".join(f"    {line}\n" for line in process_props.splitlines())
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
                " config_match, config_change, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, NULL, '', 1, 0, :m, :c, :t, 1, :t, 1)"
            ),
            {
                "id": ACL_BASE + n,
                "name": name,
                "m": f"Properties:\n  Frontend:\n    Action: [{action}]\n"
                + (f"  Process:\n{props}" if props else ""),
                "c": change,
                "t": T0,
            },
        )
    engine.dispose()
    clear_revision_cache()


@pytest.fixture
async def engine(sync_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(sync_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    yield eng
    await eng.dispose()


def _app(engine: AsyncEngine) -> Any:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_portal_enabled] = lambda: None
    return app


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = _app(engine)
    user = AuthenticatedUser(
        id=AGENT_A,
        login="agent-889a",
        first_name="Ada",
        last_name="Agent889",
        auth_method="session",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def portal_client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = _app(engine)
    customer = AuthenticatedCustomer(
        id=CUSTOMER_ID_ROW,
        login=CUSTOMER_LOGIN,
        email="cu889@example.org",
        customer_id=CUSTOMER_COMPANY,
        first_name="Cora",
        last_name="Cust",
    )
    app.dependency_overrides[get_current_customer] = lambda: customer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


AGENT = "/api/v1/process"
PORTAL = "/api/portal/process"

# FieldOrder [Title(1), QueueID(2), Region(2), Article(2), Lock(0)] (Lock hidden).
FIRST_DIALOG: dict[str, str] = {
    "Title": "Display: '1'",
    "QueueID": "Display: '2'",
    "DynamicField_Region889": "Display: '2'",
    "Article": "{Display: '2', Config: {CommunicationChannel: Internal}}",
    "Lock": "Display: '0'",
}


def _basic(conn: Any) -> None:
    _add_dialog(conn, 1, FIRST_DIALOG)
    _add_process(conn, 1, start_dialog=1, dialogs=[1])


async def _start_form(c: AsyncClient, prefix: str, n: int = 1, **params: Any) -> Any:
    return await c.get(f"{prefix}/{process_eid(n)}/start-form", params=params)


def _labels(field: dict[str, Any]) -> list[str]:
    return [o["label"] for o in field["options"]]


def _by_name(form: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in form["fields"]}


# --- fields, order and kinds ---------------------------------------------------------------------


@pytest.mark.db
async def test_form_lists_visible_fields_in_order_with_kinds(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    resp = await _start_form(client, AGENT)
    assert resp.status_code == 200, resp.text
    form = resp.json()
    assert [(f["name"], f["kind"], f["required"]) for f in form["fields"]] == [
        ("Title", "text", False),
        ("Queue", "select", True),
        ("DynamicField_Region889", "select", True),
        ("Article", "article", True),
    ]
    assert form["process_entity_id"] == process_eid(1)
    assert form["activity_dialog_entity_id"] == dialog_eid(1)
    assert form["name"] == "Dialog 1"
    assert form["description_short"] == "Dialog 1"
    assert form["description_long"] == "Long 1"
    assert form["submit_button_text"] == "Go"
    assert form["submit_advice_text"] == "Advice"
    fields = _by_name(form)
    assert fields["Title"]["label"] == "Title"
    assert fields["DynamicField_Region889"]["label"] == "Region (889)"
    assert fields["DynamicField_Region889"]["default"] == "eu"  # DF default, dialog has none
    # Queues: only the ones the agent may see (ro), by name; DF: Znuny label order.
    assert [(o["value"], o["label"]) for o in fields["Queue"]["options"]] == [
        (str(QUEUE_JUNK), Q_JUNK),
        (str(QUEUE_SUPPORT), Q_SUPPORT),
    ]
    assert [(o["value"], o["label"]) for o in fields["DynamicField_Region889"]["options"]] == [
        ("us", "America"),
        ("as", "Asia"),
        ("eu", "Europe"),
    ]
    assert fields["Title"]["options"] == []


@pytest.mark.db
async def test_field_kinds_lock_and_agent_pickers(client: AsyncClient, sync_url: str) -> None:
    def setup(conn: Any) -> None:
        _add_dialog(
            conn,
            1,
            {
                "Title": "{Display: '1', DefaultValue: Hello, DescriptionShort: Short t}",
                "State": "Display: '1'",
                "Priority": "Display: '1'",
                "Owner": "Display: '1'",
                "ResponsibleID": "Display: '1'",
                "Lock": "Display: '1'",
                "PendingTime": "Display: '1'",
                "CustomerUserID": "Display: '2'",
                "CustomerID": "Display: '1'",
                "DynamicField_Note889": "Display: '1'",
                "DynamicField_Tags889": "Display: '1'",
                "DynamicField_Flag889": "Display: '1'",
                "DynamicField_Due889": "Display: '1'",
                "DynamicField_At889": "Display: '1'",
            },
        )
        _add_process(conn, 1, start_dialog=1, dialogs=[1])

    _build(sync_url, setup)
    form = (await _start_form(client, AGENT)).json()
    assert [(f["name"], f["kind"]) for f in form["fields"]] == [
        ("Title", "text"),
        ("State", "select"),
        ("Priority", "select"),
        ("Owner", "select"),
        ("Responsible", "select"),
        ("Lock", "select"),
        ("PendingTime", "pending_time"),
        ("CustomerUserID", "customer"),  # CustomerID is carried by the same field
        ("DynamicField_Note889", "textarea"),
        ("DynamicField_Tags889", "multiselect"),
        ("DynamicField_Flag889", "boolean"),
        ("DynamicField_Due889", "date"),
        ("DynamicField_At889", "datetime"),
    ]
    fields = _by_name(form)
    assert fields["Title"]["default"] == "Hello"
    assert fields["Title"]["description_short"] == "Short t"
    assert fields["CustomerUserID"]["required"] is True  # Display 2 on one of the two
    assert [(o["value"], o["label"]) for o in fields["Lock"]["options"]] == [
        ("lock", "lock"),
        ("unlock", "unlock"),
    ]
    agents = [(o["value"], o["label"]) for o in fields["Owner"]["options"]]
    assert (str(AGENT_A), "Ada Agent889") in agents
    assert (str(AGENT_B), "Bo Agent889") in agents
    assert fields["Responsible"]["options"] == fields["Owner"]["options"]
    assert _labels(fields["DynamicField_Tags889"]) == ["Alpha", "Beta"]
    assert "open" in _labels(fields["State"])
    assert "3 normal" in _labels(fields["Priority"])
    assert fields["DynamicField_Note889"]["options"] == []


@pytest.mark.db
async def test_unsupported_df_type_is_marked(client: AsyncClient, sync_url: str) -> None:
    def setup(conn: Any) -> None:
        _add_dialog(
            conn,
            1,
            {
                "DynamicField_Registry889": "Display: '1'",  # a "Database" DF
                "DynamicField_NoSuchField": "Display: '1'",
                "Attachments": "Display: '1'",  # phase 2 (rulings F34)
                "Title": "Display: '1'",
            },
        )
        _add_process(conn, 1, start_dialog=1, dialogs=[1])

    _build(sync_url, setup)
    form = (await _start_form(client, AGENT)).json()
    assert [(f["name"], f["kind"], f["options"]) for f in form["fields"]] == [
        ("DynamicField_Registry889", "unsupported", []),
        ("DynamicField_NoSuchField", "unsupported", []),
        ("Attachments", "unsupported", []),
        ("Title", "text", []),
    ]
    assert _by_name(form)["DynamicField_Registry889"]["label"] == "Registry"


# --- ACL-filtered options ------------------------------------------------------------------------


@pytest.mark.db
async def test_agent_options_are_acl_filtered(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _basic)
    _add_acl(
        sync_url,
        1,
        "no-junk",
        f"PossibleNot:\n  Ticket:\n    Queue: [{Q_JUNK}]\n",
    )
    _add_acl(
        sync_url,
        2,
        "no-us",
        "PossibleNot:\n  Ticket:\n    DynamicField_Region889: [us]\n",
    )
    # An ACL for another frontend module does not touch this form.
    _add_acl(
        sync_url,
        3,
        "other-module",
        "PossibleNot:\n  Ticket:\n    DynamicField_Region889: [eu]\n",
        action="AgentTicketPhone",
    )
    fields = _by_name((await _start_form(client, AGENT)).json())
    assert [(o["value"], o["label"]) for o in fields["Queue"]["options"]] == [
        (str(QUEUE_SUPPORT), Q_SUPPORT)
    ]
    assert [(o["value"], o["label"]) for o in fields["DynamicField_Region889"]["options"]] == [
        ("as", "Asia"),
        ("eu", "Europe"),
    ]


@pytest.mark.db
async def test_customer_form_skips_owner_and_filters_as_customer(
    portal_client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _add_dialog(
            conn,
            1,
            {
                "Title": "Display: '1'",
                "Queue": "Display: '2'",
                "Owner": "Display: '1'",
                "Responsible": "Display: '1'",
                "Lock": "Display: '1'",
                "PendingTime": "Display: '1'",
                "CustomerID": "Display: '1'",
                "CustomerUserID": "Display: '1'",
                "DynamicField_Region889": "Display: '1'",
                "Article": "{Display: '2', Config: {CommunicationChannel: Internal}}",
            },
        )
        _add_process(conn, 1, start_dialog=1, dialogs=[1])

    _build(sync_url, setup)
    # Agent-only ACL: must NOT apply to the customer interface ...
    _add_acl(
        sync_url,
        1,
        "agent-hides-support",
        f"PossibleNot:\n  Ticket:\n    Queue: [{Q_SUPPORT}]\n",
        action="AgentTicketProcess",
    )
    # ... the customer one does, with the customer as principal.
    _add_acl(
        sync_url,
        2,
        "customer-hides-junk889-and-us",
        f"PossibleNot:\n  Ticket:\n    Queue: [{Q_JUNK}]\n    DynamicField_Region889: [us]\n",
        action="CustomerTicketProcess",
    )
    resp = await _start_form(portal_client, PORTAL)
    assert resp.status_code == 200, resp.text
    form = resp.json()
    assert [(f["name"], f["kind"]) for f in form["fields"]] == [
        ("Title", "text"),
        ("Queue", "select"),
        ("DynamicField_Region889", "select"),
        ("Article", "article"),
    ]
    fields = _by_name(form)
    queue_labels = _labels(fields["Queue"])
    assert [q for q in queue_labels if q in (Q_JUNK, Q_SUPPORT)] == [Q_SUPPORT]
    # Znuny's system queues are not offered to customers.
    assert not {"Postmaster", "Raw", "Junk"} & set(queue_labels)
    assert [(o["value"], o["label"]) for o in fields["DynamicField_Region889"]["options"]] == [
        ("as", "Asia"),
        ("eu", "Europe"),
    ]


# --- ticket-bound dialogs ---


def _two_dialogs(conn: Any) -> None:
    _add_dialog(conn, 1, {"Title": "Display: '1'"})
    _add_dialog(
        conn,
        2,
        {
            "Title": "Display: '1'",
            "Queue": "Display: '1'",
            "Service": "Display: '1'",
            "SLA": "Display: '1'",
            "State": "Display: '1'",
            "Owner": "Display: '1'",
        },
    )
    _add_dialog(conn, 3, {"Title": "Display: '1'"}, AGENT_ONLY)
    _add_process(conn, 1, start_dialog=1, dialogs=[1, 2, 3])


async def _enroll(c: AsyncClient, prefix: str, ticket_id: int) -> None:
    resp = await c.post(
        f"{prefix}/ticket/{ticket_id}/start", json={"process_entity_id": process_eid(1)}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.db
async def test_ticket_dialog_form_agent_and_customer_skips(
    client: AsyncClient, portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    await _enroll(client, AGENT, TICKET_1)
    url = f"/ticket/{TICKET_1}/dialog/{dialog_eid(2)}/form"
    agent = (await client.get(AGENT + url)).json()
    assert [(f["name"], f["kind"]) for f in agent["fields"]] == [
        ("Title", "text"),
        ("Queue", "select"),
        ("Service", "select"),
        ("SLA", "select"),
        ("State", "select"),
        ("Owner", "select"),
    ]
    assert agent["process_entity_id"] == process_eid(1)
    assert agent["activity_dialog_entity_id"] == dialog_eid(2)
    # CustomerTicketProcess.pm:464/950/1506/3448: Owner is skipped, Queue/Service/SLA are not
    # (the Run-scope $SkipFields with them is dead code), also on an existing ticket.
    customer = (await portal_client.get(PORTAL + url)).json()
    assert [(f["name"], f["kind"]) for f in customer["fields"]] == [
        ("Title", "text"),
        ("Queue", "select"),
        ("Service", "select"),
        ("SLA", "select"),
        ("State", "select"),
    ]
    assert "open" in _labels(_by_name(customer)["State"])


@pytest.mark.db
async def test_dialog_form_for_hidden_dialog_is_403(
    client: AsyncClient, portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    await _enroll(client, AGENT, TICKET_1)
    _add_acl(
        sync_url,
        1,
        "hide-d2",
        f"PossibleNot:\n  ActivityDialog: [{dialog_eid(2)}]\n",
    )
    _add_acl(
        sync_url,
        2,
        "hide-d1-customer",
        f"PossibleNot:\n  ActivityDialog: [{dialog_eid(1)}]\n",
        action="CustomerTicketProcess",
    )
    assert (
        await client.get(f"{AGENT}/ticket/{TICKET_1}/dialog/{dialog_eid(2)}/form")
    ).status_code == 403
    # Agent-only dialog on the customer interface; ACL-hidden dialog for the customer.
    assert (
        await portal_client.get(f"{PORTAL}/ticket/{TICKET_1}/dialog/{dialog_eid(3)}/form")
    ).status_code == 403
    assert (
        await portal_client.get(f"{PORTAL}/ticket/{TICKET_1}/dialog/{dialog_eid(1)}/form")
    ).status_code == 403
    # Visible ones still render, a dialog that is not on the activity / ticket outside a process
    # are conflicts, an unknown ticket is 404.
    assert (
        await client.get(f"{AGENT}/ticket/{TICKET_1}/dialog/{dialog_eid(1)}/form")
    ).status_code == 200
    assert (
        await client.get(f"{AGENT}/ticket/{TICKET_1}/dialog/ActivityDialog-none/form")
    ).status_code == 409
    assert (
        await client.get(f"{AGENT}/ticket/{TICKET_2}/dialog/{dialog_eid(1)}/form")
    ).status_code == 409
    assert (
        await client.get(f"{AGENT}/ticket/889999/dialog/{dialog_eid(1)}/form")
    ).status_code == 404


# --- start-form gates (shared with the start itself) ---------------------------------------------


@pytest.mark.db
async def test_start_form_uses_the_start_gates(
    client: AsyncClient, portal_client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _add_dialog(conn, 1, {"Title": "Display: '1'"}, AGENT_ONLY)
        _add_process(conn, 1, start_dialog=1, dialogs=[1])
        _add_dialog(conn, 2, {"Title": "Display: '1'"})
        _add_process(conn, 2, start_dialog=2, dialogs=[2], state="S2")
        _add_dialog(conn, 3, {"Title": "Display: '1'"})
        _add_process(conn, 3, start_dialog=3, dialogs=[3])

    _build(sync_url, setup)
    assert (await _start_form(client, AGENT, 1)).status_code == 200
    assert (await _start_form(portal_client, PORTAL, 1)).status_code == 409  # no customer dialog
    assert (await _start_form(client, AGENT, 2)).status_code == 409  # Inactive
    assert (await client.get(f"{AGENT}/Process-none/start-form")).status_code == 404
    # The Action ACL is evaluated only in a ticket context (rulings F36), like the start.
    _add_acl(sync_url, 1, "no-process-module", "PossibleNot:\n  Action: [AgentTicketProcess]\n")
    assert (await _start_form(client, AGENT, 3)).status_code == 200
    assert (await _start_form(client, AGENT, 3, ticket_id=TICKET_1)).status_code == 409
    # A Process ACL hides the form like the list.
    _add_acl(sync_url, 2, "hide-p3", f"PossibleNot:\n  Process: [{process_eid(3)}]\n")
    assert (await _start_form(client, AGENT, 3)).status_code == 409


# --- process context of the option ACLs (AgentTicketProcess.pm:1103-1105, 2638, 4319) -----------


def _queue_values(form: dict[str, Any]) -> list[str]:
    return [
        o["label"] for o in _by_name(form)["Queue"]["options"] if o["label"] in (Q_JUNK, Q_SUPPORT)
    ]


_HIDE_JUNK = f"PossibleNot:\n  Ticket:\n    Queue: [{Q_JUNK}]\n"


@pytest.mark.db
async def test_agent_option_acl_keyed_on_activity_dialog_id(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    await _enroll(client, AGENT, TICKET_1)
    _add_acl(
        sync_url,
        1,
        "dialog-2-no-junk",
        _HIDE_JUNK,
        process_props=f"ActivityDialogEntityID: [{dialog_eid(2)}]",
    )
    form = (await client.get(f"{AGENT}/ticket/{TICKET_1}/dialog/{dialog_eid(2)}/form")).json()
    assert _queue_values(form) == [Q_SUPPORT]
    # Another dialog's form is not affected (that one has no Queue field: use the start form of
    # a process whose start dialog differs).
    other = (await _start_form(client, AGENT, 1)).json()
    assert [f["name"] for f in other["fields"]] == ["Title"]


@pytest.mark.db
async def test_agent_start_form_option_acl_keyed_on_process_and_activity(
    client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _add_dialog(conn, 1, {"Queue": "Display: '1'"})
        _add_process(conn, 1, start_dialog=1, dialogs=[1])
        _add_dialog(conn, 2, {"Queue": "Display: '1'"})
        _add_process(conn, 2, start_dialog=2, dialogs=[2])

    _build(sync_url, setup)
    assert _queue_values((await _start_form(client, AGENT, 1)).json()) == [Q_JUNK, Q_SUPPORT]
    _add_acl(
        sync_url,
        1,
        "p1-no-junk",
        _HIDE_JUNK,
        process_props=f"ProcessEntityID: [{process_eid(1)}]",
    )
    assert _queue_values((await _start_form(client, AGENT, 1)).json()) == [Q_SUPPORT]
    assert _queue_values((await _start_form(client, AGENT, 2)).json()) == [Q_JUNK, Q_SUPPORT]
    # The start activity is part of the context as well.
    _add_acl(
        sync_url,
        2,
        "p2-activity-no-support",
        "PossibleNot:\n  Ticket:\n    Queue: [Support-889]\n",
        process_props=f"ActivityEntityID: [Activity-{889300 + 4}]",
    )
    assert _queue_values((await _start_form(client, AGENT, 2)).json()) == [Q_JUNK]
    assert _queue_values((await _start_form(client, AGENT, 1)).json()) == [Q_SUPPORT]


@pytest.mark.db
async def test_customer_option_acl_keyed_on_process_context(
    portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    _add_acl(
        sync_url,
        1,
        "customer-p1-no-junk",
        _HIDE_JUNK,
        action="CustomerTicketProcess",
        process_props=f"ProcessEntityID: [{process_eid(1)}]\nActivityDialogEntityID: "
        f"[{dialog_eid(1)}]\nActivityEntityID: [Activity-{889302}]",
    )
    start = (await _start_form(portal_client, PORTAL)).json()
    assert [f["name"] for f in start["fields"]] == ["Title"]  # dialog 1 has no Queue: see below
    await _enroll(portal_client, PORTAL, TICKET_1)
    queue = (
        await portal_client.get(f"{PORTAL}/ticket/{TICKET_1}/dialog/{dialog_eid(2)}/form")
    ).json()
    # Dialog 2 differs from the ACL's ActivityDialogEntityID: the ACL must NOT match ...
    assert _queue_values(queue) == [Q_JUNK, Q_SUPPORT]


@pytest.mark.db
async def test_customer_option_acl_matches_on_dialog_id(
    portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    await _enroll(portal_client, PORTAL, TICKET_1)
    _add_acl(
        sync_url,
        1,
        "customer-d2-no-junk",
        _HIDE_JUNK,
        action="CustomerTicketProcess",
        process_props=f"ProcessEntityID: [{process_eid(1)}]\n"
        f"ActivityDialogEntityID: [{dialog_eid(2)}]",
    )
    queue = (
        await portal_client.get(f"{PORTAL}/ticket/{TICKET_1}/dialog/{dialog_eid(2)}/form")
    ).json()
    assert _queue_values(queue) == [Q_SUPPORT]


# --- foreign / inaccessible tickets -----------------------------------------------------------


@pytest.mark.db
async def test_dialog_form_on_foreign_tickets_is_refused(
    client: AsyncClient, portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _two_dialogs)
    d = dialog_eid(1)
    # Agent A has no group on the foreign queue; the 404 is for a missing ticket.
    assert (
        await client.get(f"{AGENT}/ticket/{TICKET_FOREIGN_QUEUE}/dialog/{d}/form")
    ).status_code == 403
    assert (
        await client.get(
            f"{AGENT}/{process_eid(1)}/start-form", params={"ticket_id": TICKET_FOREIGN_QUEUE}
        )
    ).status_code == 403
    # Customer: a ticket of another customer.
    assert (
        await portal_client.get(f"{PORTAL}/ticket/{TICKET_FOREIGN_CUSTOMER}/dialog/{d}/form")
    ).status_code == 403
    assert (
        await portal_client.get(
            f"{PORTAL}/{process_eid(1)}/start-form", params={"ticket_id": TICKET_FOREIGN_CUSTOMER}
        )
    ).status_code == 403
    assert (await portal_client.get(f"{PORTAL}/ticket/889999/dialog/{d}/form")).status_code == 404
