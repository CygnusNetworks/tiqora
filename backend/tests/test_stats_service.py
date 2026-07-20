"""DB integration tests for :class:`tiqora.stats.service.StatsService`.

Seeds tickets/articles/history across an allowed and a denied queue and
asserts both the report aggregates and that data in a queue the caller
lacks ``ro`` on never leaks into any report.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2024, 6, 10, 12, 0, 0)

# Znuny fixture defaults (tests/fixtures/znuny-schema/initial_insert.*.sql):
# ticket_state 1=new(open-type), 4=open(open-type), 2=closed successful(closed-type)
STATE_OPEN = 4
STATE_CLOSED = 2
# article_sender_type 1=agent, 3=customer
SENDER_AGENT = 1
# ticket_history_type 1=NewTicket
HIST_NEW_TICKET = 1


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed(sync_url: str) -> dict[str, Any]:
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000

    t1_create = NOW - timedelta(days=5)
    t2_create = NOW - timedelta(days=3)
    t2_closed = NOW - timedelta(days=1)
    t3_create = NOW - timedelta(days=2)
    fr_time = t1_create + timedelta(hours=2)

    past_epoch = 1  # 1970 epoch second — always "breached" (<= now)

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :login, 'x', 'Read', 'Er', 1, :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "login": f"stats.alpha.{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:g1, :n1, 1, :t, 1, :t, 1), (:g2, :n2, 1, :t, 1, :t, 1)"
            ),
            {
                "g1": base + 10,
                "n1": f"stats-allowed-{ns}",
                "g2": base + 11,
                "n2": f"stats-denied-{ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "gid": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q1, :qn1, :g1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1),"
                " (:q2, :qn2, :g2, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {
                "q1": base + 20,
                "qn1": f"StatsAllowedQueue-{ns}",
                "g1": base + 10,
                "q2": base + 21,
                "qn2": f"StatsDeniedQueue-{ns}",
                "g2": base + 11,
                "t": NOW,
            },
        )
        # t1: open, allowed queue, escalated (past escalation timestamps)
        # t2: closed, allowed queue (closed via history row on t2_closed)
        # t3: open, denied queue — must never surface in any report
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES"
                " (:t1, :tn1, 'Escalated open', :q1, 1, 1, :uid, 1, 3, :st_open,"
                "  'CUST-STATS', 'alice@example.com', 0, 0, :pe, 0, :pe, 0, 0, :t1c, 1, :t1c, 1),"
                " (:t2, :tn2, 'Closed', :q1, 1, 1, :uid, 1, 3, :st_closed,"
                "  'CUST-STATS', 'alice@example.com', 0, 0, 0, 0, 0, 0, 0, :t2c, 1, :t2c, 1),"
                " (:t3, :tn3, 'Denied open', :q2, 1, 1, :uid, 1, 3, :st_open,"
                "  'CUST-STATS', 'alice@example.com', 0, 0, :pe, 0, :pe, 0, 0, :t3c, 1, :t3c, 1)"
            ),
            {
                "t1": base + 30,
                "tn1": f"T{base + 30}",
                "t2": base + 31,
                "tn2": f"T{base + 31}",
                "t3": base + 32,
                "tn3": f"T{base + 32}",
                "q1": base + 20,
                "q2": base + 21,
                "uid": base + 1,
                "st_open": STATE_OPEN,
                "st_closed": STATE_CLOSED,
                "pe": past_epoch,
                "t1c": t1_create,
                "t2c": t2_create,
                "t3c": t3_create,
            },
        )
        # First-response article on t1 (agent, customer-visible).
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:aid, :tid, :sender, 1, 1, :t, 1, :t, 1)"
            ),
            {"aid": base + 40, "tid": base + 30, "sender": SENDER_AGENT, "t": fr_time},
        )
        # Closed-state history row on t2 (solution time source).
        conn.execute(
            text(
                "INSERT INTO ticket_history (id, name, history_type_id, ticket_id, article_id,"
                " type_id, queue_id, owner_id, priority_id, state_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:hid, 'Updated: Old to New', :htype, :tid, NULL, 1, :qid, :uid, 3,"
                " :st_closed, :t, 1, :t, 1)"
            ),
            {
                "hid": base + 50,
                "htype": HIST_NEW_TICKET,
                "tid": base + 31,
                "qid": base + 20,
                "uid": base + 1,
                "st_closed": STATE_CLOSED,
                "t": t2_closed,
            },
        )
    engine.dispose()
    return {
        "user_id": base + 1,
        "login": f"stats.alpha.{ns}",
        "queue_id": base + 20,
        "denied_queue_id": base + 21,
        "t1": base + 30,
        "t2": base + 31,
        "t3": base + 32,
        "t1_create": t1_create,
        "t2_create": t2_create,
        "t2_closed": t2_closed,
        "state_open": STATE_OPEN,
    }


async def _session_factory(mariadb_znuny_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.db
@pytest.mark.asyncio
async def test_ticket_volume_and_permission_filtering(mariadb_znuny_url: str) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        points = await svc.ticket_volume(ids["user_id"], StatsFilters(), granularity="day")
    await engine.dispose()

    total_created = sum(p.created for p in points)
    total_closed = sum(p.closed for p in points)
    assert total_created == 2, "only the two allowed-queue tickets should be counted"
    assert total_closed == 1, "only t2's closed-state history row should be counted"


@pytest.mark.db
@pytest.mark.asyncio
async def test_open_snapshot_dimensions(mariadb_znuny_url: str) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        by_queue = await svc.open_snapshot(ids["user_id"], StatsFilters(), "queue")
        by_owner = await svc.open_snapshot(ids["user_id"], StatsFilters(), "owner")
        by_state = await svc.open_snapshot(ids["user_id"], StatsFilters(), "state")
    await engine.dispose()

    # Only t1 is open in the allowed queue (t2 closed, t3 in denied queue).
    assert [d.id for d in by_queue] == [ids["queue_id"]]
    assert by_queue[0].count == 1
    assert [d.id for d in by_owner] == [ids["user_id"]]
    assert [d.id for d in by_state] == [ids["state_open"]]


@pytest.mark.db
@pytest.mark.asyncio
async def test_sla_stats_escalation_and_distributions(mariadb_znuny_url: str) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        s = await svc.sla_stats(ids["user_id"], StatsFilters())
    await engine.dispose()

    assert s.total == 2  # t1 + t2 (t3 excluded by permission)
    assert s.escalated == 1  # only t1 has a past escalation_time
    assert s.first_response_breached == 1
    assert s.solution_breached == 0  # t1's escalation_solution_time is 0
    assert len(s.first_response_minutes) == 1
    assert s.first_response_minutes[0] == pytest.approx(120.0, abs=0.5)  # 2h article delay
    assert len(s.solution_minutes) == 1  # t2's closed-history sample
    assert s.solution_minutes[0] == pytest.approx(2 * 24 * 60, abs=1)  # 2 days later


@pytest.mark.db
@pytest.mark.asyncio
async def test_agent_workload(mariadb_znuny_url: str) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        workload = await svc.agent_workload(ids["user_id"], StatsFilters())
    await engine.dispose()

    assert len(workload) == 1
    item = workload[0]
    assert item.user_id == ids["user_id"]
    assert item.owned_open == 1  # t1 only (t2 closed, t3 denied queue)
    assert item.closed_in_period == 1  # t2's history row


@pytest.mark.db
@pytest.mark.asyncio
async def test_agent_workload_excludes_invalid_agents(mariadb_znuny_url: str) -> None:
    """A soft-invalidated agent (valid_id != 1) who still owns an open ticket
    in an allowed queue must not appear in the workload report."""
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    invalid_uid = ids["user_id"] + 500
    invalid_ticket = ids["t1"] + 500

    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :login, 'x', 'Gone', 'Agent', 2, :t, 1, :t, 1)"
            ),
            {"uid": invalid_uid, "login": f"stats.invalid.{invalid_uid}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :tn, 'Owned by invalid', :qid, 1, 1, :uid, 1, 3, :st_open,"
                "  'CUST-STATS', 'alice@example.com', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "tid": invalid_ticket,
                "tn": f"T{invalid_ticket}",
                "qid": ids["queue_id"],
                "uid": invalid_uid,
                "st_open": ids["state_open"],
                "t": ids["t1_create"],
            },
        )
    engine.dispose()

    engine2, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        workload = await svc.agent_workload(ids["user_id"], StatsFilters())
    await engine2.dispose()

    owners = {item.user_id for item in workload}
    assert invalid_uid not in owners
    assert ids["user_id"] in owners


@pytest.mark.db
@pytest.mark.asyncio
async def test_backlog_trend_nonnegative_and_filtered(mariadb_znuny_url: str) -> None:
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = StatsService(session)
        points = await svc.backlog_trend(ids["user_id"], StatsFilters(), granularity="day")
    await engine.dispose()

    assert points, "expected at least one bucket"
    assert all(p.open_count >= 0 for p in points)
    # created 2 - closed 1 = 1 net open ticket by the last bucket.
    assert points[-1].open_count == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_no_permission_returns_empty(mariadb_znuny_url: str) -> None:
    """A user with no ``ro`` grants sees zero rows across every report."""
    from tiqora.stats.service import StatsFilters, StatsService

    ids = _seed(mariadb_znuny_url)
    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    unrelated_user_id = ids["user_id"] + 999_999  # no group_user rows at all
    async with factory() as session:
        svc = StatsService(session)
        assert await svc.ticket_volume(unrelated_user_id, StatsFilters()) == []
        assert await svc.open_snapshot(unrelated_user_id, StatsFilters(), "queue") == []
        sla = await svc.sla_stats(unrelated_user_id, StatsFilters())
        assert sla.total == 0
        assert await svc.agent_workload(unrelated_user_id, StatsFilters()) == []
        assert await svc.backlog_trend(unrelated_user_id, StatsFilters()) == []
    await engine.dispose()
