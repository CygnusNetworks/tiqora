"""TimeAccountingGet reports the queue at entry time (both dialects).

Seed ids live in the band 8980-8989; every row is deleted again after the module.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.compat.history_at import queue_at
from tiqora.api.compat.operations import op_time_accounting_get
from tiqora.domain.auth import SessionStore

pytestmark = pytest.mark.db

AGENT = 8980
SESSION = "GI8980AGENT"
T_MOVED, T_NO_QUEUE_HISTORY, T_NO_HISTORY = 8980, 8981, 8982
_SEEDED: list[str] = []

_CLEANUP = (
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN 8980 AND 8989",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 8980 AND 8989",
    "DELETE FROM ticket WHERE id BETWEEN 8980 AND 8989",
    "DELETE FROM sessions WHERE session_id LIKE 'GI8980%'",
    "DELETE FROM queue WHERE id IN (8980, 8981)",
    "DELETE FROM group_user WHERE user_id = 8980",
    "DELETE FROM permission_groups WHERE id = 8980",
    "DELETE FROM users WHERE id = 8980",
)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _ticket(conn: Any, tid: int, queue_id: int, now: datetime) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
            " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by) VALUES (:id, :tn, :title, :q, 1, 1, 8980,"
            " 1, 3, (SELECT id FROM ticket_state WHERE name = 'new'), 'GI8980', 'gi8980.cust',"
            " 0, 0, 0, 0, 0, 0, 0, :t, 8980, :t, 8980)"
        ),
        {"id": tid, "tn": f"{tid}001", "title": f"Example {tid}", "q": queue_id, "t": now},
    )


def _history(conn: Any, tid: int, kind: str, name: str, queue_id: int, at: datetime) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket_history (name, history_type_id, ticket_id, article_id, type_id,"
            " queue_id, owner_id, priority_id, state_id, create_time, create_by, change_time,"
            " change_by) VALUES (:n, (SELECT id FROM ticket_history_type WHERE name = :k), :tid,"
            " NULL, 1, :q, 1, 3, 1, :t, 8980, :t, 8980)"
        ),
        {"n": name, "k": kind, "tid": tid, "q": queue_id, "t": at},
    )


def _book(conn: Any, tid: int, unit: str, at: datetime) -> None:
    conn.execute(
        text(
            "INSERT INTO time_accounting (ticket_id, article_id, time_unit, create_time,"
            " create_by, change_time, change_by) VALUES (:tid, NULL, :u, :t, 8980, :t, 8980)"
        ),
        {"tid": tid, "u": unit, "t": at},
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    base = datetime(2024, 6, 1, 12, 0, 0)
    stamp = str(int(time.time()))
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (8980, 'gi8980.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"t": base},
        )
        group_id = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'timeaccounting_webservice'")
        ).scalar()
        if group_id is None:
            group_id = 8980
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (8980, 'timeaccounting_webservice', 1, :t, 1, :t, 1)"
                ),
                {"t": base},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (8980, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"g": group_id, "t": base},
        )
        for qid in (8980, 8981):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:q, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"q": qid, "n": f"Q{qid}", "g": group_id, "t": base},
            )
        for key, val in {
            "UserID": "8980",
            "UserLogin": "gi8980.agent",
            "UserType": "User",
            "UserLastRequest": stamp,
            "UserSessionStart": stamp,
        }.items():
            conn.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:sid, :k, :v, 0)"
                ),
                {"sid": SESSION, "k": key, "v": val},
            )
        # Created in Q8980, time booked, later moved to Q8981, more time booked.
        _ticket(conn, T_MOVED, 8981, base)
        _history(
            conn,
            T_MOVED,
            "NewTicket",
            f"%%{T_MOVED}001%%Q8980%%3 normal%%new%%{T_MOVED}",
            8980,
            base,
        )
        _book(conn, T_MOVED, "1.50", datetime(2024, 6, 1, 12, 30, 0))
        _history(
            conn,
            T_MOVED,
            "Move",
            "%%Q8981%%8981%%Q8980%%8980",
            8981,
            datetime(2024, 6, 1, 13, 0, 0),
        )
        _book(conn, T_MOVED, "0.25", datetime(2024, 6, 1, 13, 30, 0))
        # History without a queue entry: current queue (Q8981) is the answer.
        _ticket(conn, T_NO_QUEUE_HISTORY, 8981, base)
        _history(conn, T_NO_QUEUE_HISTORY, "AddNote", "%%note", 8981, base)
        _book(conn, T_NO_QUEUE_HISTORY, "2.00", datetime(2024, 6, 1, 14, 0, 0))
        # No history at all: Znuny answers undef.
        _ticket(conn, T_NO_HISTORY, 8980, base)
        _book(conn, T_NO_HISTORY, "3.00", datetime(2024, 6, 1, 15, 0, 0))
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


async def _entries(
    sync_url: str,
    start: str = "2024-06-01 00:00:00",
    end: str = "2024-06-01 23:59:59",
) -> list[dict[str, Any]]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            res = await op_time_accounting_get(
                {
                    "SessionID": SESSION,
                    "TimeAccountingUserLogin": "gi8980.agent",
                    "TimeAccountingStart": start,
                    "TimeAccountingEnd": end,
                },
                session,
                store,
            )
    finally:
        await engine.dispose()
    assert "Error" not in res, res
    entries: list[dict[str, Any]] = res["TimeAccountingResult"]
    return entries


async def test_time_accounting_queue_at_entry_time(sync_url: str) -> None:
    moved = [e for e in await _entries(sync_url) if e["TicketNumber"] == f"{T_MOVED}001"]
    assert [e["Queue"] for e in moved] == ["Q8980", "Q8981"]
    assert [e["TimeUnit"] for e in moved] == ["1.5", "0.25"]
    assert [e["Created"] for e in moved] == ["2024-06-01 12:30:00", "2024-06-01 13:30:00"]
    for entry in moved:
        assert "TicketID" not in entry
        assert entry["TicketCustomerID"] == "GI8980"
        assert entry["TicketTitle"] == f"Example {T_MOVED}"


async def test_queue_at_falls_back_to_current_queue(sync_url: str) -> None:
    by_number = {e["TicketNumber"]: e for e in await _entries(sync_url)}
    assert by_number[f"{T_NO_QUEUE_HISTORY}001"]["Queue"] == "Q8981"
    assert by_number[f"{T_NO_QUEUE_HISTORY}001"]["TimeUnit"] == "2"
    assert by_number[f"{T_NO_HISTORY}001"]["Queue"] is None

    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await queue_at(session, T_MOVED, datetime(2024, 6, 1, 12, 59, 59)) == "Q8980"
            assert await queue_at(session, T_MOVED, datetime(2024, 6, 1, 13, 0, 0)) == "Q8981"
            assert await queue_at(session, T_MOVED, datetime(2024, 5, 1, 0, 0, 0)) is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2024-06-01T00:00:00", "2024-06-01T23:59:59"),
        ("2024-06-01T00:00:00Z", "2024-06-01T23:59:59+00:00"),
        ("2024-06-01T02:00:00+02:00", "2024-06-02T01:59:59+02:00"),
        ("2024-06-01", "2024-06-01 23:59:59"),
    ],
)
async def test_iso_date_bounds_are_accepted(sync_url: str, start: str, end: str) -> None:
    entries = await _entries(sync_url, start, end)
    assert len(entries) == 4


async def test_unparsable_date_bound_gives_empty_result(sync_url: str) -> None:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            res = await op_time_accounting_get(
                {
                    "SessionID": SESSION,
                    "TimeAccountingUserLogin": "gi8980.agent",
                    "TimeAccountingStart": "yesterday",
                    "TimeAccountingEnd": "2024-06-01 23:59:59",
                },
                session,
                store,
            )
    finally:
        await engine.dispose()
    assert res == {}
