"""Built-in report catalogue v1: definitions (pure) and exact results on both dialects.

Seed ids: 47790-47809 (names contain ``rep47``): user 47790 (``ro`` on group 47791), readable
queue 47791 (first response target 20 min, solution target 1400 min), foreign queue 47792
(no access), tickets 47795 (closed, response breached), 47796 (open), 47797 (reopened),
47798 (created in the foreign queue, moved over), 47799 (away and back), 47802/47803 (foreign),
bookings 47800/47801/47804, one snapshot day. Every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.reporting.catalogue import (
    BUILTIN_REPORTS,
    QUEUE_PARAM_FIELD,
    BuiltinReport,
    core_builtin_reports,
)
from tiqora.reporting.compiler import RunParams
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
AGENT, QUEUE, FOREIGN = 47790, 47791, 47792  # FOREIGN: queue the agent cannot read
T_DONE, T_OPEN, T_REOPEN, T_MOVED, T_PING = 47795, 47796, 47797, 47798, 47799
T_FOREIGN_DONE, T_FOREIGN_OPEN = 47802, 47803
MY_TICKETS = (T_DONE, T_OPEN, T_REOPEN, T_MOVED, T_PING)
ALL_TICKETS = (*MY_TICKETS, T_FOREIGN_DONE, T_FOREIGN_OPEN)
FOREIGN_NAME = "rep47-queue-47792"
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
        for gid, first, solution in ((QUEUE, 20, 1400), (FOREIGN, 0, 0)):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-group-{gid}", "t": t0},
            )
            # first response 20 min, solution 1400 min (working minutes) on the readable queue
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, first_response_time,"
                    " solution_time, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :id, 1, 1, 1, 1, 0, :fr, :sol, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-queue-{gid}", "fr": first, "sol": solution, "t": t0},
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

        def ticket(tid: int, state: str, at: datetime, queue: int = QUEUE) -> None:
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
                    "q": queue,
                    "u": AGENT,
                    "s": st[state],
                    "c": CUSTOMER,
                    "cu": CUSTOMER_USER,
                    "t": at,
                },
            )

        def history(
            tid: int, htype: int, name: str, state: str, at: datetime, queue: int = QUEUE
        ) -> None:
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
                    "q": queue,
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

        h_move = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'Move'")

        def created(
            tid: int, state: str, at: datetime, queue: int = QUEUE, current: int | None = None
        ) -> None:
            ticket(tid, state, at, current or queue)
            name = f"%%2024060{tid}%%rep47-queue-{queue}%%3 normal%%new%%{tid}"
            history(tid, h_new, name, "new", at, queue)
            article(tid, s_customer, at)

        def closed(tid: int, old: str, at: datetime) -> None:
            names = {"new": "new", "open": "open", "closed": "closed successful"}
            history(tid, h_state, f"%%{names[old]}%%closed successful", "closed", at)

        def d(day: int, hour: int, minute: int = 0) -> datetime:
            return datetime(2024, 6, day, hour, minute)

        # DONE: Mon 06-03 08:00, reply after 30 min (target 20: breached), closed after 2 h
        created(T_DONE, "closed", d(3, 8))
        article(T_DONE, s_agent, d(3, 8, 30))
        closed(T_DONE, "new", d(3, 10))
        # OPEN: Mon 06-10 09:00, set to open after 1 h, no reply
        created(T_OPEN, "open", d(10, 9))
        history(T_OPEN, h_state, "%%new%%open", "open", d(10, 10))
        # REOPEN: Tue 06-04 08:00, reply after 10 min, closed, reopened, closed again
        created(T_REOPEN, "closed", d(4, 8))
        article(T_REOPEN, s_agent, d(4, 8, 10))
        closed(T_REOPEN, "new", d(4, 10))
        history(T_REOPEN, h_state, "%%closed successful%%open", "open", d(5, 10))
        closed(T_REOPEN, "open", d(6, 10))
        # MOVED: created in the foreign queue Tue 06-11 08:00, moved to QUEUE after 30 min
        created(T_MOVED, "new", d(11, 8), FOREIGN, current=QUEUE)
        article(T_MOVED, s_agent, d(11, 8, 5))
        history(
            T_MOVED,
            h_move,
            f"%%rep47-queue-{QUEUE}%%{QUEUE}%%rep47-queue-{FOREIGN}%%{FOREIGN}",
            "new",
            d(11, 8, 30),
        )
        # PING: Wed 06-12 08:00 in QUEUE, reply after 19 min (near miss), away and back
        created(T_PING, "new", d(12, 8))
        article(T_PING, s_agent, d(12, 8, 19))
        for to, frm, at in ((FOREIGN, QUEUE, d(12, 9)), (QUEUE, FOREIGN, d(12, 10))):
            history(
                T_PING,
                h_move,
                f"%%rep47-queue-{to}%%{to}%%rep47-queue-{frm}%%{frm}",
                "new",
                at,
                to,
            )
        # foreign queue: one closed (with a booking), one open ticket
        created(T_FOREIGN_DONE, "closed", d(3, 8), FOREIGN)
        article(T_FOREIGN_DONE, s_agent, d(3, 8, 5))
        history(T_FOREIGN_DONE, h_state, "%%new%%closed successful", "closed", d(3, 9), FOREIGN)
        created(T_FOREIGN_OPEN, "new", d(12, 8), FOREIGN)
        for taid, tid, units, at in (
            (47800, T_DONE, "1.50", datetime(2024, 5, 20, 10)),
            (47801, T_DONE, "2.00", datetime(2024, 6, 3, 10)),
            (47804, T_FOREIGN_DONE, "3.00", datetime(2024, 5, 21, 10)),
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


class _WorkingHours(SysConfig):
    """Mon-Fri 08:00-18:00 UTC: every seeded reply lies inside the working hours."""

    async def get(self, name: str, default: Any = None) -> Any:
        if name == "TimeWorkingHours":
            return {day: list(range(8, 18)) for day in ("Mon", "Tue", "Wed", "Thu", "Fri")}
        if name in ("TimeVacationDays", "TimeVacationDaysOneTime"):
            return {}
        if name == "OTRSTimeZone":
            return "UTC"
        return await super().get(name, default)


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
            await rebuild_ticket_facts(s, ALL_TICKETS, sysconfig=_WorkingHours(s), now=BUILT)
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


JUNE = TypeAdapter(TimeRange).validate_python(
    {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-30"}
)


_JUNE_KW = {"time_range": JUNE}
#: reports whose own window (March-May) is empty in the seed run over June instead
_JUNE_RUNS = {
    "sla_compliance",
    "sla_by_customer",
    "first_contact_resolution",
    "top_customers",
}
E = CUSTOMER
EXPECTED_ROWS: dict[str, list[list[Any]]] = {
    "ticket_kpis": [[5, 2, 0.4, 14.5, 720.0]],
    "volume": [
        ["2024-06-03", "close", 3],
        ["2024-06-03", "create", 2],
        ["2024-06-10", "create", 3],
    ],
    "backlog_trend": [["2024-06-18", 3]],
    "open_now": [[QUEUE, 3]],
    "agent_activity": [[AGENT, "reply", 4], [AGENT, "close", 3]],
    "volume_by_channel": [["2024-06-03", "email", 2], ["2024-06-10", "email", 3]],
    "channel_mix": [["email", 5]],
    "volume_by_queue": [[QUEUE, 5, 2]],
    "backlog_by_state": [["2024-06-18", "new", 2], ["2024-06-18", "open", 1]],
    "backlog_aging": [[QUEUE, 0, 0, 0, 3, 0]],
    # tickets, sla_met, response breach, solution breach, near misses (2), update breaches
    "sla_compliance": [[QUEUE, 5, 0.2, 0.4, 0.6, 1, 1, 0]],
    "sla_times": [
        ["2024-06-03", 20.0, 28.0, 720.0, 1200.0],
        ["2024-06-10", 12.0, 17.6, None, None],
    ],
    "sla_by_customer": [[E, 5, 0.2, 2, 3, 14.5, 720.0]],
    "first_contact_resolution": [[QUEUE, 2, 0.5, 0.5]],
    "reopen_trend": [["2024-06-03", 0.5, 1, 2]],
    # (-1 = a queue the viewer cannot read)
    "queue_transfers": [[-1, QUEUE, 2, 2], [QUEUE, -1, 1, 1]],
    "pingpong": [[QUEUE, 5, 1, 0.6]],
    "time_by_customer": [[E, 1.5, 1, 1]],
    "time_by_queue": [[QUEUE, 1.5, 1, 1]],
    "time_by_agent": [[AGENT, 1.5, 1, 1]],
    "time_by_agent_month": [[AGENT, "2024-05-01", 1.5], [AGENT, "2024-06-01", 2.0]],
    "top_customers": [[E, 5, 2, 720.0, 3.5]],
    "created_heatmap": [[1, 10, 1], [1, 11, 1], [2, 10, 2], [3, 10, 1]],
}


def _data_rows(rows: list[list[Any]], width: int) -> list[list[Any]]:
    """Rows with data: zero-filled buckets (all measures 0 / None) are dropped."""
    return sorted(
        (r for r in rows if any(c not in (0, None) for c in r[width:])),
        key=lambda r: [str(c) for c in r],
    )


def _kwargs(r: BuiltinReport) -> dict[str, Any]:
    return _JUNE_KW if r.name in _JUNE_RUNS else {}


async def _run_all(factory: async_sessionmaker[AsyncSession], **extra: Any) -> dict[str, Any]:
    out = {}
    for r in core_builtin_reports():
        out[r.name] = await _run(factory, r, **_kwargs(r), **extra)
    return out


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_every_builtin_runs_and_has_exact_values(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Executes (hence compiles) all 25 on this dialect; exact rows for every seeded report.

    Run once without and once with the ``queues`` parameter: the viewer's own scope already
    limits the data, so the parameter must change nothing here.
    """
    factory = await _factory(request, url_fixture)
    engine = factory.kw["bind"]
    try:
        for extra in ({}, {"parameters": {"queues": [QUEUE]}}):
            results = await _run_all(factory, **extra)
            for r in core_builtin_reports():
                res = results[r.name]
                width = len(r.definition.dimensions)
                expected = [d.key for d in r.definition.dimensions] + [
                    m.key for m in r.definition.measures
                ]
                assert [c.key for c in res.columns] == expected, r.key
                assert [c.role for c in res.columns] == ["dimension"] * width + ["measure"] * len(
                    r.definition.measures
                )
                assert all(len(row) == len(expected) for row in res.rows), r.key
                assert res.report_key == r.key
                if r.name in EXPECTED_ROWS:
                    got = _data_rows(res.rows, width)
                    want = sorted(EXPECTED_ROWS[r.name], key=lambda x: [str(c) for c in x])
                    assert len(got) == len(want), (r.key, extra, got)
                    for g, w in zip(got, want, strict=True):
                        assert g == [pytest.approx(c) if isinstance(c, float) else c for c in w], (
                            r.key,
                            extra,
                        )
            # dwell time per previous state, keyed by label (state ids differ per database)
            dwell = results["time_in_state"]
            by_state = {dwell.labels["from_state"][str(row[0])]: row[1:] for row in dwell.rows}
            assert by_state == {
                "new": [3, 2.0, 2.0, pytest.approx(5 / 3)],
                "closed successful": [1, 24.0, 24.0, 24.0],
                "open": [1, 24.0, 24.0, 24.0],
            }
            # bookings by type: one type, May booking of the readable ticket only
            assert [row[1:] for row in results["time_by_type"].rows] == [[1.5, 1, 1]]
            assert results["time_by_queue"].totals is not None
            assert results["time_by_queue"].totals.grand[1:] == [1.5, 1, 1]
            assert results["time_by_agent_month"].totals.rows[str(AGENT)][2] == 3.5  # type: ignore[union-attr]
            assert results["queue_transfers"].totals.grand[2:] == [3, 2]  # type: ignore[union-attr]
    finally:
        await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_foreign_queue_is_invisible_in_every_builtin(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    engine = factory.kw["bind"]
    try:
        for extra in ({}, {"parameters": {"queues": [QUEUE]}}):
            for name, res in (await _run_all(factory, **extra)).items():
                dumped = res.model_dump_json()
                assert FOREIGN_NAME not in dumped, name
                assert str(FOREIGN) not in dumped, name
        results = await _run_all(factory)
        # the foreign tickets are not counted anywhere (5 readable tickets, 3 open)
        assert results["ticket_kpis"].rows[0][0] == 5
        assert results["open_now"].rows == [[QUEUE, 3]]
        assert results["backlog_trend"].rows == [["2024-06-18", 3]]
        assert [row[1:] for row in results["time_by_queue"].rows] == [[1.5, 1, 1]]
        # the hidden side of a queue move is shown as "Andere Queue"
        transfers = results["queue_transfers"]
        assert transfers.labels["from_queue"]["-1"] == "Andere Queue"
        assert transfers.labels["to_queue"]["-1"] == "Andere Queue"
        # a parameter naming the foreign queue still finds nothing
        r = next(b for b in core_builtin_reports() if b.name == "open_now")
        res = await _run(factory, r, parameters={"queues": [FOREIGN]})
        assert res.rows == [] and FOREIGN_NAME not in res.model_dump_json()
    finally:
        await engine.dispose()
