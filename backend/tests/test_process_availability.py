"""State gate, interface and ACLs on every process surface (agent + portal + engine).

Seed ids live in the 886000-886499 band (rulings F8). Znuny references:
``Process.pm`` ``ProcessList`` (state + Interface), ``ActivityDialog.pm`` ``ActivityDialogGet``
(Interface), ``AgentTicketProcess.pm:114-224`` (dialog Permission, Action ACL, Process ACL),
``AgentTicketZoom.pm:1620-1650`` (dialog Permission + ActivityDialog ACL).
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
from tiqora.process.engine import submit_activity_dialog
from tiqora.process.exceptions import ProcessPermissionDenied
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT_A, AGENT_B = 886010, 886011  # A: rw on the queue's group, B: ro only
GROUP = 886020
QUEUE = 886030
TICKET_1, TICKET_2 = 886040, 886041
CUSTOMER_ID_ROW = 886001
CUSTOMER_LOGIN = "cu-886001"
CUSTOMER_COMPANY = "cust-886"
ACL_BASE = 886450

AGENT_ONLY = ["AgentInterface"]
CUSTOMER_ONLY = ["CustomerInterface"]
BOTH = ["AgentInterface", "CustomerInterface"]

_SEEDED_URLS: list[str] = []


def process_eid(n: int) -> str:
    return f"Process-{886100 + n}"


def dialog_eid(n: int) -> str:
    return f"ActivityDialog-{886200 + n}"


def _cleanup(conn: Any) -> None:
    for stmt in (
        f"DELETE FROM acl WHERE id BETWEEN {ACL_BASE} AND {ACL_BASE + 49}",
        "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 886040 AND 886049",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 886040 AND 886049",
        "DELETE FROM ticket WHERE id BETWEEN 886040 AND 886049",
        "DELETE FROM queue WHERE id BETWEEN 886030 AND 886039",
        "DELETE FROM group_user WHERE group_id BETWEEN 886020 AND 886029",
        "DELETE FROM permission_groups WHERE id BETWEEN 886020 AND 886029",
        "DELETE FROM customer_user WHERE id BETWEEN 886001 AND 886009",
        "DELETE FROM users WHERE id BETWEEN 886010 AND 886019",
        "DELETE FROM pm_process WHERE id BETWEEN 886100 AND 886199",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 886200 AND 886299",
        "DELETE FROM pm_activity WHERE id BETWEEN 886300 AND 886399",
        "DELETE FROM pm_transition WHERE id BETWEEN 886400 AND 886449",
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
    """The engine's outbox / cache-invalidation side effects (tables exist from _seed)."""
    for table in ("tiqora_event_outbox", "tiqora_cache_invalidation"):
        conn.execute(text(f"DELETE FROM {table} WHERE ticket_id BETWEEN 886040 AND 886049"))


_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)


def _add_dialog(conn: Any, n: int, interfaces: list[str], permission: str = "") -> str:
    iface = "".join(f"- {i}\n" for i in interfaces) if interfaces else ""
    cfg = (
        "DescriptionShort: Set title\nFieldOrder:\n- Title\nFields:\n  Title:\n"
        "    Display: '1'\nSubmitButtonText: Submit\n"
        + (f"Interface:\n{iface}" if iface else "Interface: []\n")
        + f"Permission: '{permission}'\n"
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {"id": 886200 + n, "eid": dialog_eid(n), "name": f"Dialog {n}", "cfg": cfg},
    )
    return dialog_eid(n)


def _add_process(
    conn: Any, n: int, *, start_dialog: int, dialogs: list[int], state: str = "S1"
) -> str:
    """Process ``n``: activity A (the given dialogs) -> activity B (none) via one transition."""
    act_a, act_b, trans = (
        f"Activity-{886300 + 2 * n}",
        f"Activity-{886301 + 2 * n}",
        f"T-{886400 + n}",
    )
    dialog_cfg = "".join(f"  '{i + 1}': {dialog_eid(d)}\n" for i, d in enumerate(dialogs))
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": 886100 + n,
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
            "id": 886300 + 2 * n,
            "eid": act_a,
            "name": f"A{n}",
            "cfg": f"ActivityDialog:\n{dialog_cfg}",
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {"id": 886301 + 2 * n, "eid": act_b, "name": f"B{n}", "cfg": "ActivityDialog: {}\n"},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 886400 + n, "eid": trans, "name": f"T{n}", "cfg": "ConditionLinking: and\n"},
    )
    return process_eid(n)


def _seed(conn: Any) -> None:
    for uid, login in ((AGENT_A, "agent-886a"), (AGENT_B, "agent-886b")):
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Av', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": uid, "login": login, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'avail-886', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP, "t": T0},
    )
    for uid, key in ((AGENT_A, "rw"), (AGENT_B, "ro")):
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": uid, "g": GROUP, "k": key, "t": T0},
        )
    conn.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'Queue-886', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
        ),
        {"id": QUEUE, "g": GROUP, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :login, 'cu886@example.org', :cust, 'Cora', 'Cust', 1, :t, 1, :t, 1)"
        ),
        {"id": CUSTOMER_ID_ROW, "login": CUSTOMER_LOGIN, "cust": CUSTOMER_COMPANY, "t": T0},
    )
    for tid in (TICKET_1, TICKET_2):
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'T886', :q, 1, 1, :u, :u, 3, 4, :cust, :cu, 0, 0, 0, 0, 0, 0,"
                " 0, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"2026030488{tid}",
                "q": QUEUE,
                "u": AGENT_A,
                "cust": CUSTOMER_COMPANY,
                "cu": CUSTOMER_LOGIN,
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
    # The portal reads tiqora_settings (company_tickets_enabled).
    TiqoraBase.metadata.create_all(engine, tables=[TiqoraSettings.__table__])
    with engine.begin() as conn:
        for ddl in _TIQORA_DDL:
            conn.execute(text(ddl))
        _clean_tiqora_rows(conn)
        _cleanup(conn)
        _seed(conn)
    engine.dispose()
    return mariadb_znuny_url


def _exec(url: str, *stmts: tuple[str, dict[str, Any]]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        for sql, params in stmts:
            conn.execute(text(sql), params)
    engine.dispose()


def _build(url: str, setup: Callable[[Any], None]) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        setup(conn)
    engine.dispose()


def _add_acl(url: str, n: int, name: str, change: str, match: str = "{}\n") -> None:
    _exec(
        url,
        (
            "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
            " config_match, config_change, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :name, NULL, '', 1, 0, :m, :c, :t, 1, :t, 1)",
            {"id": ACL_BASE + n, "name": name, "m": match, "c": change, "t": T0},
        ),
    )
    clear_revision_cache()


def _set_state(url: str, n: int, state: str) -> None:
    _exec(
        url,
        (
            "UPDATE pm_process SET state_entity_id = :s WHERE id = :id",
            {"s": state, "id": 886100 + n},
        ),
    )


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


def _agent_client(engine: AsyncEngine, user_id: int) -> AsyncClient:
    app = _app(engine)
    user = AuthenticatedUser(
        id=user_id, login=f"agent-{user_id}", first_name="A", last_name="B", auth_method="session"
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    async with _agent_client(engine, AGENT_A) as c:
        yield c


@pytest.fixture
async def client_b(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    async with _agent_client(engine, AGENT_B) as c:
        yield c


@pytest.fixture
async def portal_client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = _app(engine)
    customer = AuthenticatedCustomer(
        id=CUSTOMER_ID_ROW,
        login=CUSTOMER_LOGIN,
        email="cu886@example.org",
        customer_id=CUSTOMER_COMPANY,
        first_name="Cora",
        last_name="Cust",
    )
    app.dependency_overrides[get_current_customer] = lambda: customer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s


def _p(path: str = "") -> str:
    return f"/api/v1/process/{path}"


def _pp(path: str = "") -> str:
    return f"/api/portal/process/{path}"


async def _listed(c: AsyncClient, prefix: str = "/api/v1/process/", **params: Any) -> list[str]:
    resp = await c.get(prefix, params=params)
    assert resp.status_code == 200, resp.text
    return [p["entity_id"] for p in resp.json()]


async def _state(c: AsyncClient, ticket_id: int, prefix: str = "/api/v1/process") -> dict[str, Any]:
    resp = await c.get(f"{prefix}/ticket/{ticket_id}/state")
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def _start(c: AsyncClient, ticket_id: int, n: int, prefix: str = "/api/v1/process") -> Any:
    return await c.post(
        f"{prefix}/ticket/{ticket_id}/start", json={"process_entity_id": process_eid(n)}
    )


async def _submit(
    c: AsyncClient, ticket_id: int, dialog_n: int, prefix: str = "/api/v1/process"
) -> Any:
    return await c.post(
        f"{prefix}/ticket/{ticket_id}/submit",
        json={"activity_dialog_entity_id": dialog_eid(dialog_n), "field_values": {"Title": "Neu"}},
    )


def _basic(conn: Any) -> None:
    """Process 1: one dialog on both interfaces, start dialog = that dialog."""
    _add_dialog(conn, 1, BOTH)
    _add_process(conn, 1, start_dialog=1, dialogs=[1])


# --- state gate --------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "listed", "dialogs_on_running_ticket", "submit_status"),
    [
        ("S1", True, True, 200),
        ("S3", False, True, 200),  # FadeAway: running tickets continue, no new starts
        ("S2", False, False, 409),  # Inactive: nothing
    ],
)
async def test_state_gate_matrix(
    client: AsyncClient,
    sync_url: str,
    state: str,
    listed: bool,
    dialogs_on_running_ticket: bool,
    submit_status: int,
) -> None:
    _build(sync_url, _basic)
    assert (await _start(client, TICKET_1, 1)).status_code == 200  # started while Active
    _set_state(sync_url, 1, state)

    assert (process_eid(1) in await _listed(client)) is listed
    dialogs = (await _state(client, TICKET_1))["available_dialogs"]
    assert [d["entity_id"] for d in dialogs] == (
        [dialog_eid(1)] if dialogs_on_running_ticket else []
    )
    # A new start needs Active.
    assert (await _start(client, TICKET_2, 1)).status_code == (200 if listed else 409)
    assert (await _submit(client, TICKET_1, 1)).status_code == submit_status


# --- interface ---------------------------------------------------------------------------------


@pytest.mark.db
async def test_agent_list_excludes_customer_only_start_dialog(
    client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _basic(conn)
        _add_dialog(conn, 2, CUSTOMER_ONLY)
        _add_process(conn, 2, start_dialog=2, dialogs=[2])

    _build(sync_url, setup)
    listed = await _listed(client)
    assert process_eid(1) in listed
    assert process_eid(2) not in listed
    assert (await _start(client, TICKET_1, 2)).status_code == 409


@pytest.mark.db
async def test_portal_list_excludes_agent_only_start_dialog(
    portal_client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _basic(conn)
        _add_dialog(conn, 2, AGENT_ONLY)
        _add_process(conn, 2, start_dialog=2, dialogs=[2])

    _build(sync_url, setup)
    listed = await _listed(portal_client, "/api/portal/process/")
    assert process_eid(1) in listed
    assert process_eid(2) not in listed
    assert (await _start(portal_client, TICKET_1, 2, "/api/portal/process")).status_code == 409


def _mixed(conn: Any) -> None:
    _add_dialog(conn, 1, BOTH)
    _add_dialog(conn, 2, AGENT_ONLY)
    _add_dialog(conn, 3, CUSTOMER_ONLY)
    _add_dialog(conn, 4, [])
    _add_process(conn, 1, start_dialog=1, dialogs=[1, 2, 3, 4])


@pytest.mark.db
async def test_agent_dialogs_exclude_customer_only_dialogs(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _mixed)
    assert (await _start(client, TICKET_1, 1)).status_code == 200
    ids = [d["entity_id"] for d in (await _state(client, TICKET_1))["available_dialogs"]]
    assert ids == [dialog_eid(1), dialog_eid(2)]
    # A customer-only dialog is refused on submit too.
    assert (await _submit(client, TICKET_1, 3)).status_code == 403


@pytest.mark.db
async def test_dialog_without_interface_is_offered_to_nobody(
    client: AsyncClient, portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _mixed)
    assert (await _start(client, TICKET_1, 1)).status_code == 200
    agent_ids = [d["entity_id"] for d in (await _state(client, TICKET_1))["available_dialogs"]]
    portal_state = await _state(portal_client, TICKET_1, "/api/portal/process")
    portal_ids = [d["entity_id"] for d in portal_state["available_dialogs"]]
    assert agent_ids == [dialog_eid(1), dialog_eid(2)]
    assert portal_ids == [dialog_eid(1), dialog_eid(3)]
    assert dialog_eid(4) not in agent_ids + portal_ids
    assert (await _submit(client, TICKET_1, 4)).status_code == 403
    assert (await _submit(portal_client, TICKET_1, 4, "/api/portal/process")).status_code == 403
    assert (await _submit(portal_client, TICKET_1, 2, "/api/portal/process")).status_code == 403


# --- ACLs --------------------------------------------------------------------------------------


@pytest.mark.db
async def test_acl_hides_process_from_list_and_refuses_start(
    client: AsyncClient, sync_url: str
) -> None:
    def setup(conn: Any) -> None:
        _basic(conn)
        _add_dialog(conn, 2, BOTH)
        _add_process(conn, 2, start_dialog=2, dialogs=[2])

    _build(sync_url, setup)
    _add_acl(sync_url, 1, "hide-p1", f"PossibleNot:\n  Process: [{process_eid(1)}]\n")
    listed = await _listed(client)
    assert process_eid(1) not in listed
    assert process_eid(2) in listed
    assert process_eid(1) not in await _listed(client, ticket_id=TICKET_1)
    assert (await _start(client, TICKET_1, 1)).status_code == 409
    assert (await _start(client, TICKET_1, 2)).status_code == 200


@pytest.mark.db
async def test_acl_hides_dialog_and_refuses_submit(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _mixed)
    assert (await _start(client, TICKET_1, 1)).status_code == 200
    _add_acl(sync_url, 1, "hide-d2", f"PossibleNot:\n  ActivityDialog: [{dialog_eid(2)}]\n")
    ids = [d["entity_id"] for d in (await _state(client, TICKET_1))["available_dialogs"]]
    assert ids == [dialog_eid(1)]
    assert (await _submit(client, TICKET_1, 2)).status_code == 403
    assert (await _submit(client, TICKET_1, 1)).status_code == 200


@pytest.mark.db
async def test_portal_submit_of_acl_hidden_dialog_is_403(
    portal_client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _mixed)
    assert (await _start(portal_client, TICKET_1, 1, "/api/portal/process")).status_code == 200
    _add_acl(sync_url, 1, "hide-d3", f"PossibleNot:\n  ActivityDialog: [{dialog_eid(3)}]\n")
    state = await _state(portal_client, TICKET_1, "/api/portal/process")
    assert [d["entity_id"] for d in state["available_dialogs"]] == [dialog_eid(1)]
    assert (await _submit(portal_client, TICKET_1, 3, "/api/portal/process")).status_code == 403
    assert (await _submit(portal_client, TICKET_1, 1, "/api/portal/process")).status_code == 200


@pytest.mark.db
async def test_action_acl_blocks_all_process_surfaces(client: AsyncClient, sync_url: str) -> None:
    _build(sync_url, _basic)
    assert (await _start(client, TICKET_1, 1)).status_code == 200
    _add_acl(sync_url, 1, "no-process-module", "PossibleNot:\n  Action: [AgentTicketProcess]\n")

    assert (await _state(client, TICKET_1))["available_dialogs"] == []
    state_2 = await _state(client, TICKET_2)
    assert state_2["can_start"] is False
    assert (await _start(client, TICKET_2, 1)).status_code == 409
    assert (await _submit(client, TICKET_1, 1)).status_code == 403
    # With a ticket context the list is empty; without one the Action ACL is not evaluated (F36).
    assert await _listed(client, ticket_id=TICKET_2) == []
    assert process_eid(1) in await _listed(client)


@pytest.mark.db
async def test_dialog_permission_is_kept_for_agents(
    client: AsyncClient, client_b: AsyncClient, session: AsyncSession, sync_url: str
) -> None:
    """F10: the dialog ``Permission`` hides the dialog and refuses the submit for agents
    (AgentTicketProcess.pm:140-153, AgentTicketZoom.pm:1620-1636)."""

    def setup(conn: Any) -> None:
        _add_dialog(conn, 1, BOTH)
        _add_dialog(conn, 2, AGENT_ONLY, permission="rw")
        _add_process(conn, 1, start_dialog=1, dialogs=[1, 2])

    _build(sync_url, setup)
    assert (await _start(client, TICKET_1, 1)).status_code == 200

    # Agent A (rw) sees and can submit it; agent B (ro) neither sees it ...
    a_ids = [d["entity_id"] for d in (await _state(client, TICKET_1))["available_dialogs"]]
    b_ids = [d["entity_id"] for d in (await _state(client_b, TICKET_1))["available_dialogs"]]
    assert a_ids == [dialog_eid(1), dialog_eid(2)]
    assert b_ids == [dialog_eid(1)]
    # ... nor can submit it, on the REST surface and in the engine itself.
    assert (await _submit(client_b, TICKET_1, 2)).status_code == 403
    with pytest.raises(ProcessPermissionDenied):
        await submit_activity_dialog(
            session,
            ticket_id=TICKET_1,
            activity_dialog_entity_id=dialog_eid(2),
            field_values={"Title": "x"},
            user_id=AGENT_B,
            sysconfig=SysConfig(session),
        )


# --- state out ---------------------------------------------------------------------------------


@pytest.mark.db
async def test_state_out_reports_process_state_and_can_start(
    client: AsyncClient, sync_url: str
) -> None:
    _build(sync_url, _basic)
    before = await _state(client, TICKET_1)
    assert before["process_entity_id"] is None
    assert before["process_state"] is None
    assert before["can_start"] is True

    assert (await _start(client, TICKET_1, 1)).status_code == 200
    active = await _state(client, TICKET_1)
    assert active["process_state"] == "Active"
    assert active["can_start"] is False  # already in a process

    _set_state(sync_url, 1, "S3")
    assert (await _state(client, TICKET_1))["process_state"] == "FadeAway"
    _set_state(sync_url, 1, "S2")
    assert (await _state(client, TICKET_1))["process_state"] == "Inactive"
    # Nothing startable is left for a ticket outside a process.
    assert (await _state(client, TICKET_2))["can_start"] is False
