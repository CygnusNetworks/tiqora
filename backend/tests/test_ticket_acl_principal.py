"""ACL principal (agent AND customer), process ACL kinds and ``filter_process_options``.

Seed ids live in the 884000-884499 band (rulings F8). ACLs are read from the live ``acl``
rows (no deployed revision exists in this module, ``load_valid_acls`` falls back to them).
Znuny references: ``TicketACL.pm:155-175`` (principal), ``:1118-1146`` (Process checks),
``:1253-1332`` (CustomerUser checks), ``CustomerGroup.pm:266-281`` (Group_<perm>).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.deployed import clear_revision_cache
from tiqora.domain.ticket_acl import (
    AclPrincipal,
    apply_ticket_acl,
    filter_process_options,
    load_customer_user_checks,
)
from tiqora.process.actor import ProcessActor
from tiqora.znuny.sysconfig import yaml_encode_effective

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT = 884010
GROUP_A, GROUP_B, GROUP_INVALID, GROUP_ZERO = 884020, 884021, 884022, 884023
QUEUE_A, QUEUE_B = 884030, 884031
TICKET_A, TICKET_B = 884040, 884041
SYSCONFIG_DEFAULT_ID, SYSCONFIG_MODIFIED_ID = 884050, 884051
ACL_BASE = 884060

_SEEDED_URLS: list[str] = []


def _cleanup(conn: Any) -> None:
    conn.execute(text("DELETE FROM tiqora_acl_revision"))
    conn.execute(text(f"DELETE FROM acl WHERE id BETWEEN {ACL_BASE} AND {ACL_BASE + 29}"))
    conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 884040 AND 884049"))
    conn.execute(text("DELETE FROM queue WHERE id BETWEEN 884030 AND 884039"))
    conn.execute(text("DELETE FROM group_customer_user WHERE group_id BETWEEN 884020 AND 884029"))
    conn.execute(text("DELETE FROM permission_groups WHERE id BETWEEN 884020 AND 884029"))
    conn.execute(text("DELETE FROM customer_user WHERE id BETWEEN 884001 AND 884009"))
    conn.execute(text(f"DELETE FROM sysconfig_modified WHERE id = {SYSCONFIG_MODIFIED_ID}"))
    conn.execute(text(f"DELETE FROM sysconfig_default WHERE id = {SYSCONFIG_DEFAULT_ID}"))
    conn.execute(text("DELETE FROM users WHERE id = 884010"))


def _seed(conn: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (884010, 'agent-884', 'x', 'Ann', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"t": T0},
    )
    for cid, login, cust in (
        (884001, "cu-884001", "cust-884-a"),
        (884002, "cu-884002", "other-co"),
        (884003, "cu-884003", "example-co"),
    ):
        conn.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :email, :cust, 'Cora', :last, 1, :t, 1, :t, 1)"
            ),
            {
                "id": cid,
                "login": login,
                "email": f"{login}@example.org",
                "cust": cust,
                "last": login,
                "t": T0,
            },
        )
    for gid, name, valid in (
        (GROUP_A, "support-884", 1),
        (GROUP_B, "other-884", 1),
        (GROUP_INVALID, "invalid-884", 2),
        (GROUP_ZERO, "zero-884", 1),
    ):
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :name, :v, :t, 1, :t, 1)"
            ),
            {"id": gid, "name": name, "v": valid, "t": T0},
        )
    for queue_id, name in ((QUEUE_A, "Queue-884a"), (QUEUE_B, "Queue-884b")):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :name, 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": name, "t": T0},
        )
    for ticket_id, queue_id, tn, cust, cu in (
        (TICKET_A, QUEUE_A, "2026030488401", "example-co", "cu-884003"),
        (TICKET_B, QUEUE_B, "2026030488402", "other-co", "cu-884002"),
    ):
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
                " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'T884', :q, 1, 1, 1, 1, 3, 4, :cust, :cu, 0, 0, 0, 0, 0, 0,"
                " 0, :t, 1, :t, 1)"
            ),
            {"id": ticket_id, "tn": tn, "q": queue_id, "cust": cust, "cu": cu, "t": T0},
        )


def _insert_acl(conn: Any, n: int, name: str, match: str, change: str) -> None:
    conn.execute(
        text(
            "INSERT INTO acl (id, name, comments, description, valid_id, stop_after_match,"
            " config_match, config_change, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :name, NULL, '', 1, 0, :match, :change, :t, 1, :t, 1)"
        ),
        {"id": ACL_BASE + n, "name": name, "match": match, "change": change, "t": T0},
    )


def _set_customer_group_support(conn: Any, value: int) -> None:
    conn.execute(
        text(
            "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
            " is_readonly, is_required, is_valid, has_configlevel, user_modification_possible,"
            " user_modification_active, xml_content_raw, xml_content_parsed, xml_filename,"
            " effective_value, is_dirty, exclusive_lock_guid, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'CustomerGroupSupport', 'x', 'x', 0, 0, 1, 1,"
            " 0, 0, 0, 'x', 'x', 'Framework.xml', :e, 0, '0', current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"id": SYSCONFIG_DEFAULT_ID, "e": yaml_encode_effective(0)},
    )
    conn.execute(
        text(
            "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id, is_valid,"
            " user_modification_active, effective_value, is_dirty, reset_to_default,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, :did, 'CustomerGroupSupport', NULL, 1, 0, :e, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": SYSCONFIG_MODIFIED_ID,
            "did": SYSCONFIG_DEFAULT_ID,
            "e": yaml_encode_effective(value),
        },
    )


def _grant(conn: Any, login: str, group_id: int, key: str, value: int = 1) -> None:
    conn.execute(
        text(
            "INSERT INTO group_customer_user (user_id, group_id, permission_key, permission_value,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, :k, :v, :t, 1, :t, 1)"
        ),
        {"u": login, "g": group_id, "k": key, "v": value, "t": T0},
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED_URLS):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED_URLS.clear()
    clear_revision_cache()


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> Generator[str, None, None]:
    _SEEDED_URLS.append(mariadb_znuny_url)
    clear_revision_cache()
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        _cleanup(conn)
        _seed(conn)
    engine.dispose()
    return mariadb_znuny_url  # type: ignore[return-value]


@pytest.fixture
async def session(sync_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(sync_url.replace("mysql+pymysql://", "mysql+aiomysql://"))
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _add_acl(sync_url: str, n: int, name: str, match: str, change: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _insert_acl(conn, n, name, match, change)
    engine.dispose()


_HIDE_PROCESS = "PossibleNot:\n  Process: [Process-884001]\n"
_PROCESSES = ["Process-884001", "Process-884002"]


async def _processes(session: AsyncSession, principal: AclPrincipal) -> list[str]:
    return await filter_process_options(
        session,
        principal=principal,
        kind="Process",
        items=_PROCESSES,
        action="CustomerTicketProcess",
    )


# --- principal ---------------------------------------------------------------------------------


async def test_customer_principal_is_not_root(session: AsyncSession, sync_url: str) -> None:
    _add_acl(
        sync_url,
        1,
        "hide-for-cu1",
        "Properties:\n  CustomerUser:\n    UserLogin: [cu-884001]\n",
        _HIDE_PROCESS,
    )
    kept = await _processes(session, AclPrincipal.customer("cu-884001"))
    assert kept == ["Process-884002"]
    # Another customer does not match the ACL.
    assert await _processes(session, AclPrincipal.customer("cu-884002")) == _PROCESSES
    # The login lookup is case-insensitive (CustomerUser/DB.pm:1207-1212).
    assert await _processes(session, AclPrincipal.customer("CU-884001")) == ["Process-884002"]


async def test_agent_root_still_bypasses(session: AsyncSession, sync_url: str) -> None:
    _add_acl(sync_url, 1, "hide-for-all", "{}\n", _HIDE_PROCESS)
    assert await _processes(session, AclPrincipal.agent(AGENT)) == ["Process-884002"]
    assert await _processes(session, AclPrincipal.agent(1)) == _PROCESSES  # TicketACL.pm:175


async def test_no_principal_returns_original(session: AsyncSession, sync_url: str) -> None:
    _add_acl(sync_url, 1, "hide-for-all", "{}\n", _HIDE_PROCESS)
    assert await _processes(session, AclPrincipal()) == _PROCESSES  # TicketACL.pm:155-160


def test_actor_acl_principal() -> None:
    agent = ProcessActor.agent(7)
    assert agent.acl_principal == AclPrincipal(user_id=7, customer_user_login=None)
    customer = ProcessActor.customer(login="cu-884001", customer_id="cust-884-a")
    # Never the portal system user id: that would be an agent principal.
    assert customer.acl_principal == AclPrincipal(user_id=None, customer_user_login="cu-884001")


# --- customer checks and groups ------------------------------------------------------------------


async def test_customer_checks_carry_user_data(session: AsyncSession) -> None:
    checks = await load_customer_user_checks(session, "cu-884003")
    assert checks["UserLogin"] == "cu-884003"
    assert checks["UserID"] == "cu-884003"
    assert checks["UserCustomerID"] == "example-co"
    assert checks["UserEmail"] == "cu-884003@example.org"
    assert checks["UserFirstname"] == "Cora"
    assert checks["UserLastname"] == "cu-884003"
    assert checks["UserFullname"] == "Cora cu-884003"


async def test_customer_groups_feed_checks(session: AsyncSession, sync_url: str) -> None:
    """CustomerGroupSupport on: group_customer_user rows (CustomerGroup.pm:270-281)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _set_customer_group_support(conn, 1)
        _grant(conn, "cu-884002", GROUP_A, "rw")  # rw implies every permission key
        _grant(conn, "cu-884002", GROUP_B, "ro")
        _grant(conn, "cu-884002", GROUP_INVALID, "rw")  # invalid group
        _grant(conn, "cu-884002", GROUP_ZERO, "rw", value=0)  # permission_value 0
        _grant(conn, "cu-884001", GROUP_B, "rw")  # someone else's row
    engine.dispose()

    checks = await load_customer_user_checks(session, "cu-884002")
    assert checks["Group_rw"] == ["support-884"]
    assert checks["Group_ro"] == ["other-884", "support-884"]
    nobody = await load_customer_user_checks(session, "cu-884003")
    assert nobody["Group_ro"] == [] and nobody["Group_rw"] == []

    # ... and an ACL keyed on Group_rw sees it (new snapshot: the ACL row is inserted after
    # this session's first read).
    await session.rollback()
    _add_acl(
        sync_url,
        1,
        "support-only",
        "Properties:\n  CustomerUser:\n    Group_rw: [support-884]\n",
        _HIDE_PROCESS,
    )
    assert await _processes(session, AclPrincipal.customer("cu-884002")) == ["Process-884002"]
    assert await _processes(session, AclPrincipal.customer("cu-884001")) == _PROCESSES


async def test_customer_groups_support_off_lists_all_valid_groups(
    session: AsyncSession, sync_url: str
) -> None:
    """CustomerGroupSupport off (default): every valid group per type (CustomerGroup.pm:266-269)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _grant(conn, "cu-884002", GROUP_A, "ro")  # rows are ignored while support is off
        valid = sorted(
            r[0]
            for r in conn.execute(text("SELECT name FROM permission_groups WHERE valid_id = 1"))
        )
    engine.dispose()
    assert "support-884" in valid and "other-884" in valid and "invalid-884" not in valid

    for login in ("cu-884002", "cu-884001", "unknown-884"):
        checks = await load_customer_user_checks(session, login)
        assert checks["Group_ro"] == valid
        assert checks["Group_rw"] == valid
    assert "UserLogin" not in await load_customer_user_checks(session, "unknown-884")
    await session.rollback()  # new snapshot for the ACL row inserted below

    _add_acl(
        sync_url,
        1,
        "any-customer-in-support",
        "Properties:\n  CustomerUser:\n    Group_ro: [support-884]\n",
        _HIDE_PROCESS,
    )
    assert await _processes(session, AclPrincipal.customer("cu-884001")) == ["Process-884002"]


# --- process checks ------------------------------------------------------------------------------


async def test_activity_dialog_acl_uses_process_checks(
    session: AsyncSession, sync_url: str
) -> None:
    _add_acl(
        sync_url,
        1,
        "dialog-1",
        "Properties:\n  Process:\n    ActivityDialogEntityID: [AD-884001]\n",
        "Possible:\n  ActivityDialog: [AD-884002]\n",
    )
    items = ["AD-884001", "AD-884002"]
    kept = await filter_process_options(
        session,
        principal=AclPrincipal.agent(AGENT),
        kind="ActivityDialog",
        items=items,
        action="AgentTicketProcess",
        activity_dialog_entity_id="AD-884001",
    )
    assert kept == ["AD-884002"]
    # A different dialog in scope does not match the ACL.
    other = await filter_process_options(
        session,
        principal=AclPrincipal.agent(AGENT),
        kind="ActivityDialog",
        items=items,
        action="AgentTicketProcess",
        activity_dialog_entity_id="AD-884002",
    )
    assert other == items


async def test_process_entity_id_and_activity_entity_id_checks(
    session: AsyncSession, sync_url: str
) -> None:
    _add_acl(
        sync_url,
        1,
        "proc",
        "Properties:\n  Process:\n    ProcessEntityID: [Process-884001]\n"
        "    ActivityEntityID: [Activity-884001]\n",
        "PossibleNot:\n  ActivityDialog: [AD-884002]\n",
    )
    common = {
        "principal": AclPrincipal.agent(AGENT),
        "kind": "ActivityDialog",
        "items": ["AD-884001", "AD-884002"],
        "action": "AgentTicketProcess",
    }
    hit = await filter_process_options(
        session, **common, process_entity_id="Process-884001", activity_entity_id="Activity-884001"
    )
    assert hit == ["AD-884001"]
    miss = await filter_process_options(session, **common, process_entity_id="Process-884001")
    assert miss == ["AD-884001", "AD-884002"]


async def test_action_acl(session: AsyncSession, sync_url: str) -> None:
    _add_acl(
        sync_url,
        1,
        "no-process-in-a",
        "PropertiesDatabase:\n  Queue:\n    Name: [Queue-884a]\n",
        "PossibleNot:\n  Action: [AgentTicketProcess]\n",
    )
    common = {
        "principal": AclPrincipal.agent(AGENT),
        "kind": "Action",
        "items": ["AgentTicketProcess"],
        "action": "AgentTicketZoom",
    }
    assert await filter_process_options(session, **common, ticket_id=TICKET_A) == []
    assert await filter_process_options(session, **common, ticket_id=TICKET_B) == [
        "AgentTicketProcess"
    ]


# --- customer on tickets -------------------------------------------------------------------------


async def test_agent_on_ticket_sees_ticket_customer_in_checks(
    session: AsyncSession, sync_url: str
) -> None:
    _add_acl(
        sync_url,
        1,
        "example-co-only",
        "PropertiesDatabase:\n  CustomerUser:\n    UserCustomerID: [example-co]\n",
        _HIDE_PROCESS,
    )
    common = {
        "principal": AclPrincipal.agent(AGENT),
        "kind": "Process",
        "items": _PROCESSES,
        "action": "AgentTicketProcess",
    }
    assert await filter_process_options(session, **common, ticket_id=TICKET_A) == ["Process-884002"]
    assert await filter_process_options(session, **common, ticket_id=TICKET_B) == _PROCESSES


async def test_customer_principal_overrides_ticket_customer_live_not_database(
    session: AsyncSession, sync_url: str
) -> None:
    # TicketACL.pm:1273-1274 overrides Checks{Ticket}{CustomerUserID}; ChecksDatabase keeps it.
    _add_acl(
        sync_url,
        1,
        "live",
        "Properties:\n  Ticket:\n    CustomerUserID: [cu-884001]\n",
        "PossibleNot:\n  Process: [Process-884001]\n",
    )
    _add_acl(
        sync_url,
        2,
        "db",
        "PropertiesDatabase:\n  Ticket:\n    CustomerUserID: [cu-884003]\n",
        "PossibleNot:\n  Process: [Process-884002]\n",
    )
    kept = await filter_process_options(
        session,
        principal=AclPrincipal.customer("cu-884001"),
        kind="Process",
        items=_PROCESSES,
        action="CustomerTicketProcess",
        ticket_id=TICKET_A,
    )
    assert kept == []


async def test_existing_agent_call_shape_still_works(session: AsyncSession, sync_url: str) -> None:
    _add_acl(sync_url, 1, "no-new", "{}\n", "PossibleNot:\n  Ticket:\n    State: [new]\n")
    filtered = await apply_ticket_acl(
        session,
        user_id=AGENT,
        data={1: "new", 4: "open"},
        return_type="Ticket",
        return_sub_type="State",
    )
    assert filtered == {4: "open"}
