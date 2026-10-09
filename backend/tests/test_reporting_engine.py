"""Report engine: execution, post-processing, labels, cache, time limits, access (both dialects).

Seed ids: 47740-47789 (names contain ``rep47``). Layout:

* users 47740 (agent, ``ro`` on group 47741), 47743 (second agent, ``ro`` on group
  47742), 47744 (no groups), 47745 (admin: ``rw`` on ``admin`` + ``ro`` on 47741);
* groups/queues 47741 (readable by the agent) and 47742 (not readable);
* tickets 47750-47755 in queue 47741, 47756/47757 in 47742, 47758 archived in 47741;
  histories with create/close/reopen/move, agent replies at known minutes, bookings
  47780-47784 in ``time_accounting``; facts built by ``rebuild_ticket_facts``.

Every row this module writes is deleted again (incl. the shared state row).
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.filters import Filter
from tiqora.reporting import engine as engine_mod
from tiqora.reporting.access import NO_REPORT_ACCESS_MESSAGE, require_report_access
from tiqora.reporting.cache import ReportCache
from tiqora.reporting.compiler import HIDDEN_QUEUE_ID, RunParams
from tiqora.reporting.definition import ReportDefinition, TimeWindow
from tiqora.reporting.engine import (
    OTHER_KEY,
    QueryBudget,
    estimate_rows,
    percentile,
    plan_report,
    previous_window,
    raw_measure_values,
    run_report,
)
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.labels import HIDDEN_QUEUE_LABEL, resolve_labels
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.reporting.state import load_state
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]

LO, HI = 47740, 47789
AGENT, AGENT2, NOBODY, ADMIN = 47740, 47743, 47744, 47745
ROLE = 47746
Q1, Q2 = 47741, 47742  # also the group ids
T1, T2, T3, T4, T5, T6, T7, T8, T_ARCH = (
    47750,
    47751,
    47752,
    47753,
    47754,
    47755,
    47756,
    47757,
    47758,
)
Q1_TICKETS = (T1, T2, T3, T4, T5, T6)
ALL_TICKETS = (*Q1_TICKETS, T7, T8, T_ARCH)
CUSTOMER = "rep47-cust"
CUSTOMER_USER = "rep47.cust@example.org"
NOW = datetime(2024, 6, 20, 12, 0)
BUILT = datetime(2024, 6, 20, 11, 0)
JUNE = {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-30"}
UTC_PARAMS = RunParams(time_zone="UTC")

_URLS: set[str] = set()
_SEEDED: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM group_role WHERE role_id BETWEEN :lo AND :hi",
    "DELETE FROM role_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM roles WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_user WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_company WHERE customer_id = 'rep47-cust'",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)


def D(raw: dict[str, Any]) -> ReportDefinition:  # noqa: N802 - brief's helper name
    return ReportDefinition.model_validate(raw)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


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


def _one(conn: Any, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _seed_znuny(sync_url: str) -> dict[str, int]:
    """Users, groups, queues, tickets, articles, histories and bookings (Znuny tables)."""
    t0 = datetime(2024, 5, 1)
    engine = create_engine(sync_url)
    ids: dict[str, int] = {}
    with engine.begin() as conn:
        for name in ("NewTicket", "Move", "StateUpdate"):
            ids[name] = _one(conn, "SELECT id FROM ticket_history_type WHERE name = :n", n=name)
        for name in ("customer", "agent"):
            ids[name] = _one(conn, "SELECT id FROM article_sender_type WHERE name = :n", n=name)
        ids["email"] = _one(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
        for key, name in (("new", "new"), ("open", "open"), ("closed", "closed successful")):
            ids[key] = _one(conn, "SELECT id FROM ticket_state WHERE name = :n", n=name)
        ids["admin_group"] = _one(conn, "SELECT id FROM permission_groups WHERE name = 'admin'")
        ids["stats_group"] = _one(conn, "SELECT id FROM permission_groups WHERE name = 'stats'")

        for uid, login, first, last in (
            (AGENT, "rep47.eng", "Erik", "Engine"),
            (AGENT2, "rep47.eng2", "Berta", "Booker"),
            (NOBODY, "rep47.none", "", ""),
            (ADMIN, "rep47.admin", "Ada", "Admin"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :s, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "s": last, "t": t0},
            )
        for gid in (Q1, Q2):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-group-{gid}", "t": t0},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-queue-{gid}", "g": gid, "t": t0},
            )
        for uid, gid, key in (
            (AGENT, Q1, "ro"),
            (AGENT2, Q2, "ro"),
            (ADMIN, Q1, "ro"),
            (ADMIN, ids["admin_group"], "rw"),
        ):
            _grant(conn, uid, gid, key)
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

        def ticket(tid: int, queue: int, state: str, created: datetime, archived: int = 0) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, :c, :cu,"
                    " 0, 0, 0, 0, 0, 0, :a, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"20240600{tid}",
                    "title": f"rep47 ticket {tid}",
                    "q": queue,
                    "u": AGENT,
                    "s": ids[state],
                    "c": CUSTOMER,
                    "cu": CUSTOMER_USER,
                    "a": archived,
                    "t": created,
                },
            )

        def history(tid: int, htype: str, name: str, queue: int, state: str, at: datetime) -> None:
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
                    "ht": ids[htype],
                    "tid": tid,
                    "q": queue,
                    "o": AGENT,
                    "s": ids[state],
                    "t": at,
                },
            )

        def article(tid: int, sender: str, at: datetime) -> None:
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
                    "s": ids[sender],
                    "c": ids["email"],
                    "t": at,
                    "by": AGENT if sender == "agent" else 1,
                },
            )

        def created(tid: int, queue: int, state: str, at: datetime, archived: int = 0) -> None:
            ticket(tid, queue, state, at, archived)
            name = f"%%20240600{tid}%%rep47-queue-{queue}%%3 normal%%new%%{tid}"
            history(tid, "NewTicket", name, queue, "new", at)
            article(tid, "customer", at)

        def state(tid: int, queue: int, old: str, new: str, at: datetime) -> None:
            names = {"new": "new", "open": "open", "closed": "closed successful"}
            history(tid, "StateUpdate", f"%%{names[old]}%%{names[new]}", queue, new, at)

        def d(day: int, hour: int, minute: int = 0) -> datetime:
            return datetime(2024, 6, day, hour, minute)

        # T1: Mon 06-03 08:00, reply after 30 min, closed 10:00
        created(T1, Q1, "closed", d(3, 8))
        article(T1, "agent", d(3, 8, 30))
        state(T1, Q1, "new", "closed", d(3, 10))
        # T2: Mon 06-03 09:00, reply after 10 min, closed, reopened, closed again
        created(T2, Q1, "closed", d(3, 9))
        article(T2, "agent", d(3, 9, 10))
        state(T2, Q1, "new", "closed", d(4, 10))
        state(T2, Q1, "closed", "open", d(5, 10))
        state(T2, Q1, "open", "closed", d(6, 10))
        # T3: Wed 06-05 08:00, reply after 20 min, still new
        created(T3, Q1, "new", d(5, 8))
        article(T3, "agent", d(5, 8, 20))
        # T4: Wed 06-12 08:00, reply after 40 min, closed 12:00
        created(T4, Q1, "closed", d(12, 8))
        article(T4, "agent", d(12, 8, 40))
        state(T4, Q1, "new", "closed", d(12, 12))
        # T5: Thu 06-13 09:00 created in Q2 (not readable), moved to Q1 at 10:00, no reply
        created(T5, Q1, "new", d(13, 9))
        conn.execute(
            text("UPDATE ticket_history SET queue_id = :q, name = :n WHERE id = :id"),
            {
                "q": Q2,
                "n": f"%%20240600{T5}%%rep47-queue-{Q2}%%3 normal%%new%%{T5}",
                "id": counter["history"],
            },
        )
        history(
            T5, "Move", f"%%rep47-queue-{Q1}%%{Q1}%%rep47-queue-{Q2}%%{Q2}", Q1, "new", d(13, 10)
        )
        # T6: Sat 06-08 08:00, set to open, no reply
        created(T6, Q1, "open", d(8, 8))
        state(T6, Q1, "new", "open", d(8, 9))
        # outside the agent's scope
        created(T7, Q2, "closed", d(3, 8))
        article(T7, "agent", d(3, 8, 5))
        state(T7, Q2, "new", "closed", d(3, 9))
        created(T8, Q2, "new", d(12, 8))
        # archived (closed) ticket in the agent's queue
        created(T_ARCH, Q1, "closed", d(4, 8), archived=1)
        article(T_ARCH, "agent", d(4, 8, 15))
        state(T_ARCH, Q1, "new", "closed", d(4, 9))

        for taid, tid, units, at, by in (
            (47780, T1, "1.50", datetime(2024, 5, 20, 10), AGENT),
            (47781, T1, "2.00", datetime(2024, 6, 3, 10), AGENT),
            (47782, T2, "0.50", datetime(2024, 6, 4, 10), AGENT2),
            (47783, T2, "1.25", datetime(2024, 5, 21, 10), AGENT2),
            (47784, T7, "5.00", datetime(2024, 6, 3, 10), AGENT),
        ):
            conn.execute(
                text(
                    "INSERT INTO time_accounting (id, ticket_id, article_id, time_unit,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, NULL, :u, :t, :by, :t, :by)"
                ),
                {"id": taid, "tid": tid, "u": Decimal(units), "t": at, "by": by},
            )
    engine.dispose()
    return ids


def _grant(conn: Any, uid: int, gid: int, key: str) -> None:
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, :k, :t, 1, :t, 1)"
        ),
        {"u": uid, "g": gid, "k": key, "t": datetime(2024, 5, 1)},
    )


_IDS: dict[str, dict[str, int]] = {}


class _WorkingHours(SysConfig):
    """Mon-Fri 08:00-18:00 UTC: every seeded reply lies inside, so working = wall minutes."""

    async def get(self, name: str, default: Any = None) -> Any:
        if name == "TimeWorkingHours":
            return {day: list(range(8, 18)) for day in ("Mon", "Tue", "Wed", "Thu", "Fri")}
        if name in ("TimeVacationDays", "TimeVacationDaysOneTime"):
            return {}
        if name == "OTRSTimeZone":
            return "UTC"
        return await super().get(name, default)


async def _rebuild(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await rebuild_ticket_facts(s, ALL_TICKETS, sysconfig=_WorkingHours(s), now=BUILT)
        await s.commit()


class _Db:
    def __init__(self, engine: AsyncEngine, ids: dict[str, int]) -> None:
        self.engine = engine
        self.factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        self.ids = ids
        self.statements: list[str] = []
        event.listen(engine.sync_engine, "before_cursor_execute", self._record)

    def _record(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        self.statements.append(statement)

    async def set_status(self, value: str) -> None:
        async with self.factory() as s:
            (await load_state(s)).backfill_status = value
            await s.commit()

    def fact_selects(self) -> int:
        return sum(
            1
            for s in self.statements
            if s.lstrip().upper().startswith("SELECT") and "tiqora_report_ticket_fact" in s
        )

    async def run(self, defn: dict[str, Any], user: int = AGENT, **kw: Any) -> Any:
        params = kw.pop("params", UTC_PARAMS)
        async with self.factory() as s:
            return await run_report(
                s,
                defn=D(defn),
                params=params,
                user_id=user,
                report_key=kw.pop("report_key", None),
                reporting=kw.pop("reporting", ReportingSettings()),
                now=NOW,
                **kw,
            )


async def _db(request: pytest.FixtureRequest, url_fixture: str) -> _Db:
    sync_url = request.getfixturevalue(url_fixture)
    engine = create_async_engine(_to_async_url(sync_url))
    if sync_url not in _SEEDED:
        _URLS.add(sync_url)
        _clean(sync_url)
        _IDS[sync_url] = _seed_znuny(sync_url)
        await _rebuild(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
        _SEEDED.add(sync_url)
    return _Db(engine, _IDS[sync_url])


def _by_key(rows: list[list[Any]], width: int = 1) -> dict[Any, list[Any]]:
    return {tuple(r[:width]) if width > 1 else r[0]: r[width:] for r in rows}


class _FakeBackend:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


# ---------------------------------------------------------------- pure helpers


def test_percentile_interpolates_like_percentile_cont() -> None:
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.9) == 7.0
    assert percentile([40.0, 10.0, 30.0, 20.0], 0.5) == 25.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 0.9) == pytest.approx(37.0)


def test_previous_window_whole_days_and_exact_bounds() -> None:
    # Berlin, 10 local days across the DST change (03-31): the previous window is the
    # 10 days before, from local midnight to local midnight
    w = TimeWindow(
        datetime(2024, 3, 25, 23),
        datetime(2024, 4, 4, 22),
        date(2024, 3, 26),
        date(2024, 4, 4),
        "Europe/Berlin",
    )
    p = previous_window(w)
    assert (p.start_date, p.end_date) == (date(2024, 3, 16), date(2024, 3, 25))
    assert (p.start_utc, p.end_utc) == (datetime(2024, 3, 15, 23), w.start_utc)
    # exact UTC bounds (/stats override): shifted by the span
    x = TimeWindow(
        datetime(2024, 6, 11, 6),
        datetime(2024, 6, 12, 6),
        date(2024, 6, 11),
        date(2024, 6, 12),
        "UTC",
    )
    q = previous_window(x)
    assert (q.start_utc, q.end_utc) == (datetime(2024, 6, 10, 6), datetime(2024, 6, 11, 6))
    assert (q.start_date, q.end_date) == (date(2024, 6, 10), date(2024, 6, 11))


def test_query_budget_job_and_inline() -> None:
    reporting = ReportingSettings(query_timeout_seconds=60)
    assert QueryBudget.job(reporting).statement_seconds() == 60
    now = [100.0]
    budget = QueryBudget.inline(reporting, clock=lambda: now[0])
    assert budget.statement_seconds() == 60  # capped by the per-query setting
    now[0] += 30
    assert budget.statement_seconds() == 45  # remaining of 75
    now[0] += 44.5
    with pytest.raises(ReportQueryTimeout):
        budget.statement_seconds()


# ---------------------------------------------------------------- engine (DB)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_counts_by_queue_only_visible(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "queue"}],
                "measures": [{"key": "tickets"}],
                "time_range": JUNE,
            }
        )
        assert r.rows == [[Q1, 6]] and r.labels["queue"][str(Q1)] == f"rep47-queue-{Q1}"
        assert [c.key for c in r.columns] == ["queue", "tickets"] and r.columns[1].type == "count"
        assert (r.columns[0].role, r.columns[0].type, r.columns[0].label_key) == (
            "dimension",
            "id",
            "reports.dimensions.queue",
        )
        assert r.row_count == 1 and r.truncated is False and r.cached is False
        assert r.time_window is not None and r.time_window.start_date == date(2024, 6, 1)

        # created in a queue the agent cannot read: counted, but the queue stays anonymous
        h = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "queue_at_create"}],
                "measures": [{"key": "tickets"}],
                "time_range": JUNE,
            }
        )
        assert _by_key(h.rows) == {Q1: [5], HIDDEN_QUEUE_ID: [1]}
        assert h.labels["queue_at_create"] == {
            str(Q1): f"rep47-queue-{Q1}",
            str(HIDDEN_QUEUE_ID): HIDDEN_QUEUE_LABEL,
        }
        assert f"rep47-queue-{Q2}" not in h.model_dump_json()
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rates_and_percentiles(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "state_type"}],
                "measures": [
                    {"key": "reopen_rate"},
                    {"key": "first_response_p50"},
                    {"key": "first_response_p90"},
                    {"key": "closed_tickets"},
                ],
                "time_range": JUNE,
                "totals": {"grand": True},
            }
        )
        rows = _by_key(r.rows)
        # closed: T1 (30 min), T2 (10, reopened once), T4 (40)
        assert rows["closed"] == [1 / 3, 30.0, pytest.approx(38.0), 3]
        assert rows["new"] == [None, 20.0, 20.0, 0]  # T3 (20), T5 (no reply); no close -> rate None
        assert rows["open"] == [None, None, None, 0]  # T6: no close, no reply
        assert [c.type for c in r.columns] == ["enum", "percent", "minutes", "minutes", "count"]
        assert r.columns[0].i18n_values == "reports.values.state_type"
        assert r.totals is not None
        # grand total from its own query: median of all four reply times, not of the groups
        assert r.totals.grand == [None, 1 / 3, 25.0, pytest.approx(37.0), 3]
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_time_series_is_filled_with_zero_buckets(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "created", "grain": "day"}],
                "measures": [{"key": "tickets"}, {"key": "first_response_avg"}],
                "time_range": {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-07"},
            }
        )
        assert [row[0] for row in r.rows] == [f"2024-06-0{d}" for d in range(1, 8)]
        assert [row[1] for row in r.rows] == [0, 0, 2, 0, 1, 0, 0]  # archived 06-04 ticket excluded
        assert r.rows[0][2] is None and r.rows[2][2] == 20.0  # avg of 30 and 10
        assert r.columns[0].type == "date" and r.columns[0].grain == "day"

        weekly = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "created", "grain": "week"}],
                "measures": [{"key": "tickets"}],
                "time_range": JUNE,
            }
        )
        # weeks starting Monday; the window starts on Saturday 06-01 -> first bucket 05-27
        assert [row[0] for row in weekly.rows] == [
            "2024-05-27",
            "2024-06-03",
            "2024-06-10",
            "2024-06-17",
            "2024-06-24",
        ]
        assert [row[1] for row in weekly.rows] == [0, 4, 2, 0, 0]
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_top_n_folds_into_other(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "tickets",
                "dimensions": [{"key": "state", "top_n": 1}],
                "measures": [
                    {"key": "tickets"},
                    {"key": "reopen_rate"},
                    {"key": "first_response_avg"},
                    {"key": "first_response_p50"},
                ],
                "time_range": JUNE,
            }
        )
        closed = db.ids["closed"]
        assert len(r.rows) == 2
        # MariaDB's AVG keeps 4 decimal places
        assert r.rows[0] == [closed, 3, 1 / 3, pytest.approx(80 / 3, abs=1e-3), 30.0]
        # new (T3, T5) + open (T6): counts summed, rate from summed num/den (no close -> None),
        # avg does not add up -> None, percentile from the merged samples (T3: 20)
        assert r.rows[1] == [OTHER_KEY, 3, None, None, 20.0]
        assert (
            OTHER_KEY not in r.labels["state"]
            and r.labels["state"][str(closed)] == "closed successful"
        )
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_totals_grand_rows_columns(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "accounted_time",
                "dimensions": [{"key": "agent"}, {"key": "booked", "grain": "month"}],
                "measures": [{"key": "time_units"}],
                "time_range": {"kind": "absolute", "start": "2024-05-01", "end": "2024-06-30"},
                "totals": {"grand": True, "rows": True, "columns": True},
                "sort": [
                    {"key": "agent", "direction": "asc"},
                    {"key": "booked", "direction": "asc"},
                ],
            }
        )
        # T7's 5.00 units are in a queue the agent cannot read
        assert r.rows == [
            [AGENT, "2024-05-01", 1.5],
            [AGENT, "2024-06-01", 2.0],
            [AGENT2, "2024-05-01", 1.25],
            [AGENT2, "2024-06-01", 0.5],
        ]
        assert r.totals is not None
        assert r.totals.grand == [None, None, 5.25] and sum(row[2] for row in r.rows) == 5.25
        assert r.totals.rows == {str(AGENT): [AGENT, None, 3.5], str(AGENT2): [AGENT2, None, 1.75]}
        assert r.totals.columns == {
            "2024-05-01": [None, "2024-05-01", 2.75],
            "2024-06-01": [None, "2024-06-01", 2.5],
        }
        assert r.labels["agent"] == {str(AGENT): "Erik Engine", str(AGENT2): "Berta Booker"}
        assert r.columns[2].type == "units" and r.columns[1].grain == "month"
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_compare_previous(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        r = await db.run(
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "time_range": {"kind": "absolute", "start": "2024-06-11", "end": "2024-06-20"},
            },
            compare_previous=True,
        )
        assert r.rows == [[2]]  # T4, T5
        assert r.totals is not None and r.totals.grand == [2]
        assert r.previous is not None and r.previous.grand == [4]  # 06-01..06-10: T1, T2, T3, T6

        # no window (live source without a range) -> no previous period
        live = await db.run(
            {"source": "open_tickets", "measures": [{"key": "tickets"}]}, compare_previous=True
        )
        assert live.previous is None and live.time_window is None
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_sort_limit_and_row_cap(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        by_type = {
            "source": "tickets",
            "dimensions": [{"key": "state_type"}],
            "measures": [{"key": "tickets"}],
            "time_range": JUNE,
        }
        default = await db.run(by_type)  # no sort, no date dimension: first measure desc
        assert [row[1] for row in default.rows] == [3, 2, 1]
        asc = await db.run(
            {**by_type, "sort": [{"key": "tickets", "direction": "asc"}], "limit": 1}
        )
        assert asc.rows == [["open", 1]] and asc.truncated is False and asc.row_count == 1
        by_dim = await db.run({**by_type, "sort": [{"key": "state_type", "direction": "desc"}]})
        assert [row[0] for row in by_dim.rows] == ["open", "new", "closed"]
        capped = await db.run(by_type, row_cap=1)
        assert capped.rows == [["closed", 3]] and capped.truncated is True and capped.row_count == 3
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_empty_scope_returns_empty_result_without_query(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    try:
        db.statements.clear()
        defn = {
            "source": "tickets",
            "dimensions": [{"key": "created"}],
            "measures": [{"key": "tickets"}],
            "time_range": JUNE,
            "totals": {"grand": True},
        }
        r = await db.run(defn, user=NOBODY, compare_previous=True)
        assert r.rows == [] and r.totals is None and r.previous is None and r.labels == {}
        assert db.fact_selects() == 0
        async with db.factory() as s:
            plan = await plan_report(
                s,
                defn=D(defn),
                params=UTC_PARAMS,
                user_id=NOBODY,
                reporting=ReportingSettings(),
                now=NOW,
            )
            assert await estimate_rows(s, plan.compiled, reporting=ReportingSettings()) == 0
        assert db.fact_selects() == 0
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_estimate_rows_uses_the_scoped_where(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    try:
        defn = D(
            {
                "source": "tickets",
                "dimensions": [{"key": "state_type"}],
                "measures": [{"key": "tickets"}],
                "time_range": JUNE,
            }
        )
        async with db.factory() as s:
            plan = await plan_report(
                s,
                defn=defn,
                params=UTC_PARAMS,
                user_id=AGENT,
                reporting=ReportingSettings(),
                now=NOW,
            )
            db.statements.clear()
            assert await estimate_rows(s, plan.compiled, reporting=ReportingSettings()) == 6
        (sql,) = [st for st in db.statements if "tiqora_report_ticket_fact" in st]
        assert "count(*)" in sql.lower() and "GROUP BY" not in sql.upper()
        assert "queue_id IN" in sql  # the scope comes with the WHERE
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_facts_status_is_attached(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    defn = {"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE}
    try:
        await db.set_status("pending")
        r = await db.run(defn)
        assert r.facts.ready is False and r.facts.backfill_status == "pending"
        await db.set_status("done")
        r = await db.run(defn)
        assert r.facts.ready is True and r.facts.backfill_status == "done"
    finally:
        await db.set_status("pending")
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_last_snapshot_date_comes_from_the_state_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    defn = D(
        {"source": "daily_snapshot", "measures": [{"key": "open_tickets"}], "time_range": JUNE}
    )
    try:
        async with db.factory() as s:
            await s.execute(text("UPDATE tiqora_report_state SET last_snapshot_date = NULL"))
            await s.commit()
            before = await plan_report(
                s,
                defn=defn,
                params=UTC_PARAMS,
                user_id=AGENT,
                reporting=ReportingSettings(),
                now=NOW,
            )
            await s.execute(
                text("UPDATE tiqora_report_state SET last_snapshot_date = :d"),
                {"d": date(2024, 6, 18)},
            )
            await s.commit()
            # a caller-supplied value is never trusted
            forged = RunParams(time_zone="UTC", last_snapshot_date=date(2030, 1, 1))
            after = await plan_report(
                s, defn=defn, params=forged, user_id=AGENT, reporting=ReportingSettings(), now=NOW
            )
        assert before.params.last_snapshot_date is None and before.compiled.stock_dates == (
            date(2024, 6, 30),
        )
        assert after.params.last_snapshot_date == date(2024, 6, 18)
        assert after.compiled.stock_dates == (date(2024, 6, 18),)
        assert before.cache_key != after.cache_key
    finally:
        async with db.factory() as s:
            await s.execute(text("UPDATE tiqora_report_state SET last_snapshot_date = NULL"))
            await s.commit()
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_open_tickets_live_source(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        async with db.factory() as s:
            await s.execute(
                text("DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi"),
                _BAND,
            )
            await s.execute(
                text("DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi"),
                _BAND,
            )
            await s.commit()
        r = await db.run(
            {
                "source": "open_tickets",
                "dimensions": [{"key": "state_type"}],
                "measures": [{"key": "tickets"}, {"key": "unassigned"}],
            }
        )
        # Q1: T3, T5 new, T6 open; T8 (Q2) not readable; closed and archived ones never
        assert _by_key(r.rows) == {"new": [2, 0], "open": [1, 0]}
        assert r.time_window is None
    finally:
        await _rebuild(db.factory)
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_raw_measure_values(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        defn = D(
            {
                "source": "tickets",
                "dimensions": [{"key": "queue"}],
                "measures": [{"key": "first_response_p50"}],
                "time_range": JUNE,
            }
        )
        async with db.factory() as s:
            values = await raw_measure_values(
                s,
                defn=defn,
                measure_key="first_response_p50",
                params=UTC_PARAMS,
                user_id=AGENT,
                reporting=ReportingSettings(),
                now=NOW,
            )
            assert sorted(values) == [10.0, 20.0, 30.0, 40.0]  # T7 (5 min) is outside the scope
            with pytest.raises(ValueError):
                await raw_measure_values(
                    s,
                    defn=defn,
                    measure_key="tickets",
                    params=UTC_PARAMS,
                    user_id=AGENT,
                    reporting=ReportingSettings(),
                    now=NOW,
                )
            assert (
                await raw_measure_values(
                    s,
                    defn=defn,
                    measure_key="first_response_p50",
                    params=UTC_PARAMS,
                    user_id=NOBODY,
                    reporting=ReportingSettings(),
                    now=NOW,
                )
                == []
            )
    finally:
        await db.engine.dispose()


# ---------------------------------------------------------------- cache through the engine


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_engine_cache_is_per_scope(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    cache = ReportCache(_FakeBackend(), ttl_seconds=300)
    defn = {"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE}
    try:
        await db.set_status("done")
        first = await db.run(defn, cache=cache, report_key="builtin.a")
        assert first.rows == [[6]] and first.cached is False
        db.statements.clear()
        second = await db.run(defn, cache=cache, report_key="builtin.b")
        assert second.cached is True and second.rows == [[6]] and second.report_key == "builtin.b"
        assert db.fact_selects() == 0

        # another scope (queue 47742 only) never reads the agent's entry
        other = await db.run(defn, user=AGENT2, cache=cache)
        assert other.cached is False and other.rows == [[2]]

        # admin with archived tickets: own entry; never served to a non-admin
        admin_all = await db.run(defn, user=ADMIN, cache=cache, include_archived=True)
        assert admin_all.cached is False and admin_all.rows == [[7]]
        agent_asks = await db.run(defn, user=AGENT, cache=cache, include_archived=True)
        assert agent_asks.rows == [[6]] and agent_asks.cached is True  # flag dropped: own entry
        admin_plain = await db.run(defn, user=ADMIN, cache=cache)
        assert admin_plain.rows == [[6]] and admin_plain.cached is True  # same scope as the agent

        # internal extra filters (/stats wrappers) never collide
        closed = Filter.model_validate(
            {"version": 1, "where": {"field": "state_type", "op": "in", "value": ["closed"]}}
        )
        new = Filter.model_validate(
            {"version": 1, "where": {"field": "state_type", "op": "in", "value": ["new"]}}
        )
        a = await db.run(defn, cache=cache, params=RunParams(time_zone="UTC", extra_filter=closed))
        b = await db.run(defn, cache=cache, params=RunParams(time_zone="UTC", extra_filter=new))
        assert (a.rows, a.cached, b.rows, b.cached) == ([[3]], False, [[2]], False)

        # no_cache style runs neither read nor write
        db.statements.clear()
        fresh = await db.run(defn, cache=cache, use_cache=False)
        assert fresh.cached is False and db.fact_selects() == 1
    finally:
        await db.set_status("pending")
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_results_before_the_data_is_ready_are_not_cached(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    backend = _FakeBackend()
    cache = ReportCache(backend, ttl_seconds=300)
    defn = {"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE}
    try:
        await db.set_status("pending")  # e.g. the first backfill is still running
        assert (await db.run(defn, cache=cache)).cached is False
        assert backend.store == {}
        await db.set_status("done")
        ready = await db.run(defn, cache=cache)
        assert ready.cached is False and ready.facts.ready is True  # not the partial one
        assert (await db.run(defn, cache=cache)).cached is True

        # sources that never read the derived data are cached regardless
        await db.set_status("pending")
        live = {"source": "open_tickets", "measures": [{"key": "tickets"}]}
        assert (await db.run(live, cache=cache)).cached is False
        assert (await db.run(live, cache=cache)).cached is True
    finally:
        await db.set_status("pending")
        await db.engine.dispose()


# ---------------------------------------------------------------- labels


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolve_labels(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    try:
        async with db.factory() as s:
            db.statements.clear()
            assert await resolve_labels(s, "queue", [HIDDEN_QUEUE_ID]) == {"-1": HIDDEN_QUEUE_LABEL}
            assert db.statements == []  # the hidden queue is never looked up
            assert await resolve_labels(s, "queue", [Q1, str(Q2), 47789]) == {
                str(Q1): f"rep47-queue-{Q1}",
                str(Q2): f"rep47-queue-{Q2}",
                "47789": "47789",  # missing -> str(key)
            }
            assert await resolve_labels(s, "user", [AGENT, NOBODY]) == {
                str(AGENT): "Erik Engine",
                str(NOBODY): "rep47.none",
            }
            assert await resolve_labels(s, "ticket", [T1]) == {
                str(T1): f"20240600{T1} rep47 ticket {T1}"
            }
            assert await resolve_labels(s, "customer_company", [CUSTOMER, "rep47-nope"]) == {
                CUSTOMER: "Rep47 Example GmbH",
                "rep47-nope": "rep47-nope",
            }
            assert await resolve_labels(s, "customer_user", [CUSTOMER_USER]) == {
                CUSTOMER_USER: "Carla Customer"
            }
            assert (await resolve_labels(s, "state", [db.ids["open"]])) == {
                str(db.ids["open"]): "open"
            }
            assert await resolve_labels(s, "service", [47789]) == {"47789": "47789"}
            assert await resolve_labels(s, "none", [1]) == {}
            assert await resolve_labels(s, "queue", []) == {}
    finally:
        await db.engine.dispose()


# ---------------------------------------------------------------- time limits


def _server_timeout(dialect: str) -> OperationalError:
    if dialect == "postgresql":

        class QueryCanceled(Exception):
            sqlstate = "57014"

        orig: Exception = QueryCanceled("canceling statement due to statement timeout")
    else:
        orig = Exception(1969, "Query execution was interrupted (max_statement_time exceeded)")
    return OperationalError("SELECT ...", {}, orig)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_timeout_maps_to_report_query_timeout(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await _db(request, url_fixture)
    defn = D({"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE})
    try:
        async with db.factory() as s:
            dialect = s.get_bind().dialect.name
            real = s.execute

            async def failing(stmt: Any, *a: Any, **kw: Any) -> Any:
                if "tiqora_report_ticket_fact" in str(stmt):
                    raise _server_timeout(dialect)
                return await real(stmt, *a, **kw)

            monkeypatch.setattr(s, "execute", failing)
            with pytest.raises(ReportQueryTimeout):
                await run_report(
                    s,
                    defn=defn,
                    params=UTC_PARAMS,
                    user_id=AGENT,
                    report_key=None,
                    reporting=ReportingSettings(),
                    now=NOW,
                )
            # server-side abort: the connection stays usable
            fairy = await (await s.connection()).get_raw_connection()
            assert fairy.is_valid is True
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_client_side_timeout_invalidates_the_connection(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await _db(request, url_fixture)
    defn = D({"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE})
    monkeypatch.setattr(engine_mod, "CLIENT_GRACE_SECONDS", 0.2)
    try:
        async with db.factory() as s:
            fairy = await (await s.connection()).get_raw_connection()
            real = s.execute

            async def hanging(stmt: Any, *a: Any, **kw: Any) -> Any:
                if "tiqora_report_ticket_fact" in str(stmt):
                    await asyncio.sleep(30)  # the driver never answers
                return await real(stmt, *a, **kw)

            monkeypatch.setattr(s, "execute", hanging)
            budget = QueryBudget(per_query_seconds=1)
            with pytest.raises(ReportQueryTimeout) as info:
                await run_report(
                    s,
                    defn=defn,
                    params=UTC_PARAMS,
                    user_id=AGENT,
                    report_key=None,
                    reporting=ReportingSettings(),
                    now=NOW,
                    budget=budget,
                )
            assert isinstance(info.value.__cause__, TimeoutError)
            assert fairy.is_valid is False  # never goes back to the pool
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inline_budget_is_shared_by_all_queries(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-INLINEBUDGET: main, raw, totals and previous queries share one 75 s deadline."""
    db = await _db(request, url_fixture)
    clock = [0.0]
    seen: list[int] = []
    real_timeout = engine_mod.statement_timeout

    def recording(session: AsyncSession, seconds: int) -> Any:
        seen.append(seconds)
        clock[0] += 20  # every statement "takes" 20 s
        return real_timeout(session, seconds)

    monkeypatch.setattr(engine_mod, "statement_timeout", recording)
    defn = D(
        {
            "source": "tickets",
            "dimensions": [{"key": "state_type"}],
            "measures": [{"key": "tickets"}, {"key": "first_response_p50"}],
            "time_range": {"kind": "absolute", "start": "2024-06-11", "end": "2024-06-20"},
            "totals": {"grand": True},
        }
    )
    reporting = ReportingSettings(query_timeout_seconds=600)
    try:
        async with db.factory() as s:
            db.statements.clear()
            budget = QueryBudget.inline(reporting, clock=lambda: clock[0])
            with pytest.raises(ReportQueryTimeout):
                await run_report(
                    s,
                    defn=defn,
                    params=UTC_PARAMS,
                    user_id=AGENT,
                    report_key=None,
                    reporting=reporting,
                    now=NOW,
                    budget=budget,
                    compare_previous=True,
                )
        # main, raw, grand, grand raw; the 5th statement finds the budget used up
        assert seen == [75, 55, 35, 15]
        assert db.fact_selects() == 4
        # job mode: every statement gets query_timeout_seconds
        seen.clear()
        async with db.factory() as s:
            r = await run_report(
                s,
                defn=defn,
                params=UTC_PARAMS,
                user_id=AGENT,
                report_key=None,
                reporting=reporting,
                now=NOW,
                compare_previous=True,
            )
        assert r.previous is not None and seen == [600] * 6
    finally:
        await db.engine.dispose()


# ---------------------------------------------------------------- access


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_require_report_access(url_fixture: str, request: pytest.FixtureRequest) -> None:
    db = await _db(request, url_fixture)
    everyone = ReportingSettings(access="all_agents")
    strict = ReportingSettings(access="stats_group")
    try:
        async with db.factory() as s:
            await require_report_access(s, AGENT, everyone)
            with pytest.raises(HTTPException) as info:
                await require_report_access(s, AGENT, strict)
            assert info.value.status_code == 403
            assert info.value.detail == {"message": NO_REPORT_ACCESS_MESSAGE}
            await require_report_access(s, ADMIN, strict)  # admin: always
            await s.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
                ),
                {"u": AGENT2, "g": db.ids["stats_group"], "t": datetime(2024, 5, 1)},
            )
            await s.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": NOBODY, "g": db.ids["stats_group"], "t": datetime(2024, 5, 1)},
            )
            await s.flush()
            await require_report_access(s, AGENT2, strict)  # ro on "stats"
            await require_report_access(s, NOBODY, strict)  # rw implies ro

            # role-derived grant: role -> group_role(stats, ro)
            t = datetime(2024, 5, 1)
            await s.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, 'rep47-role', 1, :t, 1, :t, 1)"
                ),
                {"id": ROLE, "t": t},
            )
            await s.execute(
                text(
                    "INSERT INTO role_user (user_id, role_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:u, :r, :t, 1, :t, 1)"
                ),
                {"u": AGENT, "r": ROLE, "t": t},
            )
            await s.execute(
                text(
                    "INSERT INTO group_role (role_id, group_id, permission_key, permission_value,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:r, :g, 'ro', 1, :t, 1, :t, 1)"
                ),
                {"r": ROLE, "g": db.ids["stats_group"], "t": t},
            )
            await s.flush()
            await require_report_access(s, AGENT, strict)

            # an inactive user loses access although the grant is still there
            await s.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": AGENT2})
            await s.flush()
            with pytest.raises(HTTPException):
                await require_report_access(s, AGENT2, strict)

            # an invalid "stats" group grants nothing (admins keep access)
            stats = {"g": db.ids["stats_group"]}
            await s.execute(text("UPDATE permission_groups SET valid_id = 2 WHERE id = :g"), stats)
            await s.flush()
            for uid in (NOBODY, AGENT):
                with pytest.raises(HTTPException):
                    await require_report_access(s, uid, strict)
            await require_report_access(s, ADMIN, strict)

            # no group named "stats" at all
            await s.execute(
                text(
                    "UPDATE permission_groups SET valid_id = 1, name = 'rep47-gone' WHERE id = :g"
                ),
                stats,
            )
            await s.flush()
            with pytest.raises(HTTPException):
                await require_report_access(s, NOBODY, strict)
            await require_report_access(s, ADMIN, strict)
            await s.rollback()  # every change above was only in this transaction
    finally:
        await db.engine.dispose()


# ---------------------------------------------------------------- bounded output (fix round 1)

BOOKINGS = {"kind": "absolute", "start": "2024-05-01", "end": "2024-06-30"}  # 61 days


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_time_series_fill_never_exceeds_the_row_cap(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """days x series is only materialised when it fits the row cap."""
    db = await _db(request, url_fixture)
    per_ticket = {
        "source": "accounted_time",
        "dimensions": [{"key": "booked"}, {"key": "ticket"}],
        "measures": [{"key": "time_units"}],
        "time_range": BOOKINGS,
        "visualization": {"type": "line", "series_dimension": 1},
    }
    try:
        full = await db.run(per_ticket)
        assert len(full.rows) == 61 * 2 and full.truncated is False  # T1, T2 x 61 days
        assert sum(1 for row in full.rows if row[2]) == 4

        capped = await db.run(per_ticket, row_cap=100)
        assert capped.truncated is True and len(capped.rows) == 4  # grid refused, data kept
        assert all(row[2] for row in capped.rows)

        # one series over the longest allowed window: 3660 day buckets > row cap
        long = {
            "source": "tickets",
            "dimensions": [{"key": "created"}],
            "measures": [{"key": "tickets"}],
            "time_range": {"kind": "absolute", "start": "2015-01-01", "end": "2025-01-07"},
        }
        r = await db.run(long, row_cap=100)
        assert r.truncated is True and len(r.rows) <= 100
        assert sum(row[1] for row in r.rows) == 6
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_row_and_column_totals_only_for_returned_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    db = await _db(request, url_fixture)
    defn = {
        "source": "accounted_time",
        "dimensions": [{"key": "ticket"}, {"key": "booked", "grain": "month"}],
        "measures": [{"key": "time_units"}],
        "time_range": BOOKINGS,
        "totals": {"grand": True, "rows": True, "columns": True},
        "sort": [{"key": "time_units", "direction": "desc"}],
    }
    try:
        full = await db.run(defn, compare_previous=True)
        assert full.totals is not None and set(full.totals.rows) == {str(T1), str(T2)}
        assert set(full.totals.columns) == {"2024-05-01", "2024-06-01"}

        r = await db.run(defn, row_cap=1, compare_previous=True)
        assert r.rows == [[T1, "2024-06-01", 2.0]] and r.truncated is True
        assert r.totals is not None and r.previous is not None
        assert r.totals.grand == [None, None, 5.25]  # grand stays complete
        assert r.totals.rows == {str(T1): [T1, None, 3.5]}
        assert r.totals.columns == {"2024-06-01": [None, "2024-06-01", 2.5]}
        assert set(r.previous.rows) <= {str(T1)} and set(r.previous.columns) <= {"2024-06-01"}
        assert set(r.labels["ticket"]) == {str(T1)}  # no labels for rows not returned
    finally:
        await db.engine.dispose()


# ---------------------------------------------------------------- cancellation, budgets


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancelled_query_invalidates_the_connection(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await _db(request, url_fixture)
    defn = D({"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE})
    try:
        async with db.factory() as s:
            fairy = await (await s.connection()).get_raw_connection()
            real = s.execute

            async def cancelled(stmt: Any, *a: Any, **kw: Any) -> Any:
                if "tiqora_report_ticket_fact" in str(stmt):
                    raise asyncio.CancelledError  # client went away mid-query
                return await real(stmt, *a, **kw)

            monkeypatch.setattr(s, "execute", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await run_report(
                    s, defn=defn, params=UTC_PARAMS, user_id=AGENT, report_key=None,
                    reporting=ReportingSettings(), now=NOW,
                )  # fmt: skip
            assert fairy.is_valid is False
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_label_lookup_cancel_and_timeout_invalidate(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_bounded: cancellation and (job-mode) timeouts of label lookups drop the connection."""
    db = await _db(request, url_fixture)
    defn = D(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue"}],
            "measures": [{"key": "tickets"}],
            "time_range": JUNE,
        }
    )

    async def cancelled(*a: Any, **kw: Any) -> dict[str, str]:
        raise asyncio.CancelledError

    async def hanging(*a: Any, **kw: Any) -> dict[str, str]:
        await asyncio.sleep(30)
        return {}

    try:
        monkeypatch.setattr(engine_mod, "resolve_labels", cancelled)
        async with db.factory() as s:
            fairy = await (await s.connection()).get_raw_connection()
            with pytest.raises(asyncio.CancelledError):
                await run_report(
                    s, defn=defn, params=UTC_PARAMS, user_id=AGENT, report_key=None,
                    reporting=ReportingSettings(), now=NOW,
                )  # fmt: skip
            assert fairy.is_valid is False

        # job mode: label lookups are bounded by the per-query limit too
        monkeypatch.setattr(engine_mod, "resolve_labels", hanging)
        async with db.factory() as s:
            fairy = await (await s.connection()).get_raw_connection()
            with pytest.raises(ReportQueryTimeout):
                await run_report(
                    s, defn=defn, params=UTC_PARAMS, user_id=AGENT, report_key=None,
                    reporting=ReportingSettings(), now=NOW, budget=QueryBudget(per_query_seconds=1),
                )  # fmt: skip
            assert fairy.is_valid is False
    finally:
        await db.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_budget_none_means_job_mode_everywhere(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = await _db(request, url_fixture)
    seen: list[int] = []
    real_timeout = engine_mod.statement_timeout

    def recording(session: AsyncSession, seconds: int) -> Any:
        seen.append(seconds)
        return real_timeout(session, seconds)

    monkeypatch.setattr(engine_mod, "statement_timeout", recording)
    reporting = ReportingSettings(query_timeout_seconds=17)
    defn = D({"source": "tickets", "measures": [{"key": "tickets"}], "time_range": JUNE})
    try:
        async with db.factory() as s:
            plan = await plan_report(
                s, defn=defn, params=UTC_PARAMS, user_id=AGENT, reporting=reporting, now=NOW
            )
            assert await estimate_rows(s, plan.compiled, reporting=reporting) == 6
            await run_report(
                s, defn=defn, params=UTC_PARAMS, user_id=AGENT, report_key=None,
                reporting=reporting, now=NOW,
            )  # fmt: skip
        assert seen == [17, 17]  # estimate and main query: the same per-query job limit
    finally:
        await db.engine.dispose()
