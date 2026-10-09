"""Built-in report catalogue v1: definitions (pure) and one execution per report on both dialects.

Seed ids: 47790-47809 (names contain ``rep47``): user 47790 (``ro`` on group 47791), group
and queue 47791, tickets 47795 (new, agent reply, closed) and 47796 (open, moved), bookings
47800/47801, one snapshot day. Every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.filters.tickets import TicketQueryScope
from tiqora.reporting.catalogue import (
    BUILTIN_REPORTS,
    QUEUE_PARAM_FIELD,
    BuiltinReport,
    core_builtin_reports,
)
from tiqora.reporting.compiler import RunParams, compile_report
from tiqora.reporting.definition import TimeRange, validate_definition
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.semantic import REPORT_SOURCES
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.snapshots import snapshot_days
from tiqora.znuny.sysconfig import SysConfig

EXPECTED = [
    "ticket_kpis",
    "volume",
    "backlog_trend",
    "open_now",
    "agent_activity",
    "volume_by_channel",
    "channel_mix",
    "volume_by_queue",
    "backlog_by_state",
    "backlog_aging",
    "sla_compliance",
    "sla_times",
    "sla_by_customer",
    "first_contact_resolution",
    "reopen_trend",
    "queue_transfers",
    "pingpong",
    "time_in_state",
    "time_by_customer",
    "time_by_queue",
    "time_by_agent",
    "time_by_type",
    "time_by_agent_month",
    "top_customers",
    "created_heatmap",
]
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
LO, HI = 47790, 47809
AGENT, QUEUE = 47790, 47791
T_DONE, T_OPEN = 47795, 47796
NOW = datetime(2024, 6, 20, 12, 0)
BUILT = datetime(2024, 6, 20, 11, 0)
SNAPSHOT_DAY = date(2024, 6, 18)
CUSTOMER = "rep47-cust14"
CUSTOMER_USER = "rep47.cust14@example.org"
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_user WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_company WHERE customer_id = 'rep47-cust14'",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _registry() -> Registry:
    reg = Registry()
    register_core(reg)
    return reg


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


# ---------------------------------------------------------------- pure


def test_catalogue_keys_and_order() -> None:
    reg = _registry()
    assert [r.key for r in reg.items(BUILTIN_REPORTS)] == [f"builtin.{k}" for k in EXPECTED]


def test_every_builtin_validates_and_has_queue_parameter() -> None:
    reg = _registry()
    for r in reg.items(BUILTIN_REPORTS):
        src = validate_definition(r.definition, reg)
        assert r.definition.time_zone is None
        assert [p.key for p in r.definition.parameters] == ["queues"]
        param = r.definition.parameters[0]
        assert (param.field, param.op) == (QUEUE_PARAM_FIELD[r.definition.source], "in")
        assert param.label_key == "reports.parameters.queues"
        assert src.filter_spec(param.field) is not None
        name = r.key.removeprefix("builtin.")
        assert r.title_key == f"reports.builtin.{name}.title"
        assert r.description_key == f"reports.builtin.{name}.description"


def test_categories_follow_the_plan() -> None:
    cats = {r.key.removeprefix("builtin."): r.category for r in core_builtin_reports()}
    assert cats["ticket_kpis"] == "overview"
    assert cats["agent_activity"] == "agents"
    assert cats["time_by_agent_month"] == "time"
    assert cats["created_heatmap"] == "patterns"
    assert set(cats.values()) == {
        "overview",
        "volume",
        "backlog",
        "sla",
        "quality",
        "agents",
        "time",
        "customers",
        "patterns",
    }


def test_overview_reports() -> None:
    reg = _registry()
    assert {r.key for r in reg.items(BUILTIN_REPORTS) if r.overview} == {
        "builtin.ticket_kpis",
        "builtin.volume",
        "builtin.backlog_trend",
        "builtin.open_now",
        "builtin.agent_activity",
    }


def test_line_and_bar_charts_plot_one_unit() -> None:
    reg = _registry()
    checked = 0
    for r in reg.items(BUILTIN_REPORTS):
        viz = r.definition.visualization
        if viz.type not in ("line", "bar", "stacked_bar", "area"):
            continue
        src = reg.get(REPORT_SOURCES, r.definition.source)
        assert src is not None
        idx = viz.chart_measures or list(range(len(r.definition.measures)))
        units = {src.measure(r.definition.measures[i].key).unit for i in idx}  # type: ignore[union-attr]
        assert len(units) == 1, (r.key, units)
        checked += 1
    assert checked >= 12


def test_specific_definitions() -> None:
    by = {r.key.removeprefix("builtin."): r for r in core_builtin_reports()}
    vol = by["volume"].definition
    assert [d.key for d in vol.dimensions] == ["occurred", "kind"]
    assert vol.dimensions[0].grain == "week" and vol.visualization.series_dimension == 1
    assert vol.filter is not None and vol.filter.where.model_dump() == {
        "field": "kind",
        "op": "in",
        "value": ["create", "close"],
    }
    open_now = by["open_now"].definition
    assert open_now.dimensions[0].choices == ["state", "priority", "owner", "type", "service"]
    assert open_now.dimensions[0].top_n == 15 and open_now.time_range is None
    month = by["time_by_agent_month"].definition
    assert month.totals.grand and month.totals.rows and month.totals.columns
    assert by["first_contact_resolution"].definition.time_field == "closed"
    assert by["reopen_trend"].definition.time_field == "closed"
    assert by["time_in_state"].definition.visualization.chart_measures == [1]


@pytest.mark.parametrize("dialect", [mysql.dialect(), postgresql.dialect()], ids=["mysql", "pg"])
def test_every_builtin_compiles_on_both_dialects(dialect: Any) -> None:
    reg = _registry()
    scope = TicketQueryScope(user_id=AGENT, queue_ids=frozenset({QUEUE}))
    for r in reg.items(BUILTIN_REPORTS):
        c = compile_report(
            r.definition,
            RunParams(time_zone="Europe/Berlin"),
            scope,
            now=NOW,
            near_miss_percent=10,
            registry=reg,
        )
        assert c.stmt is not None, r.key
        sql = str(c.stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        assert sql.lstrip().upper().startswith("SELECT"), r.key
        assert [m.key for m in c.measures] == [m.key for m in r.definition.measures]


# ---------------------------------------------------------------- DB


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()
    _SEEDED.clear()


def _id(conn: Any, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _seed(sync_url: str) -> None:
    t0 = datetime(2024, 5, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        h_new = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        h_state = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
        s_customer = _id(conn, "SELECT id FROM article_sender_type WHERE name = 'customer'")
        s_agent = _id(conn, "SELECT id FROM article_sender_type WHERE name = 'agent'")
        chan = _id(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
        st = {
            k: _id(conn, "SELECT id FROM ticket_state WHERE name = :n", n=n)
            for k, n in (("new", "new"), ("open", "open"), ("closed", "closed successful"))
        }
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.cat', 'x', 'Cora', 'Catalogue', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-group-47791', 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-queue-47791', :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": QUEUE, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:c, 'Rep47 Example GmbH', 1, :t, 1, :t, 1)"
            ),
            {"c": CUSTOMER, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :l, :l, :c, 'Carla', 'Customer', 1, :t, 1, :t, 1)"
            ),
            {"id": LO, "l": CUSTOMER_USER, "c": CUSTOMER, "t": t0},
        )
        counter = {"history": LO, "article": LO}

        def ticket(tid: int, state: str, at: datetime) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, :c, :cu,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"20240600{tid}",
                    "title": f"rep47 ticket {tid}",
                    "q": QUEUE,
                    "u": AGENT,
                    "s": st[state],
                    "c": CUSTOMER,
                    "cu": CUSTOMER_USER,
                    "t": at,
                },
            )

        def history(tid: int, htype: int, name: str, state: str, at: datetime) -> None:
            counter["history"] += 1
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": counter["history"],
                    "n": name,
                    "ht": htype,
                    "tid": tid,
                    "q": QUEUE,
                    "o": AGENT,
                    "s": st[state],
                    "t": at,
                },
            )

        def article(tid: int, sender: int, at: datetime) -> None:
            counter["article"] += 1
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, :by, :t, :by)"
                ),
                {
                    "id": counter["article"],
                    "tid": tid,
                    "s": sender,
                    "c": chan,
                    "t": at,
                    "by": AGENT if sender == s_agent else 1,
                },
            )

        new_name = "%%2024060047795%%rep47-queue-47791%%3 normal%%new%%47795"
        ticket(T_DONE, "closed", datetime(2024, 6, 3, 8))
        history(T_DONE, h_new, new_name, "new", datetime(2024, 6, 3, 8))
        article(T_DONE, s_customer, datetime(2024, 6, 3, 8))
        article(T_DONE, s_agent, datetime(2024, 6, 3, 8, 30))
        history(T_DONE, h_state, "%%new%%closed successful", "closed", datetime(2024, 6, 3, 10))
        ticket(T_OPEN, "open", datetime(2024, 6, 10, 9))
        history(T_OPEN, h_new, new_name.replace("47795", "47796"), "new", datetime(2024, 6, 10, 9))
        article(T_OPEN, s_customer, datetime(2024, 6, 10, 9))
        history(T_OPEN, h_state, "%%new%%open", "open", datetime(2024, 6, 10, 10))
        for taid, tid, units, at in (
            (47800, T_DONE, "1.50", datetime(2024, 5, 20, 10)),
            (47801, T_DONE, "2.00", datetime(2024, 6, 3, 10)),
        ):
            conn.execute(
                text(
                    "INSERT INTO time_accounting (id, ticket_id, article_id, time_unit,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, NULL, :u, :t, :by, :t, :by)"
                ),
                {"id": taid, "tid": tid, "u": Decimal(units), "t": at, "by": AGENT},
            )
    engine.dispose()


async def _factory(
    request: pytest.FixtureRequest, url_fixture: str
) -> async_sessionmaker[AsyncSession]:
    sync_url = request.getfixturevalue(url_fixture)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _URLS.add(sync_url)
        _clean(sync_url)
        _seed(sync_url)
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_DONE, T_OPEN], sysconfig=SysConfig(s), now=BUILT)
            await s.commit()
        await snapshot_days(factory, [SNAPSHOT_DAY], zone="UTC", now=BUILT)
        _SEEDED.add(sync_url)
    return factory


async def _run(factory: async_sessionmaker[AsyncSession], r: BuiltinReport, **params: Any) -> Any:
    async with factory() as s:
        return await run_report(
            s,
            defn=r.definition,
            params=RunParams(time_zone="Europe/Berlin", **params),
            user_id=AGENT,
            report_key=r.key,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_every_builtin_runs(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    engine = factory.kw["bind"]
    try:
        results = {}
        for r in core_builtin_reports():
            res = await _run(factory, r, parameters={"queues": [QUEUE]})
            expected = [d.key for d in r.definition.dimensions] + [
                m.key for m in r.definition.measures
            ]
            assert [c.key for c in res.columns] == expected, r.key
            assert [c.role for c in res.columns] == ["dimension"] * len(r.definition.dimensions) + [
                "measure"
            ] * len(r.definition.measures)
            assert all(len(row) == len(expected) for row in res.rows), r.key
            assert res.report_key == r.key
            results[r.key] = res
        # the seed is visible through the catalogue, not just executable
        kpis = results["builtin.ticket_kpis"]
        assert kpis.rows[0][:2] == [2, 1]
        open_now = results["builtin.open_now"]
        assert [row[-1] for row in open_now.rows] == [1]
        assert open_now.labels["queue"][str(QUEUE)] == "rep47-queue-47791"
        assert results["builtin.backlog_trend"].rows
        june = TypeAdapter(TimeRange).validate_python(
            {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-30"}
        )
        top = next(b for b in core_builtin_reports() if b.key == "builtin.top_customers")
        res = await _run(factory, top, time_range=june, parameters={"queues": [QUEUE]})
        assert res.rows[0][:3] == [CUSTOMER, 2, 1]
        assert res.labels["customer"][CUSTOMER] == "Rep47 Example GmbH"
    finally:
        await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_queue_parameter_scopes_the_report(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    engine = factory.kw["bind"]
    try:
        r = next(b for b in core_builtin_reports() if b.key == "builtin.open_now")
        assert (await _run(factory, r, parameters={"queues": [QUEUE]})).rows
        assert (await _run(factory, r, parameters={"queues": [QUEUE + 5000]})).rows == []
    finally:
        await engine.dispose()
