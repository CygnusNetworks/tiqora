"""Option refresh of process dialog forms (plan Task 6, D7).

Both dialects. Seed band 893000-893499: customer user 893001, agents 893010 (rw on both
groups), 893011 (rw on group 1), 893012 (rw on group 2), groups 893020/893021, queues
893030/893031, tickets 893040/893041, services 893050-893052, SLAs 893060-893062, dynamic
field 893070, ACL 893450+, processes 893100+, dialogs 893200+. Znuny references:
``AgentTicketProcess.pm`` ``_GetOwners``/``_GetServices``/``_GetSLAs`` and the ``AJAXUpdate``
re-render with ``%GetParam`` as ACL context.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.portal import process as portal_process
from tiqora.api.v1 import process as agent_process
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.process.actor import ProcessActor
from tiqora.process.dialog_form import build_start_form, build_ticket_dialog_form
from tiqora.process.engine import start_process
from tiqora.process.form_options import (
    ProcessFormOptionsIn,
    ProcessFormOptionsOut,
    refresh_start_options,
    refresh_ticket_dialog_options,
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.schemas import ProcessDialogFormOut
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)
CUST_ROW = 893001
CUST_LOGIN = "wf3-cu-893@example.org"
CUST_COMPANY = "wf3-co-893"
ACTOR, OWNER_G1, OWNER_G2 = 893010, 893011, 893012
GROUP_1, GROUP_2 = 893020, 893021
QUEUE_1, QUEUE_2 = 893030, 893031
Q1, Q2 = "Wf3Opt-Q1", "Wf3Opt-Q2"
TICKET_1, TICKET_CUSTOMER = 893040, 893041
SVC_A, SVC_B, SVC_C = 893050, 893051, 893052  # C: not assigned to the customer user
SLA_A, SLA_B, SLA_C = 893060, 893061, 893062
DF_AREA = 893070
STATE_CLOSED = "closed successful"
ACL_BASE = 893450

_CLEANUP = (
    "DELETE FROM acl WHERE id BETWEEN 893450 AND 893499",
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 893040 AND 893049",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 893040 AND 893049",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 893040 AND 893049",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 893040 AND 893049",
    "DELETE FROM ticket WHERE id BETWEEN 893040 AND 893049",
    "DELETE FROM dynamic_field WHERE id BETWEEN 893070 AND 893079",
    "DELETE FROM service_customer_user WHERE service_id BETWEEN 893050 AND 893059",
    "DELETE FROM service_sla WHERE service_id BETWEEN 893050 AND 893059",
    "DELETE FROM sla WHERE id BETWEEN 893060 AND 893069",
    "DELETE FROM service WHERE id BETWEEN 893050 AND 893059",
    "DELETE FROM queue WHERE id BETWEEN 893030 AND 893039",
    "DELETE FROM group_user WHERE group_id BETWEEN 893020 AND 893029",
    "DELETE FROM permission_groups WHERE id BETWEEN 893020 AND 893029",
    "DELETE FROM customer_user WHERE id BETWEEN 893001 AND 893009",
    "DELETE FROM users WHERE id BETWEEN 893010 AND 893019",
    "DELETE FROM pm_process WHERE id BETWEEN 893100 AND 893199",
    "DELETE FROM pm_activity_dialog WHERE id BETWEEN 893200 AND 893299",
    "DELETE FROM pm_activity WHERE id BETWEEN 893300 AND 893399",
    "DELETE FROM pm_transition WHERE id BETWEEN 893400 AND 893449",
)
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    engine.dispose()


def process_eid(n: int) -> str:
    return f"Process-{893100 + n}"


def dialog_eid(n: int) -> str:
    return f"ActivityDialog-{893200 + n}"


_PM_INSERT = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)
BOTH = ("AgentInterface", "CustomerInterface")


def _add_dialog(
    conn: Any, n: int, fields: dict[str, str], interfaces: tuple[str, ...] = BOTH
) -> None:
    order = "".join(f"- {name}\n" for name in fields)
    body = "".join(f"  {name}:\n    {yaml}\n" for name, yaml in fields.items())
    iface = "".join(f"- {i}\n" for i in interfaces)
    cfg = (
        f"DescriptionShort: Dialog {n}\nDescriptionLong: Long {n}\nFieldOrder:\n{order}"
        f"Fields:\n{body}Interface:\n{iface}Permission: ''\n"
        "SubmitAdviceText: Advice\nSubmitButtonText: Go\n"
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity_dialog")),
        {"id": 893200 + n, "eid": dialog_eid(n), "name": f"Dialog {n}", "cfg": cfg},
    )


def _add_process(conn: Any, n: int, *, dialogs: list[int], state: str = "S1") -> None:
    """Process *n*: start dialog = the first of *dialogs*, which are all on the start activity."""
    act_a, act_b, trans = (
        f"Activity-{893300 + 2 * n}",
        f"Activity-{893301 + 2 * n}",
        f"T-{893400 + n}",
    )
    dialog_cfg = "".join(f"  '{i + 1}': {dialog_eid(d)}\n" for i, d in enumerate(dialogs))
    conn.execute(
        text(
            "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :eid, :name, :st,"
            " '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
        ),
        {
            "id": 893100 + n,
            "eid": process_eid(n),
            "name": f"Process {n}",
            "st": state,
            "cfg": (
                f"StartActivity: {act_a}\nStartActivityDialog: {dialog_eid(dialogs[0])}\n"
                f"Path:\n  {act_a}:\n    {trans}:\n      ActivityEntityID: {act_b}\n"
                f"  {act_b}: {{}}\n"
            ),
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {
            "id": 893300 + 2 * n,
            "eid": act_a,
            "name": f"A{n}",
            "cfg": f"ActivityDialog:\n{dialog_cfg}",
        },
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_activity")),
        {"id": 893301 + 2 * n, "eid": act_b, "name": f"B{n}", "cfg": "ActivityDialog: {}\n"},
    )
    conn.execute(
        text(_PM_INSERT.format(table="pm_transition")),
        {"id": 893400 + n, "eid": trans, "name": f"T{n}", "cfg": "ConditionLinking: and\n"},
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, login, first in (
            (ACTOR, "wf3.opt.actor", "Anna"),
            (OWNER_G1, "wf3.opt.g1", "Bruno"),
            (OWNER_G2, "wf3.opt.g2", "Carla"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', :f, 'Opt', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": login, "f": first, "t": T0},
            )
        for gid in (GROUP_1, GROUP_2):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"wf3-opt-{gid}", "t": T0},
            )
        for uid, gid in (
            (ACTOR, GROUP_1),
            (ACTOR, GROUP_2),
            (OWNER_G1, GROUP_1),
            (OWNER_G2, GROUP_2),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "t": T0},
            )
        for qid, name, gid in ((QUEUE_1, Q1, GROUP_1), (QUEUE_2, Q2, GROUP_2)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "g": gid, "t": T0},
            )
        for sid, name in (
            (SVC_A, "Wf3Opt-Svc-A"),
            (SVC_B, "Wf3Opt-Svc-B"),
            (SVC_C, "Wf3Opt-Svc-C"),
        ):
            conn.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": name, "t": T0},
            )
        for lid, name in (
            (SLA_A, "Wf3Opt-Sla-A"),
            (SLA_B, "Wf3Opt-Sla-B"),
            (SLA_C, "Wf3Opt-Sla-C"),
        ):
            conn.execute(
                text(
                    "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                    " valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 60, 60, 60, 1, :t, 1, :t, 1)"
                ),
                {"id": lid, "n": name, "t": T0},
            )
        for sid, lid in ((SVC_A, SLA_A), (SVC_B, SLA_B), (SVC_C, SLA_C)):
            conn.execute(
                text("INSERT INTO service_sla (service_id, sla_id) VALUES (:s, :l)"),
                {"s": sid, "l": lid},
            )
        for sid in (SVC_A, SVC_B):
            conn.execute(
                text(
                    "INSERT INTO service_customer_user (customer_user_login, service_id,"
                    " create_time, create_by) VALUES (:c, :s, :t, 1)"
                ),
                {"c": CUST_LOGIN, "s": sid, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :login, :cust, 'Cora', 'Opt', 1, :t, 1, :t, 1)"
            ),
            {"id": CUST_ROW, "login": CUST_LOGIN, "cust": CUST_COMPANY, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 0, 'wf3Area', 'Area', 1, 'Dropdown',"
                " 'Ticket', :cfg, 1, :t, 1, :t, 1)"
            ),
            {
                "id": DF_AREA,
                "cfg": "---\nPossibleValues:\n  North: North\n  South: South\n",
                "t": T0,
            },
        )
        for tid, user, customer in ((TICKET_1, ACTOR, None), (TICKET_CUSTOMER, ACTOR, CUST_LOGIN)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'wf3 options', :q, 1, 1, :u, :u, 3, 4, :cc, :cu,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2026110{tid}",
                    "q": QUEUE_1,
                    "u": user,
                    "cc": CUST_COMPANY if customer else None,
                    "cu": customer,
                    "t": T0,
                },
            )
    engine.dispose()


def _add_acl(sync_url: str, n: int, name: str, match_ticket: str, change: str) -> None:
    engine = create_engine(sync_url)
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
                "m": "Properties:\n  Frontend:\n    Action: [AgentTicketProcess]\n"
                f"  Ticket:\n{match_ticket}",
                "c": change,
                "t": T0,
            },
        )
    engine.dispose()
    clear_revision_cache()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run(url, _CLEANUP)
    _URLS.clear()
    clear_revision_cache()


AGENT = AuthenticatedUser(
    id=ACTOR, login="wf3.opt.actor", first_name="Anna", last_name="Opt", auth_method="session"
)
CUSTOMER = AuthenticatedCustomer(
    id=CUST_ROW,
    login=CUST_LOGIN,
    email=CUST_LOGIN,
    customer_id=CUST_COMPANY,
    first_name="Cora",
    last_name="Opt",
)

# Queue, Owner, Service, SLA, State, Priority and a dropdown dynamic field, all editable.
FULL_DIALOG: dict[str, str] = {
    "Queue": "Display: '2'",
    "Owner": "Display: '1'",
    "Service": "Display: '1'",
    "SLA": "Display: '1'",
    "State": "Display: '1'",
    "Priority": "Display: '1'",
    "DynamicField_wf3Area": "Display: '1'",
}
CUSTOMER_DIALOG: dict[str, str] = {
    "Service": "Display: '1'",
    "SLA": "Display: '1'",
    "State": "Display: '1'",
}


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str

    def build(self, *, dialogs: dict[int, dict[str, str]], state: str = "S1") -> None:
        """Process 1 whose activity offers *dialogs* (the lowest number is the start dialog)."""
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            for n, fields in dialogs.items():
                _add_dialog(conn, n, fields)
            _add_process(conn, 1, dialogs=sorted(dialogs), state=state)
        engine.dispose()
        clear_revision_cache()

    async def enroll(self, ticket_id: int = TICKET_1) -> None:
        async with self.factory() as session, session.begin():
            await start_process(
                session,
                ticket_id=ticket_id,
                process_entity_id=process_eid(1),
                user_id=ACTOR,
                sysconfig=SysConfig(session),
            )

    async def start_options(
        self,
        values: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        *,
        actor: ProcessActor | None = None,
        user: AuthenticatedUser | None = AGENT,
    ) -> dict[str, list[tuple[str, str]]]:
        async with self.factory() as session:
            out = await refresh_start_options(
                session,
                actor=actor or ProcessActor.agent(ACTOR),
                user=user,
                process_entity_id=process_eid(1),
                ticket_id=None,
                body=ProcessFormOptionsIn(values=values or {}, fields=fields or []),
                repository=ProcessRepository(session),
            )
        return _flat(out)

    async def ticket_options(
        self,
        values: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        *,
        dialog: int = 1,
    ) -> dict[str, list[tuple[str, str]]]:
        async with self.factory() as session:
            out = await refresh_ticket_dialog_options(
                session,
                actor=ProcessActor.agent(ACTOR),
                user=AGENT,
                ticket_id=TICKET_1,
                activity_dialog_entity_id=dialog_eid(dialog),
                body=ProcessFormOptionsIn(values=values or {}, fields=fields or []),
                repository=ProcessRepository(session),
            )
        return _flat(out)

    async def start_form(self) -> ProcessDialogFormOut:
        async with self.factory() as session:
            return await build_start_form(
                session,
                actor=ProcessActor.agent(ACTOR),
                user=AGENT,
                process_entity_id=process_eid(1),
                ticket_id=None,
                repository=ProcessRepository(session),
            )

    async def ticket_form(self, dialog: int = 1) -> ProcessDialogFormOut:
        async with self.factory() as session:
            return await build_ticket_dialog_form(
                session,
                actor=ProcessActor.agent(ACTOR),
                user=AGENT,
                ticket_id=TICKET_1,
                activity_dialog_entity_id=dialog_eid(dialog),
                repository=ProcessRepository(session),
            )


def _flat(out: ProcessFormOptionsOut) -> dict[str, list[tuple[str, str]]]:
    return {name: [(o.value, o.label) for o in opts] for name, opts in out.options.items()}


def _values(form: ProcessDialogFormOut, name: str) -> list[str]:
    return [o.value for f in form.fields if f.name == name for o in f.options]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory, sync_url)
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


# ---------------------------------------------------------------------------
# Dependent lists
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_owner_options_follow_selected_queue(env: Env) -> None:
    env.build(dialogs={1: FULL_DIALOG})
    only_owner = ["Owner"]
    q1 = await env.start_options({"Queue": str(QUEUE_1)}, only_owner)
    q2 = await env.start_options({"Queue": str(QUEUE_2)}, only_owner)
    # Owner/Responsible choices are the agents with rw on the queue's group (_GetOwners).
    assert sorted(v for v, _ in q1["Owner"]) == sorted([str(ACTOR), str(OWNER_G1)])
    assert sorted(v for v, _ in q2["Owner"]) == sorted([str(ACTOR), str(OWNER_G2)])
    assert dict(q1["Owner"])[str(OWNER_G1)] == "Bruno Opt"  # labels are the full names
    assert set(q1) == {"Owner"}  # only what was asked for

    # The form tells the browser which fields to watch.
    form = await env.start_form()
    refresh_on = {f.name: f.refresh_on for f in form.fields}
    assert refresh_on["Owner"] == ["Queue"]
    assert refresh_on["SLA"] == ["Service"]
    assert refresh_on["Service"] == []  # no CustomerUserID in this dialog
    assert refresh_on["State"] == [
        "Queue", "Owner", "Service", "SLA", "Priority", "DynamicField_wf3Area"
    ]  # fmt: skip
    assert refresh_on["Queue"][0] == "Owner"


@pytest.mark.db
async def test_sla_options_follow_selected_service(env: Env) -> None:
    env.build(dialogs={1: FULL_DIALOG})
    both = await env.start_options({}, ["SLA"])
    assert [v for v, _ in both["SLA"]] == [str(SLA_A), str(SLA_B), str(SLA_C)]  # form parity
    only_a = await env.start_options({"Service": str(SVC_A)}, ["SLA"])
    assert only_a["SLA"] == [(str(SLA_A), "Wf3Opt-Sla-A")]
    only_b = await env.start_options({"Service": SVC_B}, ["SLA"])  # numbers work as well
    assert only_b["SLA"] == [(str(SLA_B), "Wf3Opt-Sla-B")]
    # A service without any SLA offers none.
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM service_sla WHERE service_id = :s"), {"s": SVC_C})
    engine.dispose()
    assert (await env.start_options({"Service": str(SVC_C)}, ["SLA"]))["SLA"] == []


@pytest.mark.db
async def test_acl_on_dynamic_field_value_hides_state(env: Env) -> None:
    env.build(dialogs={1: FULL_DIALOG})
    _add_acl(
        env.sync_url,
        1,
        "north-no-closed",
        "    DynamicField_wf3Area: [North]\n",
        f"PossibleNot:\n  Ticket:\n    State: ['{STATE_CLOSED}']\n",
    )
    names = lambda out: [label for _, label in out["State"]]  # noqa: E731
    north = await env.start_options({"DynamicField_wf3Area": "North"}, ["State"])
    south = await env.start_options({"DynamicField_wf3Area": "South"}, ["State"])
    nothing = await env.start_options({}, ["State"])
    assert STATE_CLOSED not in names(north)
    assert "open" in names(north)
    assert STATE_CLOSED in names(south)
    assert STATE_CLOSED in names(nothing)  # without the value the ACL does not match
    # A multi value (the Multiselect shape) and the ticket dialog see the value too.
    listed = await env.start_options({"DynamicField_wf3Area": ["North"]}, ["State"])
    assert STATE_CLOSED not in names(listed)
    await env.enroll()
    on_ticket = await env.ticket_options({"DynamicField_wf3Area": "North"}, ["State"])
    assert STATE_CLOSED not in names(on_ticket)


@pytest.mark.db
async def test_refresh_never_returns_more_than_the_form(env: Env) -> None:
    env.build(dialogs={1: FULL_DIALOG, 2: {k: v for k, v in FULL_DIALOG.items() if k != "Queue"}})
    _add_acl(
        env.sync_url,
        1,
        "north-no-closed",
        "    DynamicField_wf3Area: [North]\n",
        f"PossibleNot:\n  Ticket:\n    State: ['{STATE_CLOSED}']\n",
    )
    form = await env.start_form()
    unfiltered = {f.name: {o.value for o in f.options} for f in form.fields}
    for values in (
        {},
        {"Service": str(SVC_A)},
        {"Service": str(SVC_C), "DynamicField_wf3Area": "North"},
        {"Queue": str(QUEUE_2), "State": "4", "Priority": "3"},
        {"Queue": "999999", "Service": "not-a-number"},  # junk and unknown queues are ignored
    ):
        got = await env.start_options(values)
        assert set(got) == {f.name for f in form.fields}, values
        for name, options in got.items():
            if name == "Owner":
                continue  # follows the selected queue, see test_owner_options_follow_selected_queue
            assert {v for v, _ in options} <= unfiltered[name], (name, values)
    assert unfiltered["DynamicField_wf3Area"] == {"North", "South"}

    # The ticket dialog has no Queue field: the pickers stay on the ticket's queue.
    await env.enroll()
    ticket_form = await env.ticket_form(2)
    ticket_unfiltered = {f.name: {o.value for o in f.options} for f in ticket_form.fields}
    got = await env.ticket_options({"Service": str(SVC_B)}, dialog=2)
    for name, options in got.items():
        assert {v for v, _ in options} <= ticket_unfiltered[name], name
    assert ticket_unfiltered["Owner"] == {str(ACTOR), str(OWNER_G1)}
    assert {v for v, _ in got["Owner"]} == {str(ACTOR), str(OWNER_G1)}
    assert [v for v, _ in got["SLA"]] == [str(SLA_B)]


@pytest.mark.db
async def test_refresh_on_lists(env: Env) -> None:
    customer_dialog = {
        "Queue": "Display: '2'",
        "Owner": "Display: '1'",
        "ResponsibleID": "Display: '1'",
        "CustomerUserID": "Display: '2'",
        "Service": "Display: '1'",
        "SLA": "Display: '1'",
        "State": "Display: '1'",
        "Title": "Display: '1'",
        "Lock": "Display: '1'",
        "DynamicField_wf3Area": "Display: '1'",
    }
    env.build(dialogs={1: customer_dialog})
    form = await env.start_form()
    refresh_on = {f.name: f.refresh_on for f in form.fields}
    assert refresh_on["Owner"] == ["Queue"]
    assert refresh_on["Responsible"] == ["Queue"]
    assert refresh_on["SLA"] == ["Service"]
    assert refresh_on["Service"] == ["CustomerUserID"]
    everything_else = ["Queue", "Owner", "Responsible", "CustomerUserID", "Service", "SLA", "State"]
    assert refresh_on["State"] == [n for n in everything_else if n != "State"] + [
        "DynamicField_wf3Area"
    ]
    assert refresh_on["DynamicField_wf3Area"] == everything_else
    assert refresh_on["Queue"] == [n for n in everything_else if n != "Queue"] + [
        "DynamicField_wf3Area"
    ]
    # Text, Lock and the customer field have no server-built options to refresh.
    assert (
        refresh_on["Title"] == []
        and refresh_on["Lock"] == []
        and refresh_on["CustomerUserID"] == []
    )

    # Service follows the picked customer user (TicketServiceList with CustomerUserID).
    services = await env.start_options({"CustomerUserID": CUST_LOGIN}, ["Service"])
    assert [v for v, _ in services["Service"]] == [str(SVC_A), str(SVC_B)]
    assert SVC_C not in {int(v) for v, _ in services["Service"]}
    # Lock and unknown names are not option fields; an empty fields list means all of them.
    asked = await env.start_options({}, ["Lock", "Title", "Nope", "State"])
    assert set(asked) == {"State"}
    assert set(await env.start_options({})) == {
        "Queue", "Owner", "Responsible", "Service", "SLA", "State", "DynamicField_wf3Area",
    }  # fmt: skip


# ---------------------------------------------------------------------------
# Principal and availability
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_portal_refresh_uses_customer_principal(env: Env) -> None:
    env.build(dialogs={1: CUSTOMER_DIALOG})
    async with env.factory() as session:
        # An agent sees every service ...
        agent = await agent_process.refresh_start_form_options(
            process_eid(1), ProcessFormOptionsIn(), AGENT, session
        )
    assert {SVC_A, SVC_B, SVC_C} <= {int(o.value) for o in agent.options["Service"]}
    async with env.factory() as session:
        # ... the customer only the services assigned to the customer user.
        everything = await portal_process.refresh_start_form_options(
            process_eid(1), ProcessFormOptionsIn(), CUSTOMER, session
        )
        picked = await portal_process.refresh_start_form_options(
            process_eid(1),
            ProcessFormOptionsIn(values={"Service": str(SVC_B)}, fields=["SLA"]),
            CUSTOMER,
            session,
        )
        foreign = await portal_process.refresh_start_form_options(
            process_eid(1),
            ProcessFormOptionsIn(values={"Service": str(SVC_C)}, fields=["SLA"]),
            CUSTOMER,
            session,
        )
    ours = {int(o.value) for o in everything.options["Service"]}
    assert ours & {SVC_A, SVC_B, SVC_C} == {SVC_A, SVC_B}
    assert {int(o.value) for o in everything.options["SLA"]} & {SLA_A, SLA_B, SLA_C} == {
        SLA_A,
        SLA_B,
    }
    assert [o.value for o in picked.options["SLA"]] == [str(SLA_B)]
    assert foreign.options["SLA"] == []  # a service the customer does not hold offers nothing

    # The customer interface has no Owner field; asking for one returns nothing for it.
    assert "Owner" not in everything.options

    # ACLs of the Customer frontend see the entered values: the agent ACL does not apply.
    _add_acl(
        env.sync_url,
        1,
        "north-no-closed",
        "    DynamicField_wf3Area: [North]\n",
        f"PossibleNot:\n  Ticket:\n    State: ['{STATE_CLOSED}']\n",
    )
    async with env.factory() as session:
        states = await portal_process.refresh_start_form_options(
            process_eid(1),
            ProcessFormOptionsIn(values={"DynamicField_wf3Area": "North"}, fields=["State"]),
            CUSTOMER,
            session,
        )
    assert "State" in states.options  # customer ACL context path ran (agent ACL: other module)

    # The ticket dialog route of the portal checks ownership of the ticket.
    await env.enroll(TICKET_CUSTOMER)
    async with env.factory() as session:
        own = await portal_process.refresh_ticket_dialog_form_options(
            TICKET_CUSTOMER,
            dialog_eid(1),
            ProcessFormOptionsIn(values={"Service": str(SVC_A)}, fields=["SLA"]),
            CUSTOMER,
            session,
        )
    assert [o.value for o in own.options["SLA"]] == [str(SLA_A)]
    await env.enroll(TICKET_1)
    async with env.factory() as session:
        with pytest.raises(HTTPException) as exc:
            await portal_process.refresh_ticket_dialog_form_options(
                TICKET_1, dialog_eid(1), ProcessFormOptionsIn(), CUSTOMER, session
            )
    assert exc.value.status_code in (403, 404)


@pytest.mark.db
async def test_refresh_checks_availability_like_the_form(env: Env) -> None:
    # Inactive process: the form is 409, the refresh as well.
    env.build(dialogs={1: FULL_DIALOG, 2: FULL_DIALOG}, state="S2")
    async with env.factory() as session:
        with pytest.raises(HTTPException) as inactive:
            await agent_process.refresh_start_form_options(
                process_eid(1), ProcessFormOptionsIn(), AGENT, session
            )
    assert inactive.value.status_code == 409

    # Hidden dialog (customer-only on the agent interface): 403 like GET .../form.
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE pm_process SET state_entity_id = 'S1' WHERE id = 893101"))
        conn.execute(
            text("UPDATE pm_activity_dialog SET config = :c WHERE id = 893202"),
            {
                "c": "FieldOrder:\n- State\nFields:\n  State:\n    Display: '1'\n"
                "Interface:\n- CustomerInterface\nPermission: ''\n"
            },
        )
    engine.dispose()
    clear_revision_cache()
    await env.enroll()
    async with env.factory() as session:
        shown = await agent_process.refresh_ticket_dialog_form_options(
            TICKET_1, dialog_eid(1), ProcessFormOptionsIn(fields=["State"]), AGENT, session
        )
        assert "State" in shown.options
        with pytest.raises(HTTPException) as hidden:
            await agent_process.refresh_ticket_dialog_form_options(
                TICKET_1, dialog_eid(2), ProcessFormOptionsIn(), AGENT, session
            )
        with pytest.raises(HTTPException) as form_hidden:
            await agent_process.get_ticket_dialog_form(TICKET_1, dialog_eid(2), AGENT, session)
        with pytest.raises(HTTPException) as unknown:
            await agent_process.refresh_ticket_dialog_form_options(
                TICKET_1, dialog_eid(9), ProcessFormOptionsIn(), AGENT, session
            )
        with pytest.raises(HTTPException) as no_ticket:
            await agent_process.refresh_ticket_dialog_form_options(
                TICKET_CUSTOMER, dialog_eid(1), ProcessFormOptionsIn(), AGENT, session
            )
    assert hidden.value.status_code == form_hidden.value.status_code == 403
    assert unknown.value.status_code == 409  # not a dialog of the current activity
    assert no_ticket.value.status_code == 409  # that ticket is not in a process
