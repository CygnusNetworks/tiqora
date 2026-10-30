"""Report source ``ci_tickets`` on the shared CMDB world (both dialects, band 972300-972319).

Classes ``ist972srv`` (GROUP_RW), ``ist972net`` (GROUP_RO), ``ist972sec`` (GROUP_HIDDEN). User A
reads the queue QUEUE_RW and the classes srv and net, but not sec nor QUEUE_HIDDEN; user B reads
class net only and no queue at all. ``_cmdb_rows_reset`` empties the CMDB rows (links included)
before every test, so classes, CIs and links come from a function-scoped fixture (F8); the
tickets, their facts, the change record and the type settings are module scoped.

Own tickets in QUEUE_RW: 972300 incident (open), 972301 incident (closed), 972302 change
(open, with a ``tiqora_itsm_change`` row), 972303 request of the default type (open).

Links as ``LinkService.add`` stores them: a pointed type (``DependsOn``) keeps the orientation
it was added with (CI to ticket or ticket to CI); an unpointed type (``RelevantTo``) always has
the CI as source. Keys are the decimal ids as strings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.dialects import mysql, postgresql

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    GROUP_RW,
    QUEUE_RW,
    TICKET_CLOSED,
    TICKET_HIDDEN,
    TICKET_OPEN,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.gate import reset_mode_cache
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.service import system_access
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.plugins.report_points import source_tables
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS
from tiqora.reporting.schemas import ReportResultOut
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sources.ci_tickets import ci_tickets_source
from tiqora.reporting.sql import digits_only

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

LO, HI = 972300, 972319
INC_OPEN, INC_CLOSED, CHG, REQ = 972300, 972301, 972302, 972303
OWN_TICKETS = (INC_OPEN, INC_CLOSED, CHG, REQ)
WORLD_TICKETS = (TICKET_OPEN, TICKET_HIDDEN, TICKET_CLOSED)
BUILT = datetime(2026, 10, 1, 12, 0)
#: the default window (last 30 days on ``created``) must contain the tickets' creation
NOW = datetime(2026, 10, 2, 12, 0)
STAMP = datetime(2026, 9, 20, 8, 0)
DB, WEB, CORE, SEC = (
    "srv-db-01.example.org",
    "srv-web-01.example.org",
    "sw-core-01.example.org",
    "vault-01.example.org",
)
SETTINGS = {
    "itsm.incident_type": "ist972 Incident",
    "itsm.change_type": "ist972 Change",
    # configured but absent from ticket_type: no other type gets a role
    "itsm.major_incident_type": "ist972 no major",
    "itsm.service_request_type": "ist972 no request",
    "itsm.problem_type": "ist972 no problem",
    "itsm.change_task_type": "ist972 no task",
}
_TICKET_SQL = (
    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
    " escalation_update_time, escalation_response_time,"
    " escalation_solution_time, archive_flag,"
    " create_time, create_by, change_time, change_by)"
    " VALUES (:id, :tn, 'ist972 ci ticket', :q, 1, :ty, 1, 1, 3, :s,"
    " NULL, NULL, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
)
_CHANGE_SQL = (
    "INSERT INTO tiqora_itsm_change (ticket_id, change_type, phase, outcome,"
    " approval_round, impact_truncated, portal_visible,"
    " created_at, created_by, updated_at, updated_by)"
    " VALUES (:id, 'normal', 'implementing', NULL, 0, :f, :f, :t, 1, :t, 1)"
)
_CLEANUP = (
    "DELETE FROM tiqora_itsm_change WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id IN :ids",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id IN :ids",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id IN :ids",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_type WHERE name LIKE 'ist972 %'",
)


@dataclass
class Tickets:
    db: CmdbWorld
    incident_type: int
    change_type: int


def _sql_in(world: CmdbWorld, sql: str, **params: Any) -> None:
    """``world.sql`` with the ``IN :ids`` marker expanded to a literal list of ints."""
    ids = ", ".join(str(i) for i in (*OWN_TICKETS, *WORLD_TICKETS))
    world.sql(sql.replace("IN :ids", f"IN ({ids})"), **params)


def _clean(world: CmdbWorld) -> None:
    for stmt in _CLEANUP:
        _sql_in(world, stmt, lo=LO, hi=HI)


async def _seed_async(world: CmdbWorld) -> None:
    async with world.factory() as session:
        for key, value in SETTINGS.items():
            session.add(TiqoraSettings(key=key, value=value))
        await session.commit()
    async with world.factory() as session:
        await rebuild_ticket_facts(session, [*OWN_TICKETS, *WORLD_TICKETS], now=BUILT)
        await session.commit()


async def _unseed_async(world: CmdbWorld) -> None:
    async with world.factory() as session:
        await session.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(list(SETTINGS))))
        await session.commit()


@pytest.fixture(scope="module")
def tickets(cmdb_db: CmdbWorld) -> Iterator[Tickets]:
    _clean(cmdb_db)
    for name in ("ist972 Incident", "ist972 Change"):
        cmdb_db.sql(
            "INSERT INTO ticket_type (name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:n, 1, :t, 1, :t, 1)",
            n=name,
            t=STAMP,
        )
    type_ids = {
        name: int(cmdb_db.sql("SELECT id FROM ticket_type WHERE name = :n", n=name)[0][0])
        for name in ("ist972 Incident", "ist972 Change")
    }
    open_state = int(cmdb_db.sql("SELECT id FROM ticket_state WHERE name = 'open'")[0][0])
    closed_state = int(
        cmdb_db.sql("SELECT id FROM ticket_state WHERE name = 'closed successful'")[0][0]
    )
    for tid, type_id, state in (
        (INC_OPEN, type_ids["ist972 Incident"], open_state),
        (INC_CLOSED, type_ids["ist972 Incident"], closed_state),
        (CHG, type_ids["ist972 Change"], open_state),
        (REQ, 1, open_state),
    ):
        cmdb_db.sql(
            _TICKET_SQL, id=tid, tn=f"20260920{tid}", q=QUEUE_RW, ty=type_id, s=state, t=STAMP
        )
    cmdb_db.sql(_CHANGE_SQL, id=CHG, f=False, t=STAMP)
    asyncio.run(_seed_async(cmdb_db))
    yield Tickets(cmdb_db, type_ids["ist972 Incident"], type_ids["ist972 Change"])
    asyncio.run(_unseed_async(cmdb_db))
    _clean(cmdb_db)


@dataclass
class World:
    db: CmdbWorld
    ci: dict[str, int]
    classes: dict[str, int]


@pytest.fixture
async def world(cmdb_db: CmdbWorld, tickets: Tickets) -> Any:
    set_mode(cmdb_db, "tiqora")
    reset_mode_cache()
    async with cmdb_db.factory() as session:
        srv = await make_class(session, key="ist972srv", group_id=GROUP_RW)
        net = await make_class(session, key="ist972net", group_id=GROUP_RO)
        sec = await make_class(session, key="ist972sec", group_id=GROUP_HIDDEN)
        ids = {
            DB: await make_ci(session, srv, DB, deployment="production", incident="operational"),
            WEB: await make_ci(session, srv, WEB, deployment="production", incident="incident"),
            CORE: await make_ci(session, net, CORE, deployment="planned", incident="operational"),
            SEC: await make_ci(session, sec, SEC, deployment="production", incident="operational"),
        }
    w = World(cmdb_db, ids, {"srv": srv, "net": net, "sec": sec})
    # (source kind, source key, target kind, target key, link type)
    for sk, sv, tk, tv, ltype in (
        ("ci", ids[DB], "ticket", INC_OPEN, "DependsOn"),
        ("ticket", CHG, "ci", ids[DB], "DependsOn"),
        ("ci", ids[DB], "ticket", INC_CLOSED, "RelevantTo"),
        ("ci", ids[WEB], "ticket", INC_OPEN, "RelevantTo"),
        ("ci", ids[CORE], "ticket", REQ, "RelevantTo"),
        # a CI of a class A may not read, linked to a ticket A may read
        ("ci", ids[SEC], "ticket", INC_OPEN, "RelevantTo"),
        # a readable CI linked to a ticket in a queue A may not read
        ("ci", ids[DB], "ticket", TICKET_HIDDEN, "RelevantTo"),
    ):
        _link(w, sk, sv, tk, tv, ltype)
    yield w
    reset_mode_cache()


def _link(w: World, sk: str, sv: Any, tk: str, tv: Any, ltype: str) -> None:
    w.db.sql(
        "INSERT INTO tiqora_itsm_link (source_kind, source_key, target_kind, target_key,"
        " link_type, created_by, created_at) VALUES (:sk, :sv, :tk, :tv, :lt, :u, :t)",
        sk=sk,
        sv=str(sv),
        tk=tk,
        tv=str(tv),
        lt=ltype,
        u=USER_ADMIN,
        t=STAMP,
    )


async def _run(
    world: World,
    user: int,
    *,
    dims: tuple[str, ...] = (),
    measures: tuple[str, ...] = ("tickets",),
    filter_: dict[str, Any] | None = None,
) -> ReportResultOut:
    payload: dict[str, Any] = {
        "source": "ci_tickets",
        "dimensions": [{"key": k} for k in dims],
        "measures": [{"key": k} for k in measures],
    }
    if filter_ is not None:
        payload["filter"] = {"version": 1, "where": filter_}
    async with world.db.factory() as session:
        return await run_report(
            session,
            defn=ReportDefinition.model_validate(payload),
            params=RunParams(time_zone="UTC"),
            user_id=user,
            report_key=None,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


def _by_id(res: ReportResultOut) -> dict[int, list[Any]]:
    return {int(row[0]): row[1:] for row in res.rows}


def _labelled(res: ReportResultOut, ndims: int) -> dict[tuple[str, ...], list[Any]]:
    keys = [c.key for c in res.columns[:ndims]]
    return {
        tuple(res.labels[k][str(v)] for k, v in zip(keys, row[:ndims], strict=True)): row[ndims:]
        for row in res.rows
    }


async def test_links_both_directions(world: World) -> None:
    res = await _run(world, USER_A, dims=("ci",))
    # DB: INC_OPEN (ci->ticket), CHG (ticket->ci), INC_CLOSED; the hidden ticket is not counted
    assert _by_id(res) == {world.ci[DB]: [3], world.ci[WEB]: [1], world.ci[CORE]: [1]}


async def test_rows_written_by_link_service_are_counted(world: World) -> None:
    """The real writer: an unpointed type puts the CI first, a pointed one keeps the direction."""
    async with world.db.factory() as session:
        svc = LinkService(session)
        await svc.add(
            system_access(USER_ADMIN),
            source=LinkEnd("ticket", str(TICKET_OPEN)),
            target=LinkEnd("ci", str(world.ci[DB])),
            link_type_key="RelevantTo",
            enforce_permissions=False,
        )
        await svc.add(
            system_access(USER_ADMIN),
            source=LinkEnd("ticket", str(TICKET_CLOSED)),
            target=LinkEnd("ci", str(world.ci[CORE])),
            link_type_key="DependsOn",
            enforce_permissions=False,
        )
        await session.commit()
    shapes = world.db.sql(
        "SELECT source_kind, source_key, target_kind, target_key, link_type FROM tiqora_itsm_link"
        " WHERE target_key IN (:a, :b) OR source_key IN (:a, :b)",
        a=str(TICKET_OPEN),
        b=str(TICKET_CLOSED),
    )
    assert sorted(tuple(r) for r in shapes) == sorted(
        [
            ("ci", str(world.ci[DB]), "ticket", str(TICKET_OPEN), "RelevantTo"),
            ("ticket", str(TICKET_CLOSED), "ci", str(world.ci[CORE]), "DependsOn"),
        ]
    )
    res = await _run(world, USER_A, dims=("ci",))
    assert _by_id(res) == {world.ci[DB]: [4], world.ci[WEB]: [1], world.ci[CORE]: [2]}


async def test_pair_linked_twice_counts_once(world: World) -> None:
    _link(world, "ci", world.ci[DB], "ticket", INC_OPEN, "RelevantTo")  # DependsOn exists already
    by_ci = await _run(world, USER_A, dims=("ci",))
    assert _by_id(by_ci)[world.ci[DB]] == [3]
    res = await _run(
        world,
        USER_A,
        dims=("link_type",),
        measures=("tickets", "cis"),
        filter_={"field": "ci", "op": "in", "value": [world.ci[DB]]},
    )
    # per link type the pair is one row each: DependsOn INC_OPEN + CHG, RelevantTo INC_OPEN +
    # INC_CLOSED; the report as a whole still has three tickets
    assert _by_key(res) == {"DependsOn": [2, 1], "RelevantTo": [2, 1]}
    total = await _run(world, USER_A, filter_={"field": "ci", "op": "in", "value": [world.ci[DB]]})
    assert total.rows == [[3]]


async def test_double_scope_queue_and_class(world: World) -> None:
    res = await _run(world, USER_A, dims=("ci",), measures=("tickets", "cis"))
    assert world.ci[SEC] not in _by_id(res)  # unreadable class, readable ticket
    assert _by_id(res)[world.ci[DB]] == [3, 1]  # the link to TICKET_HIDDEN is not counted
    assert SEC not in {v for col in res.labels.values() for v in col.values()}
    by_ticket = await _run(world, USER_A, dims=("ticket_type",), measures=("tickets",))
    assert sum(r[1] for r in by_ticket.rows) == 4  # INC_OPEN, INC_CLOSED, CHG, REQ
    # filtering on the hidden pieces reveals nothing
    for flt in (
        {"field": "ci", "op": "in", "value": [world.ci[SEC]]},
        {"field": "ci_class", "op": "in", "value": [world.classes["sec"]]},
    ):
        hidden = await _run(world, USER_A, dims=("ci",), filter_=flt)
        assert hidden.rows == []
    # the viewer with the class but without a queue sees nothing; empty queue ids must not break
    nobody = await _run(world, USER_B, dims=("ci",), measures=("tickets", "cis"))
    assert nobody.rows == []
    assert (await _run(world, USER_B, dims=("queue",))).rows == []


async def test_non_numeric_link_keys_are_ignored(world: World) -> None:
    ci = world.ci[DB]
    _link(world, "ci", ci, "customer_user", "cmdb4-cust-a@example.org", "RelevantTo")
    _link(world, "ci", ci, "customer_company", "cmdb4-co-a", "RelevantTo")
    _link(world, "ci", "x1", "ticket", INC_OPEN, "RelevantTo")  # ci kind, key not a number
    _link(world, "ci", ci, "ticket", "12345678901234567890", "RelevantTo")  # 20 digits
    _link(world, "ticket", "12345678901234567890", "ci", ci, "RelevantTo")
    _link(world, "ticket", "7e3", "ci", ci, "RelevantTo")  # looks numeric to a lenient cast
    res = await _run(world, USER_A, dims=("ci",))
    assert _by_id(res) == {world.ci[DB]: [3], world.ci[WEB]: [1], world.ci[CORE]: [1]}


async def test_changes_and_incidents_measures(world: World) -> None:
    measures = ("tickets", "cis", "open_tickets", "changes", "incidents", "open_incidents")
    res = await _run(world, USER_A, measures=measures)
    assert [dict(zip(measures, row, strict=True)) for row in res.rows] == [
        {
            "tickets": 4,  # INC_OPEN, INC_CLOSED, CHG, REQ
            "cis": 3,
            "open_tickets": 3,  # all but INC_CLOSED
            "changes": 1,  # CHG has a change record
            "incidents": 2,  # INC_OPEN, INC_CLOSED (type role incident)
            "open_incidents": 1,
        }
    ]
    only_changes = await _run(world, USER_A, filter_={"field": "is_change", "op": "is_true"})
    assert only_changes.rows == [[1]]
    by_phase = await _run(world, USER_A, dims=("change_phase",), measures=("changes",))
    # tickets that are no change form the NULL group (shown as "None" here)
    assert _by_key(by_phase) == {"implementing": [1], "None": [0]}


async def test_ci_class_dimension(world: World) -> None:
    res = await _run(world, USER_A, dims=("ci_class",), measures=("changes", "cis"))
    assert _labelled(res, 1) == {("ist972srv",): [1, 2], ("ist972net",): [0, 1]}
    assert "ist972sec" not in {v for col in res.labels.values() for v in col.values()}


async def test_ci_state_dimensions(world: World) -> None:
    res = await _run(world, USER_A, dims=("ci_incident_state",), measures=("cis",))
    assert _labelled(res, 1) == {("Operational",): [2], ("Incident",): [1]}
    res = await _run(world, USER_A, dims=("ci_incident_category",), measures=("cis",))
    assert _by_key(res) == {"operational": [2], "incident": [1]}
    res = await _run(world, USER_A, dims=("ci_deployment_state",), measures=("cis",))
    assert _labelled(res, 1) == {("Production",): [2], ("Planned",): [1]}


def _by_key(res: ReportResultOut) -> dict[str, list[Any]]:
    return {str(row[0]): row[1:] for row in res.rows}


def test_digits_only_compiles_per_dialect() -> None:
    from sqlalchemy import column

    expr = digits_only(column("target_key"))
    my = str(expr.compile(dialect=mysql.dialect()))
    pg = str(expr.compile(dialect=postgresql.dialect()))
    assert "REGEXP" in my and "^[0-9]{1,18}$" in my
    assert "~" in pg and "^[0-9]{1,18}$" in pg


def test_source_reads_only_tiqora_tables() -> None:
    tables = source_tables(ci_tickets_source())
    assert tables
    assert all(t.startswith("tiqora_") for t in tables), sorted(tables)
    assert {"tiqora_itsm_link", "tiqora_cmdb_ci", "tiqora_report_ticket_fact"} <= tables


def test_source_is_double_scoped_and_itsm_only() -> None:
    src = ci_tickets_source()
    assert src.scope_kind == "queue"
    assert [kind for kind, _ in src.extra_scopes] == ["cmdb_class"]
    assert src.scope_kinds() == {"queue", "cmdb_class"}
    assert src.availability == "itsm_tiqora"
    assert src.requires_facts is True
    assert src.time_fields == ("created", "closed")
    assert src.default_time_field == "created"
    assert src.archive_exclusion is not None
    assert set(src.common_fields) == {"queue", "priority", "service"}
    assert {f.key for f in src.filter_fields} == {"is_change"}


def test_dimensions_are_classified_for_outward_paths() -> None:
    keys = {d.key for d in ci_tickets_source().dimensions}
    assert keys == {
        "ci",
        "ci_class",
        "ci_deployment_state",
        "ci_incident_state",
        "ci_incident_category",
        "link_type",
        "ticket_type",
        "itsm_role",
        "state_type",
        "queue",
        "priority",
        "service",
        "change_phase",
        "created",
        "closed",
    }
    assert "ci" in PERSON_DIMENSIONS
    assert not PERSON_DIMENSIONS & NON_PERSON_DIMENSIONS
    assert keys - {"ci"} <= NON_PERSON_DIMENSIONS


def test_picker_sources_follow_the_ruling() -> None:
    picks = {d.key: d.options_source for d in ci_tickets_source().dimensions}
    assert picks["ci_class"] == "cmdb_class"
    assert picks["ci_deployment_state"] == "cmdb_deployment_state"
    assert picks["ci_incident_state"] == "cmdb_incident_state"
    assert picks["ci"] is None
