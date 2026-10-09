"""Condition data (Znuny ``TicketGet`` + dynamic fields) and ``run_transitions`` on a real DB.

Seed ids live in the 887100-887499 band (rulings F8). Znuny references: ``Ticket.pm:1240-1360``
(TicketGet keys), ``Process.pm:395-445`` (transition check on Active/FadeAway processes only),
``TransitionValidation/Base.pm:154-285`` (smart tags in Base-type conditions).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tiqora.deployed import clear_revision_cache
from tiqora.process.conditions import get_condition_data
from tiqora.process.engine import get_ticket_attrs, run_transitions, submit_activity_dialog
from tiqora.process.ticket_state import get_ticket_process_state
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)

AGENT = 887110
DF_MULTI, DF_TEXT, DF_UNSET, DF_DATE = 887120, 887121, 887122, 887123
TICKET_DATA, TICKET_TYPE, TICKET_TAG, TICKET_INACTIVE = 887140, 887141, 887142, 887143
TYPE_ID = 30887  # ticket_type.id is a SMALLINT; outside the band, unique by value
SERVICE_ID = 887160
SLA_ID = 887170
PROC_BASE = 887100  # process n: pm_process 887100+n, activity 887300+2n/+1, transition 887400+n

_SEEDED_URLS: list[str] = []


def _eid(kind: str, n: int, offset: int = 0) -> str:
    base = {"Process": 887100, "ActivityDialog": 887200, "Activity": 887300, "T": 887400}[kind]
    return f"{kind}-{base + n + offset}"


def _cleanup(conn: Any) -> None:
    for stmt in (
        "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 887140 AND 887149",
        "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 887140 AND 887149",
        "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 887140 AND 887149",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 887140 AND 887149",
        "DELETE FROM ticket WHERE id BETWEEN 887140 AND 887149",
        "DELETE FROM dynamic_field WHERE id BETWEEN 887120 AND 887129",
        "DELETE FROM ticket_type WHERE id = 30887",
        "DELETE FROM service WHERE id = 887160",
        "DELETE FROM sla WHERE id = 887170",
        "DELETE FROM users WHERE id = 887110",
        "DELETE FROM pm_process WHERE id BETWEEN 887100 AND 887199",
        "DELETE FROM pm_activity_dialog WHERE id BETWEEN 887200 AND 887299",
        "DELETE FROM pm_activity WHERE id BETWEEN 887300 AND 887399",
        "DELETE FROM pm_transition WHERE id BETWEEN 887400 AND 887449",
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

_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)


def _cond(fields_yaml: str) -> str:
    """A one-block ``Condition`` (Znuny dump shape) around an indented ``Fields`` body."""
    head = "Condition:\n  '1':\n    Fields:\n"
    return f"{head}{fields_yaml}    Type: and\n  Type: and\nConditionLinking: and\n"


def _add_process(conn: Any, n: int, condition: str, *, state: str = "S1") -> None:
    """Process ``n``: activity A (one dialog setting Type) -> activity B via one transition."""
    act_a, act_b = _eid("Activity", n, n), _eid("Activity", n, n + 1)
    dialog, trans = _eid("ActivityDialog", n), _eid("T", n)
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": PROC_BASE + n,
            "eid": _eid("Process", n),
            "name": f"Process {n}",
            "st": state,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: {dialog}\n"
                f"Path:\n  {act_a}:\n    {trans}:\n      ActivityEntityID: {act_b}\n"
                f"  {act_b}: {{}}\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {
            "id": 887200 + n,
            "eid": dialog,
            "name": f"Dialog {n}",
            "cfg": (
                "DescriptionShort: Set type\nFieldOrder:\n- Type\nFields:\n  Type:\n"
                "    Display: '1'\nInterface:\n- AgentInterface\nPermission: ''\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {
            "id": 887300 + n + n,
            "eid": act_a,
            "name": f"A{n}",
            "cfg": f"ActivityDialog:\n  '1': {dialog}\n",
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {"id": 887300 + n + n + 1, "eid": act_b, "name": f"B{n}", "cfg": "ActivityDialog: {}\n"},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 887400 + n, "eid": trans, "name": f"T{n}", "cfg": condition},
    )


def _add_ticket(conn: Any, tid: int, *, title: str, type_id: int = 1, full: bool = False) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " sla_id, user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " customer_id, customer_user_id, timeout, until_time, escalation_time,"
            " escalation_update_time, escalation_response_time, escalation_solution_time,"
            " archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tn, :title, 1, :lock, :type, :svc, :sla, :u, :u, 3, 4, :cid, :cuid,"
            " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
        ),
        {
            "id": tid,
            "tn": f"2026030488{tid}",
            "title": title,
            "lock": 2 if full else 1,
            "type": type_id,
            "svc": SERVICE_ID if full else None,
            "sla": SLA_ID if full else None,
            "u": AGENT,
            "cid": "CUST-887" if full else None,
            "cuid": "cora@example.org" if full else None,
            "t": T0,
        },
    )


def _seed(conn: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:id, 'agent-887', 'x', 'Ava', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"id": AGENT, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO ticket_type (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'Incident-887', 1, :t, 1, :t, 1)"
        ),
        {"id": TYPE_ID, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'Service-887', 1, :t, 1, :t, 1)"
        ),
        {"id": SERVICE_ID, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO sla (id, name, first_response_time, update_time, solution_time, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, 'Sla-887', 0, 0, 0, 1, :t, 1, :t, 1)"
        ),
        {"id": SLA_ID, "t": T0},
    )
    for fid, name, ftype in (
        (DF_MULTI, "Multi887", "Multiselect"),
        (DF_TEXT, "Text887", "Text"),
        (DF_UNSET, "Unset887", "Dropdown"),
        (DF_DATE, "Date887", "DateTime"),
    ):
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:id, 0, :n, :n, :id, :ft, 'Ticket', '---', 1, :t, 1, :t, 1)"
            ),
            {"id": fid, "n": name, "ft": ftype, "t": T0},
        )
    _add_ticket(conn, TICKET_DATA, title="Data ticket", type_id=TYPE_ID, full=True)
    for fid, vt, vd in (
        (DF_MULTI, "a", None),
        (DF_MULTI, "b", None),
        (DF_TEXT, "hello", None),
        (DF_DATE, None, datetime(2026, 2, 1, 10, 30, 0)),
    ):
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text, value_date)"
                " VALUES (:f, :o, :vt, :vd)"
            ),
            {"f": fid, "o": TICKET_DATA, "vt": vt, "vd": vd},
        )
    _add_ticket(conn, TICKET_TYPE, title="Type ticket")
    _add_ticket(conn, TICKET_TAG, title="Postmaster")
    _add_ticket(conn, TICKET_INACTIVE, title="Inactive ticket", type_id=TYPE_ID)


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in set(_SEEDED_URLS):
        engine = create_engine(url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED_URLS.clear()
    clear_revision_cache()


@pytest.fixture
def sync_url(mariadb_znuny_url: str) -> str:
    _SEEDED_URLS.append(mariadb_znuny_url)
    clear_revision_cache()
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        for ddl in _TIQORA_DDL:
            conn.execute(text(ddl))
        _cleanup(conn)
        _seed(conn)
        _add_process(
            conn,
            1,
            _cond("      Type:\n        Match: Incident-887\n        Type: String\n"),
        )
        _add_process(
            conn,
            2,
            _cond("      Title:\n        Match: <OTRS_TICKET_Queue>\n        Type: Equal\n"),
        )
        _add_process(
            conn,
            3,
            _cond("      Type:\n        Match: Incident-887\n        Type: String\n"),
            state="S2",
        )
    engine.dispose()
    return mariadb_znuny_url


@pytest.fixture
async def factory(sync_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    eng: AsyncEngine = create_async_engine(
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    )
    yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    await eng.dispose()


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


async def _put_in_process(
    factory: async_sessionmaker[AsyncSession], ticket_id: int, n: int, activity: str
) -> None:
    """Place a ticket on an activity without the start-process side effects."""
    async with factory() as session, session.begin():
        for name, value in (
            ("ProcessManagementProcessID", _eid("Process", n)),
            ("ProcessManagementActivityID", activity),
        ):
            await session.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " SELECT id, :o, :v FROM dynamic_field WHERE name = :n"
                ),
                {"o": ticket_id, "v": value, "n": name},
            )


@pytest.mark.db
async def test_condition_data_has_znuny_ticketget_keys(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        data = await get_condition_data(session, TICKET_DATA)
    # Ticket.pm:1240-1290 / names 1362-1420
    assert data["TicketID"] == str(TICKET_DATA)
    assert data["Title"] == "Data ticket"
    assert data["Type"] == "Incident-887"
    assert data["TypeID"] == str(TYPE_ID)
    assert data["Service"] == "Service-887"
    assert data["ServiceID"] == str(SERVICE_ID)
    assert data["SLA"] == "Sla-887"
    assert data["SLAID"] == str(SLA_ID)
    assert data["Owner"] == "agent-887"
    assert data["OwnerID"] == str(AGENT)
    assert data["Responsible"] == "agent-887"
    assert data["Lock"] == "lock"
    assert data["LockID"] == "2"
    assert data["CustomerID"] == "CUST-887"
    assert data["CustomerUserID"] == "cora@example.org"
    assert data["State"] == "open"
    assert data["StateType"] == "open"
    assert data["Queue"] == "Postmaster"
    assert data["ArchiveFlag"] == "n"
    assert data["Created"] == "2026-03-04 05:06:07"
    # Dynamic fields: Multiselect -> list, other -> str, unset -> None, DateTime space-separated
    assert data["DynamicField_Multi887"] == ["a", "b"]
    assert data["DynamicField_Text887"] == "hello"
    assert data["DynamicField_Unset887"] is None
    assert data["DynamicField_Date887"] == "2026-02-01 10:30:00"

    async with factory() as session:
        plain = await get_condition_data(session, TICKET_TYPE)
        flat = await get_ticket_attrs(session, TICKET_DATA)
    # Znuny: ServiceID/SLAID are '' for tickets without them, TypeID falls back to 1
    assert plain["Service"] is None
    assert plain["ServiceID"] is None
    assert plain["Type"] == "Unclassified"
    # get_ticket_attrs is the flat view of the same data (list joined, unset empty)
    assert flat["DynamicField_Multi887"] == "a, b"
    assert flat["DynamicField_Unset887"] == ""
    assert flat["Type"] == "Incident-887"


@pytest.mark.db
async def test_transition_on_type_matches_now(factory: async_sessionmaker[AsyncSession]) -> None:
    """A ``Type: String`` condition silently never matched before (Type was not in the data)."""
    sysconfig = _sysconfig()
    await _put_in_process(factory, TICKET_TYPE, 1, _eid("Activity", 1, 1))
    async with factory() as session, session.begin():
        result = await submit_activity_dialog(
            session,
            ticket_id=TICKET_TYPE,
            activity_dialog_entity_id=_eid("ActivityDialog", 1),
            field_values={"Type": "Incident-887"},
            user_id=AGENT,
            sysconfig=sysconfig,
        )
    assert result.activity_changed is True
    assert result.new_activity_entity_id == _eid("Activity", 1, 2)
    assert result.transition_entity_id == _eid("T", 1)
    async with factory() as session:
        state = await get_ticket_process_state(session, TICKET_TYPE)
    assert state is not None
    assert state.activity_entity_id == _eid("Activity", 1, 2)


@pytest.mark.db
async def test_run_transitions_substitutes_tags_in_conditions(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """``Title Equal <OTRS_TICKET_Queue>`` (Base.pm MatchValueGet): title "Postmaster" = queue."""
    sysconfig = _sysconfig()
    await _put_in_process(factory, TICKET_TAG, 2, _eid("Activity", 2, 2))
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE ticket SET title = 'Other' WHERE id = :t"), {"t": TICKET_TAG}
        )
        miss = await run_transitions(
            session, ticket_id=TICKET_TAG, user_id=AGENT, sysconfig=sysconfig
        )
        assert miss.activity_changed is False
        assert miss.transition_entity_id is None
        await session.execute(
            text("UPDATE ticket SET title = 'postmaster' WHERE id = :t"), {"t": TICKET_TAG}
        )
        hit = await run_transitions(
            session, ticket_id=TICKET_TAG, user_id=AGENT, sysconfig=sysconfig
        )
    assert hit.activity_changed is True
    assert hit.transition_entity_id == _eid("T", 2)
    assert hit.new_activity_entity_id == _eid("Activity", 2, 3)


@pytest.mark.db
async def test_run_transitions_skips_inactive_process(
    factory: async_sessionmaker[AsyncSession], sync_url: str
) -> None:
    """Process.pm:395-402: Inactive runs no transitions; the same ticket fires once FadeAway."""
    sysconfig = _sysconfig()
    await _put_in_process(factory, TICKET_INACTIVE, 3, _eid("Activity", 3, 3))
    async with factory() as session, session.begin():
        skipped = await run_transitions(
            session, ticket_id=TICKET_INACTIVE, user_id=AGENT, sysconfig=sysconfig
        )
    assert skipped.activity_changed is False
    assert skipped.transition_entity_id is None
    assert skipped.unsupported_actions == []
    async with factory() as session:
        state = await get_ticket_process_state(session, TICKET_INACTIVE)
    assert state is not None
    assert state.activity_entity_id == _eid("Activity", 3, 3)

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE pm_process SET state_entity_id = 'S3' WHERE id = 887103"))
    engine.dispose()
    clear_revision_cache()
    async with factory() as session, session.begin():
        fired = await run_transitions(
            session, ticket_id=TICKET_INACTIVE, user_id=AGENT, sysconfig=sysconfig
        )
    assert fired.activity_changed is True
    assert fired.new_activity_entity_id == _eid("Activity", 3, 4)
