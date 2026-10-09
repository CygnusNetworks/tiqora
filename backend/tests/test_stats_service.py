"""DB integration tests for :class:`tiqora.stats.service.StatsService` (both dialects).

The service is a thin wrapper over the report engine, so the seed has realistic
histories (``NewTicket`` + ``StateUpdate`` rows) and the report data (facts and events)
is rebuilt for the seeded tickets before anything is asserted.

Seed ids 47870-47899 (names contain ``rep47``): agents 47870 (reads queue 47871) and
47871 (invalid), groups/queues 47871 (allowed) and 47872 (denied), SLA 47870 (first
response within 60 minutes), tickets 47880-47885, history rows 47890-47899, articles
47890-47891. Allowed queue, all created in June 2024::

    T1 47880 open, owner 47870, answered after 2 h (no SLA), past escalation timestamps
    T2 47881 closed once (State update), never answered
    T3 47882 open, DENIED queue - must never surface
    T4 47883 open, owned by the invalid agent 47871
    T5 47884 closed once, then a note was added (history row with the closed state)
    T6 47885 closed once, SLA 47870, answered after 3 h (breach), escalation columns zeroed

Every row this module writes is deleted again (incl. the report state row).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.sql import ReportQueryTimeout

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 10, 12, 0, 0)
LO, HI = 47870, 47899
READER, GONE = 47870, 47871
Q_ALLOWED, Q_DENIED = 47871, 47872
SLA = 47870
T1, T2, T3, T4, T5, T6 = 47880, 47881, 47882, 47883, 47884, 47885
TICKETS = (T1, T2, T3, T4, T5, T6)
CUSTOMER = "rep47-cust-stats"
LOGIN = "rep47.stats"
# Znuny fixture defaults: ticket_state 4 = open (open-type), 2 = closed successful (closed-type)
STATE_OPEN = 4
STATE_CLOSED = 2
SENDER_AGENT = 1
_BAND = {"lo": LO, "hi": HI}
_RESET = (
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM sla WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: dict[str, dict[str, Any]] = {}


def to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
    engine.dispose()


def reset_state(sync_url: str) -> None:
    """Drop the report state row and ``reporting.*`` settings (per-test isolation)."""
    run_sql(sync_url, _RESET)


def cleanup_all() -> None:
    for url in _URLS:
        run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    cleanup_all()


def _one(conn: Any, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _seed_rows(sync_url: str) -> dict[str, Any]:
    t1_create = NOW - timedelta(days=5)
    t2_create = NOW - timedelta(days=3)
    t3_create = NOW - timedelta(days=2)
    t5_create = NOW - timedelta(days=4)
    t6_create = NOW - timedelta(days=6)
    t2_closed = NOW - timedelta(days=1)
    t5_closed = NOW - timedelta(days=2)
    t5_note = NOW - timedelta(days=1)  # same day as T2's close
    t6_closed = NOW - timedelta(days=5)
    past_epoch = 1  # 1970 epoch second - always "breached" (<= now)

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        h_new = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        h_state = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
        h_note = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'AddNote'")
        for uid, login, first, last, valid in (
            (READER, LOGIN, "Read", "Er", 1),
            (GONE, "rep47.stats.gone", "Gone", "Agent", 2),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :login, 'x', :f, :l, :v, :t, 1, :t, 1)"
                ),
                {"uid": uid, "login": login, "f": first, "l": last, "v": valid, "t": NOW},
            )
        for gid in (Q_ALLOWED, Q_DENIED):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-group-{gid}", "t": NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :id, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-queue-{gid}", "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": READER, "g": Q_ALLOWED, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO sla (id, name, calendar_name, first_response_time, update_time,"
                " solution_time, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-sla', NULL, 60, 0, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": SLA, "t": NOW},
        )
        # (id, queue, owner, state, sla, created, escalation timestamp)
        tickets = (
            (T1, Q_ALLOWED, READER, STATE_OPEN, None, t1_create, past_epoch),
            (T2, Q_ALLOWED, READER, STATE_CLOSED, None, t2_create, 0),
            (T3, Q_DENIED, READER, STATE_OPEN, None, t3_create, past_epoch),
            (T4, Q_ALLOWED, GONE, STATE_OPEN, None, t1_create, 0),
            (T5, Q_ALLOWED, READER, STATE_CLOSED, None, t5_create, 0),
            (T6, Q_ALLOWED, READER, STATE_CLOSED, SLA, t6_create, 0),
        )
        for tid, queue, owner, state, sla, created, esc in tickets:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " sla_id, customer_id, customer_user_id, timeout, until_time,"
                    " escalation_time, escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, :sla, :c, :cu,"
                    " 0, 0, :esc, 0, :esc, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2024060{tid}", "title": f"rep47 stats {tid}", "q": queue,
                 "u": owner, "s": state, "sla": sla, "c": CUSTOMER,
                 "cu": "rep47.stats@example.org", "esc": esc, "t": created},
            )  # fmt: skip
        # (id, ticket, history type, state after the action, at)
        history = (
            (47890, T1, h_new, STATE_OPEN, t1_create),
            (47891, T2, h_new, STATE_OPEN, t2_create),
            (47892, T2, h_state, STATE_CLOSED, t2_closed),
            (47893, T3, h_new, STATE_OPEN, t3_create),
            (47894, T4, h_new, STATE_OPEN, t1_create),
            (47895, T5, h_new, STATE_OPEN, t5_create),
            (47896, T5, h_state, STATE_CLOSED, t5_closed),
            (47897, T5, h_note, STATE_CLOSED, t5_note),  # a note on the closed ticket
            (47898, T6, h_new, STATE_OPEN, t6_create),
            (47899, T6, h_state, STATE_CLOSED, t6_closed),
        )
        queue_of = {t[0]: t[1] for t in tickets}
        owner_of = {t[0]: t[2] for t in tickets}
        for hid, tid, htype, state, at in history:
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, 1, :t, 1)"
                ),
                {"id": hid, "n": f"%%rep47 history {hid}", "ht": htype, "tid": tid,
                 "q": queue_of[tid], "o": owner_of[tid], "s": state, "t": at},
            )  # fmt: skip
        # first responses (agent, visible for the customer): T1 after 2 h, T6 after 3 h
        for aid, tid, at in (
            (47890, T1, t1_create + timedelta(hours=2)),
            (47891, T6, t6_create + timedelta(hours=3)),
        ):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :sender, 1, 1, :t, 1, :t, 1)"
                ),
                {"id": aid, "tid": tid, "sender": SENDER_AGENT, "t": at},
            )
    engine.dispose()
    return {
        "user_id": READER,
        "login": LOGIN,
        "queue_id": Q_ALLOWED,
        "denied_queue_id": Q_DENIED,
        "state_open": STATE_OPEN,
        "state_closed": STATE_CLOSED,
        "gone_user_id": GONE,
        "t1_create": t1_create,
        "t2_closed": t2_closed,
        "t5_closed": t5_closed,
        "t6_closed": t6_closed,
    }


async def seed(sync_url: str) -> dict[str, Any]:
    """Seed once per module and dialect, rebuild the report data of the tickets."""
    _URLS.add(sync_url)
    if sync_url not in _SEEDED:
        run_sql(sync_url, _CLEANUP)
        ids = _seed_rows(sync_url)
        engine = create_async_engine(to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await rebuild_ticket_facts(session, TICKETS, now=NOW)
            await session.commit()
        await engine.dispose()
        _SEEDED[sync_url] = ids
    reset_state(sync_url)
    return _SEEDED[sync_url]


@dataclass
class Env:
    sync_url: str
    ids: dict[str, Any]
    factory: async_sessionmaker[AsyncSession]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    ids = await seed(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    try:
        yield Env(
            sync_url, ids, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        )
    finally:
        await engine.dispose()
        reset_state(sync_url)


def _day(value: datetime) -> str:
    return value.date().isoformat()


async def test_ticket_volume_and_permission_filtering(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        points = await StatsService(session).ticket_volume(
            env.ids["user_id"], StatsFilters(), granularity="day"
        )

    # T1, T2, T4, T5, T6 are created in the allowed queue; T3 (denied queue) never shows
    assert sum(p.created for p in points) == 5
    # one close per transition into a closed state: T2, T5, T6
    assert sum(p.closed for p in points) == 3
    assert [p.bucket for p in points] == sorted(p.bucket for p in points)
    assert all(p.created or p.closed for p in points), "only buckets with data"


async def test_volume_counts_one_close_per_transition(env: Env) -> None:
    """T5 is closed once and gets a note afterwards: closed == 1 (the old code counted 2)."""
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        points = await StatsService(session).ticket_volume(
            env.ids["user_id"], StatsFilters(), granularity="day"
        )
    closed_on = {p.bucket: p.closed for p in points}
    # T2 closes on the day T5 gets its note (history row with the closed state)
    assert closed_on[_day(env.ids["t2_closed"])] == 1
    assert closed_on[_day(env.ids["t5_closed"])] == 1
    assert closed_on[_day(env.ids["t6_closed"])] == 1
    assert sum(closed_on.values()) == 3


async def test_volume_granularity_week_and_month(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        svc = StatsService(session)
        weeks = await svc.ticket_volume(env.ids["user_id"], StatsFilters(), "week")
        months = await svc.ticket_volume(env.ids["user_id"], StatsFilters(), "month")
    assert all(date.fromisoformat(p.bucket).weekday() == 0 for p in weeks)  # Mondays
    assert [(p.bucket, p.created, p.closed) for p in months] == [("2024-06-01", 5, 3)]


async def test_volume_date_range_and_filters(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        svc = StatsService(session)
        # only T2 is created on 06-07
        window = StatsFilters(date_from=datetime(2024, 6, 7), date_to=datetime(2024, 6, 7, 23, 59))
        in_window = await svc.ticket_volume(env.ids["user_id"], window)
        closed_now = await svc.ticket_volume(
            env.ids["user_id"], StatsFilters(state_id=env.ids["state_closed"])
        )
        other_cust = await svc.ticket_volume(env.ids["user_id"], StatsFilters(customer_id="nobody"))
        inverted = await svc.ticket_volume(
            env.ids["user_id"],
            StatsFilters(date_from=datetime(2024, 6, 8), date_to=datetime(2024, 6, 7)),
        )
    assert [(p.bucket, p.created) for p in in_window] == [("2024-06-07", 1)]
    # the current ticket state filters both sides: T2, T5, T6 are closed now
    assert sum(p.created for p in closed_now) == 3
    assert sum(p.closed for p in closed_now) == 3
    assert other_cust == []
    assert inverted == []


async def test_open_snapshot_dimensions(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    uid = env.ids["user_id"]
    async with env.factory() as session:
        svc = StatsService(session)
        by_queue = await svc.open_snapshot(uid, StatsFilters(), "queue")
        by_owner = await svc.open_snapshot(uid, StatsFilters(), "owner")
        by_state = await svc.open_snapshot(uid, StatsFilters(), "state")
        by_priority = await svc.open_snapshot(uid, StatsFilters(), "priority")

    # T1 and T4 are open in the allowed queue (T2/T5/T6 closed, T3 in the denied queue)
    assert [(d.id, d.count) for d in by_queue] == [(env.ids["queue_id"], 2)]
    assert by_queue[0].label == f"rep47-queue-{Q_ALLOWED}"
    assert {(d.id, d.count) for d in by_owner} == {(uid, 1), (env.ids["gone_user_id"], 1)}
    assert [(d.id, d.count) for d in by_state] == [(env.ids["state_open"], 2)]
    assert [d.count for d in by_priority] == [2]


async def test_open_snapshot_owner_dimension_labels(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        by_owner = await StatsService(session).open_snapshot(
            env.ids["user_id"], StatsFilters(), "owner"
        )
    labels = {d.id: d.label for d in by_owner}
    assert labels[env.ids["user_id"]] == "Read Er"  # "<First> <Last>", the login before
    assert labels[env.ids["gone_user_id"]] == "Gone Agent"


async def test_open_snapshot_ignores_dates_and_honours_filters(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    uid = env.ids["user_id"]
    async with env.factory() as session:
        svc = StatsService(session)
        dated = await svc.open_snapshot(uid, StatsFilters(date_from=datetime(2030, 1, 1)), "queue")
        wrong_queue = await svc.open_snapshot(
            uid, StatsFilters(queue_id=env.ids["denied_queue_id"]), "queue"
        )
        closed_filter = await svc.open_snapshot(
            uid, StatsFilters(state_id=env.ids["state_closed"]), "queue"
        )
    assert [d.count for d in dated] == [2], "tickets open now, whatever the dates say"
    assert wrong_queue == [], "a queue the caller may not read stays empty"
    assert closed_filter == []


async def test_sla_stats_escalation_and_distributions(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        s = await StatsService(session).sla_stats(env.ids["user_id"], StatsFilters())

    assert s.total == 5  # T1, T2, T4, T5, T6 (T3 excluded by permission)
    assert s.escalated == 1  # open tickets created in the window: only T1 is escalated
    assert s.first_response_breached == 1  # T6: SLA target 60 minutes, answered after 180
    assert s.update_breached == 0
    assert s.solution_breached == 0
    # clock minutes creation -> first agent answer: T1 2 h, T6 3 h
    assert sorted(s.first_response_minutes) == pytest.approx([120.0, 180.0], abs=0.5)
    # creation -> LAST close (closed tickets only): T6 1 day, T2 and T5 2 days
    assert sorted(s.solution_minutes) == pytest.approx([1440.0, 2880.0, 2880.0], abs=1)


async def test_sla_breaches_survive_close(env: Env) -> None:
    """T6 was answered after its first-response target and closed (escalation columns zeroed)."""
    from tiqora.stats.service import StatsFilters, StatsService

    engine = create_engine(env.sync_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT escalation_time, escalation_response_time, escalation_solution_time,"
                " ticket_state_id FROM ticket WHERE id = :t"
            ),
            {"t": T6},
        ).one()
    engine.dispose()
    assert tuple(row) == (0, 0, 0, STATE_CLOSED), "the old code could not see this breach"

    async with env.factory() as session:
        svc = StatsService(session)
        s = await svc.sla_stats(env.ids["user_id"], StatsFilters())
        only_t6 = await svc.sla_stats(
            env.ids["user_id"],
            StatsFilters(date_from=datetime(2024, 6, 4), date_to=datetime(2024, 6, 4, 23, 59, 59)),
        )
    assert s.first_response_breached == 1
    assert (only_t6.total, only_t6.first_response_breached) == (1, 1)


async def test_sla_stats_filters(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    uid = env.ids["user_id"]
    async with env.factory() as session:
        svc = StatsService(session)
        closed_only = await svc.sla_stats(uid, StatsFilters(state_id=env.ids["state_closed"]))
        none = await svc.sla_stats(uid, StatsFilters(customer_id="nobody"))
        inverted = await svc.sla_stats(
            uid, StatsFilters(date_from=datetime(2024, 6, 8), date_to=datetime(2024, 6, 7))
        )
    assert closed_only.total == 3
    assert closed_only.escalated == 0  # escalated = open tickets only
    assert (none.total, none.first_response_minutes) == (0, [])
    assert (inverted.total, inverted.solution_minutes) == (0, [])


async def test_agent_workload(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        workload = await StatsService(session).agent_workload(env.ids["user_id"], StatsFilters())

    assert len(workload) == 1  # the invalid agent owning T4 is excluded
    item = workload[0]
    assert item.user_id == env.ids["user_id"]
    assert (item.login, item.name) == (LOGIN, "Read Er")
    assert item.owned_open == 1  # T1 only (T3 denied queue, T2/T5/T6 closed)
    assert item.closed_in_period == 3  # T2, T5, T6 are closed and were closed in the period


async def test_agent_workload_excludes_invalid_agents(env: Env) -> None:
    """A soft-invalidated agent (valid_id != 1) who still owns an open ticket in an allowed
    queue (T4) must not appear in the workload report."""
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        svc = StatsService(session)
        workload = await svc.agent_workload(env.ids["user_id"], StatsFilters())
        snapshot = await svc.open_snapshot(env.ids["user_id"], StatsFilters(), "owner")

    owners = {item.user_id for item in workload}
    assert env.ids["gone_user_id"] not in owners
    assert env.ids["user_id"] in owners
    assert env.ids["gone_user_id"] in {d.id for d in snapshot}, "the snapshot still counts T4"


async def test_agent_workload_date_range(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        # only T6 closes on 06-05
        one_day = await StatsService(session).agent_workload(
            env.ids["user_id"],
            StatsFilters(date_from=datetime(2024, 6, 5), date_to=datetime(2024, 6, 5, 23, 59, 59)),
        )
    assert [(w.owned_open, w.closed_in_period) for w in one_day] == [(1, 1)]


async def test_backlog_trend_nonnegative_and_filtered(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    async with env.factory() as session:
        points = await StatsService(session).backlog_trend(
            env.ids["user_id"], StatsFilters(), granularity="day"
        )

    assert points, "expected at least one bucket"
    assert all(p.open_count >= 0 for p in points)
    # created 5 - closed 3 = 2 net open tickets by the last bucket (T1, T4)
    assert points[-1].open_count == 2


async def test_no_permission_returns_empty(env: Env) -> None:
    """A user with no ``ro`` grants sees zero rows across every report."""
    from tiqora.stats.service import StatsFilters, StatsService

    unrelated_user_id = env.ids["user_id"] + 100_000  # no group_user rows at all
    async with env.factory() as session:
        svc = StatsService(session)
        assert await svc.ticket_volume(unrelated_user_id, StatsFilters()) == []
        assert await svc.open_snapshot(unrelated_user_id, StatsFilters(), "queue") == []
        sla = await svc.sla_stats(unrelated_user_id, StatsFilters())
        assert (sla.total, sla.escalated, sla.first_response_minutes) == (0, 0, [])
        assert await svc.agent_workload(unrelated_user_id, StatsFilters()) == []
        assert await svc.backlog_trend(unrelated_user_id, StatsFilters()) == []


class _CountingBudget(QueryBudget):
    """Counts the statements/lookups that ask the budget for their time."""

    calls = 0

    def statement_seconds(self) -> int:
        type(self).calls += 1
        return super().statement_seconds()


async def test_every_engine_call_uses_the_request_budget(env: Env) -> None:
    """``budget=None`` would mean job mode: every query of a request must take the shared one."""
    from tiqora.stats.service import StatsFilters, StatsService

    _CountingBudget.calls = 0
    budget = _CountingBudget(per_query_seconds=60, deadline=1e12, clock=lambda: 0.0)
    async with env.factory() as session:
        await StatsService(session, budget=budget).sla_stats(env.ids["user_id"], StatsFilters())
    # kpis, escalated, two raw percentile queries - each fetch asks the budget once
    assert _CountingBudget.calls >= 4


async def test_used_up_budget_raises_report_query_timeout(env: Env) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    spent = QueryBudget(per_query_seconds=60, deadline=-1.0, clock=lambda: 0.0)
    async with env.factory() as session:
        svc = StatsService(session, budget=spent)
        with pytest.raises(ReportQueryTimeout):
            await svc.ticket_volume(env.ids["user_id"], StatsFilters())
        with pytest.raises(ReportQueryTimeout):
            await svc.open_snapshot(env.ids["user_id"], StatsFilters(), "queue")


def test_wrappers_return_every_row_no_top_n() -> None:
    """The built-in ``open_now`` truncates to 15 plus ``__other__``; the wrapper copy does not."""
    from tiqora.extensions.core import get_registry
    from tiqora.reporting.catalogue import BUILTIN_REPORTS
    from tiqora.stats.service import _builtin

    original = get_registry().get(BUILTIN_REPORTS, "builtin.open_now")
    assert original is not None
    assert original.definition.dimensions[0].top_n == 15
    copy = _builtin("builtin.open_now")
    assert [d.top_n for d in copy.dimensions] == [None]
    assert original.definition.dimensions[0].top_n == 15, "the catalogue itself is untouched"


def test_window_and_extra_filter_mapping() -> None:
    from tiqora.stats.service import (
        _EVENT_FIELDS,
        _TICKET_FIELDS,
        StatsFilters,
        _extra_filter,
        _window,
    )

    now = datetime(2026, 10, 9, 12, 0)
    default = _window(StatsFilters(), now)
    assert default is not None
    assert (default.end_utc, default.start_utc) == (now, now - timedelta(days=3660))

    # exclusive end = date_to + 1 microsecond; local dates follow the time zone
    berlin = _window(
        StatsFilters(
            date_from=datetime(2024, 6, 9, 22, 0),
            date_to=datetime(2024, 6, 10, 21, 59, 59, 999999),
            time_zone="Europe/Berlin",
        ),
        now,
    )
    assert berlin is not None
    assert berlin.end_utc == datetime(2024, 6, 10, 22, 0)
    assert (berlin.start_date, berlin.end_date) == (date(2024, 6, 10), date(2024, 6, 10))
    assert _window(StatsFilters(date_from=now, date_to=now - timedelta(days=1)), now) is None

    assert _extra_filter(StatsFilters(), _TICKET_FIELDS) is None
    full = StatsFilters(state_id=4, priority_id=3, type_id=1, customer_id="c")
    flt = _extra_filter(full, _EVENT_FIELDS)
    assert flt is not None
    dumped = flt.model_dump(by_alias=True, exclude_none=True)
    assert dumped["where"]["all"] == [
        {"field": "ticket_state", "op": "eq", "value": 4},
        {"field": "ticket_priority", "op": "eq", "value": 3},
        {"field": "ticket_type", "op": "eq", "value": 1},
        {"field": "customer", "op": "eq", "value": "c"},
    ]
    single = _extra_filter(StatsFilters(state_id=2), _TICKET_FIELDS)
    assert single is not None
    assert single.model_dump(by_alias=True, exclude_none=True)["where"] == {
        "field": "state",
        "op": "eq",
        "value": 2,
    }
