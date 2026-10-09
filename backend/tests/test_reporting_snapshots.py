"""Daily snapshots: pure timeline/count logic and both-dialect writing.

Seed ids: 47580-47619 (names contain ``rep47``). Layout: group 47580, queue 47581,
agent 47583, tickets 47590 (A, stays open, priority 3 -> 5) and 47591 (B, closed).
Every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraReportDailySnapshot
from tiqora.reporting.facts_store import rebuild_and_commit
from tiqora.reporting.history_events import EventRow
from tiqora.reporting.snapshots import (
    compute_snapshot_counts,
    day_end_instants,
    snapshot_days,
    ticket_timeline,
)

LO, HI = 47580, 47619
GROUP, QUEUE, AGENT = 47580, 47581, 47583
T_A, T_B = 47590, 47591
T0 = datetime(2024, 6, 1, 8, 0, 0)
UTC_ZONE = "UTC"

_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)


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


def _ev(
    kind: str,
    at: datetime,
    *,
    queue: int = 1,
    to_id: int | None = None,
    from_id: int | None = None,
    to_value: str | None = None,
    hid: int | None = None,
) -> EventRow:
    return EventRow(
        ticket_id=1,
        kind=kind,
        occurred_at=at,
        queue_id=queue,
        history_id=hid,
        from_id=from_id,
        to_id=to_id,
        to_value=to_value,
    )


# ---------------------------------------------------------------- pure


def test_day_end_instants_across_dst() -> None:
    ends = day_end_instants(
        [date(2024, 3, 30), date(2024, 3, 31), date(2024, 10, 27)], "Europe/Berlin"
    )
    assert ends[date(2024, 3, 30)] == datetime(2024, 3, 30, 23, 0)  # midnight CET
    assert ends[date(2024, 3, 31)] == datetime(2024, 3, 31, 22, 0)  # midnight CEST
    assert ends[date(2024, 10, 27)] == datetime(2024, 10, 27, 23, 0)


def _life() -> list[EventRow]:
    return [
        _ev("create", T0, queue=1, to_id=1, to_value="new"),
        _ev("queue", T0 + timedelta(days=1), queue=2, from_id=1, to_id=2, hid=1),
        _ev("state", T0 + timedelta(days=2), queue=2, to_value="pending reminder", hid=2),
        _ev("priority", T0 + timedelta(days=3), queue=2, from_id=3, to_id=4, hid=3),
        _ev("state", T0 + timedelta(days=4), queue=2, to_value="closed", hid=4),
    ]


def test_timeline_segments() -> None:
    segs = ticket_timeline(T0, _life())
    assert len(segs) == 5
    assert segs[0].start == T0 and segs[-1].end is None
    assert segs[-1].state_type == "closed"
    for a, b in zip(segs, segs[1:], strict=False):
        assert a.end == b.start
    assert [(s.queue_id, s.state_type, s.priority_id) for s in segs] == [
        (1, "new", 3),
        (2, "new", 3),
        (2, "pending reminder", 3),
        (2, "pending reminder", 4),
        (2, "closed", 4),
    ]


def test_timeline_unknown_state_and_default_priority() -> None:
    segs = ticket_timeline(
        T0,
        [_ev("create", T0, to_id=7, to_value=None), _ev("state", T0 + timedelta(hours=1))],
        default_priority=5,
    )
    assert [(s.queue_id, s.state_type, s.priority_id) for s in segs] == [
        (7, "unknown", 5),
        (7, "unknown", 5),
    ]


def _ticket_a() -> tuple[datetime, list[EventRow]]:
    created = datetime(2024, 6, 1, 8, 0)
    return created, [
        _ev("create", created, queue=5, to_id=5, to_value="new"),
        _ev("state", datetime(2024, 6, 5, 10, 0), queue=5, to_value="closed", hid=1),
    ]


def _ticket_b() -> tuple[datetime, list[EventRow]]:
    created = datetime(2024, 6, 4, 12, 0)
    return created, [
        _ev("create", created, queue=5, to_id=5, to_value="new"),
        _ev("state", created, queue=5, to_value="pending reminder", hid=2),
    ]


def test_counts_open_only_and_age_buckets() -> None:
    days = [date(2024, 6, d) for d in range(1, 7)]
    counts = compute_snapshot_counts([_ticket_a(), _ticket_b()], day_end_instants(days, UTC_ZONE))
    assert counts[(date(2024, 6, 1), 5, "new", 3)] == [1, 1, 0, 0, 0, 0]
    # 06-04: A is 3.67 days old (R-T7AGE) -> age_3_7d; B (12 h) is pending
    assert counts[(date(2024, 6, 4), 5, "new", 3)] == [1, 0, 0, 1, 0, 0]
    assert counts[(date(2024, 6, 4), 5, "pending reminder", 3)] == [1, 1, 0, 0, 0, 0]
    # 06-05: A closed -> only B (36 h old)
    assert counts[(date(2024, 6, 5), 5, "pending reminder", 3)] == [1, 0, 1, 0, 0, 0]
    assert (date(2024, 6, 5), 5, "new", 3) not in counts
    # 06-06: B is 60 h old
    assert counts[(date(2024, 6, 6), 5, "pending reminder", 3)] == [1, 0, 1, 0, 0, 0]
    assert not any(k[2] == "closed" for k in counts)


def test_age_bucket_boundaries_are_exclusive_upper_bounds() -> None:
    created = datetime(2024, 6, 1, 0, 0)
    ev = [_ev("create", created, to_id=1, to_value="open")]
    days = [date(2024, 6, 1), date(2024, 6, 2), date(2024, 6, 3), date(2024, 6, 7)]
    counts = compute_snapshot_counts([(created, ev)], day_end_instants(days, UTC_ZONE))
    # ages at day end: 1.0, 2.0, 3.0, 7.0 days
    assert counts[(date(2024, 6, 1), 1, "open", 3)][1:] == [0, 1, 0, 0, 0]
    assert counts[(date(2024, 6, 2), 1, "open", 3)][1:] == [0, 1, 0, 0, 0]
    assert counts[(date(2024, 6, 3), 1, "open", 3)][1:] == [0, 0, 1, 0, 0]
    assert counts[(date(2024, 6, 7), 1, "open", 3)][1:] == [0, 0, 0, 1, 0]


def test_ticket_reopened_counts_again() -> None:
    created = datetime(2024, 6, 1, 8, 0)
    ev = [
        _ev("create", created, to_id=1, to_value="new"),
        _ev("state", datetime(2024, 6, 2, 10, 0), to_value="closed", hid=1),
        _ev("state", datetime(2024, 6, 3, 10, 0), to_value="open", hid=2),
    ]
    days = [date(2024, 6, 1), date(2024, 6, 2), date(2024, 6, 3)]
    counts = compute_snapshot_counts([(created, ev)], day_end_instants(days, UTC_ZONE))
    assert {k[0] for k in counts} == {date(2024, 6, 1), date(2024, 6, 3)}  # absent on 06-02
    assert counts[(date(2024, 6, 3), 1, "open", 3)][0] == 1


def test_ticket_created_after_day_end_not_counted() -> None:
    created = datetime(2024, 6, 2, 8, 0)
    ev = [_ev("create", created, to_id=1, to_value="new")]
    counts = compute_snapshot_counts(
        [(created, ev)], day_end_instants([date(2024, 6, 1), date(2024, 6, 2)], UTC_ZONE)
    )
    assert set(counts) == {(date(2024, 6, 2), 1, "new", 3)}


def test_event_exactly_at_day_end_belongs_to_next_day() -> None:
    created = datetime(2024, 6, 1, 8, 0)
    ev = [
        _ev("create", created, to_id=1, to_value="new"),
        _ev("state", datetime(2024, 6, 2, 0, 0), to_value="closed", hid=1),
    ]
    days = [date(2024, 6, 1), date(2024, 6, 2)]
    counts = compute_snapshot_counts([(created, ev)], day_end_instants(days, UTC_ZONE))
    # closed at exactly 06-02 00:00 -> still open at the end of 06-01, closed on 06-02
    assert set(counts) == {(date(2024, 6, 1), 1, "new", 3)}
    # created exactly at the day end -> not counted for that day, counted for the next
    at = datetime(2024, 6, 2, 0, 0)
    counts = compute_snapshot_counts(
        [(at, [_ev("create", at, to_id=1, to_value="new")])], day_end_instants(days, UTC_ZONE)
    )
    assert set(counts) == {(date(2024, 6, 2), 1, "new", 3)}


def test_merged_state_is_not_open() -> None:
    created = datetime(2024, 6, 1, 8, 0)
    ev = [
        _ev("create", created, to_id=1, to_value="new"),
        _ev("state", datetime(2024, 6, 2, 10, 0), to_value="merged", hid=1),
    ]
    days = [date(2024, 6, 1), date(2024, 6, 2), date(2024, 6, 3)]
    counts = compute_snapshot_counts([(created, ev)], day_end_instants(days, UTC_ZONE))
    assert set(counts) == {(date(2024, 6, 1), 1, "new", 3)}


# ---------------------------------------------------------------- DB


def _one(conn: Any, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar_one())


def _seed(sync_url: str) -> None:
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        h_new = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        h_prio = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'PriorityUpdate'")
        h_state = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
        st_new = _one(conn, "SELECT id FROM ticket_state WHERE name = 'new'")
        st_closed = _one(conn, "SELECT id FROM ticket_state WHERE name = 'closed successful'")
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.snap', 'x', 'Sven', 'Snap', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-snap-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-snap-q', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": T0},
        )

        def ticket(tid: int, state: int, prio: int, created: datetime) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'rep47 snap', :q, 1, 1, :u, 1, :p, :s,"
                    " 'rep47-cust', 'rep47.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"20240601{tid}",
                    "q": QUEUE,
                    "u": AGENT,
                    "p": prio,
                    "s": state,
                    "t": created,
                },
            )

        def hist(
            hid: int, tid: int, htype: int, name: str, state: int, prio: int, at: datetime
        ) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, :p, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": hid,
                    "n": name,
                    "ht": htype,
                    "tid": tid,
                    "q": QUEUE,
                    "o": AGENT,
                    "p": prio,
                    "s": state,
                    "t": at,
                },
            )

        b_created = datetime(2024, 6, 2, 10, 0)
        ticket(T_A, st_new, 5, T0)
        ticket(T_B, st_closed, 3, b_created)
        hist(47590, T_A, h_new, f"%%tn%%rep47-snap-q%%3 normal%%new%%{T_A}", st_new, 3, T0)
        hist(
            47591,
            T_A,
            h_prio,
            "%%3 normal%%3%%5 very high%%5",
            st_new,
            5,
            datetime(2024, 6, 2, 9, 0),
        )
        hist(47592, T_B, h_new, f"%%tn%%rep47-snap-q%%3 normal%%new%%{T_B}", st_new, 3, b_created)
        hist(
            47593,
            T_B,
            h_state,
            "%%new%%closed successful",
            st_closed,
            3,
            datetime(2024, 6, 2, 12, 0),
        )
    engine.dispose()


async def _rows(session: AsyncSession) -> dict[tuple[date, str, int], tuple[int, ...]]:
    res = await session.execute(
        select(TiqoraReportDailySnapshot).where(TiqoraReportDailySnapshot.queue_id == QUEUE)
    )
    return {
        (r.snapshot_date, r.state_type, r.priority_id): (
            r.open_count,
            r.age_0_1d,
            r.age_1_3d,
            r.age_3_7d,
            r.age_7_30d,
            r.age_30d_plus,
        )
        for r in res.scalars()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_snapshot_days_writes_and_replaces(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    days = [date(2024, 6, 1), date(2024, 6, 2)]
    built = datetime(2024, 6, 10, 12, 0, 0)
    try:
        await rebuild_and_commit(factory, [T_A, T_B], now=built)
        written = await snapshot_days(factory, days, zone="Europe/Berlin", now=built)
        # Berlin day ends: 06-01 22:00 UTC, 06-02 22:00 UTC.
        # 06-01: A (14 h old, prio 3). 06-02: A prio 5 (38 h old); B closed at 12:00 -> absent.
        expected = {
            (date(2024, 6, 1), "new", 3): (1, 1, 0, 0, 0, 0),
            (date(2024, 6, 2), "new", 5): (1, 0, 1, 0, 0, 0),
        }
        assert written == 2
        async with factory() as s:
            assert await _rows(s) == expected

        # second call replaces: same rows, and a stale row of a snapshotted day is dropped
        eng = create_engine(sync_url)
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_report_daily_snapshot (snapshot_date, queue_id,"
                    " state_type, priority_id, open_count, age_0_1d, age_1_3d, age_3_7d,"
                    " age_7_30d, age_30d_plus, built_at)"
                    " VALUES ('2024-06-01', :q, 'open', 1, 9, 9, 0, 0, 0, 0, :t)"
                ),
                {"q": QUEUE, "t": built},
            )
        eng.dispose()
        async with factory() as s:
            assert len(await _rows(s)) == 3
        assert await snapshot_days(factory, days, zone="Europe/Berlin", now=built) == 2
        async with factory() as s:
            assert await _rows(s) == expected
    finally:
        await engine.dispose()
    _clean(sync_url)
