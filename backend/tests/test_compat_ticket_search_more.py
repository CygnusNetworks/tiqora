"""TicketSearch gap closing: Created*, time-window families, TicketID, DISTINCT + ORDER BY.

DB tests on MariaDB and PostgreSQL. Seed ids live in the band 7670-7689; every row is
deleted again after the module.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import op_ticket_search
from tiqora.domain.auth import SessionStore

SESSION = "GI7670AGENT"
Q_A, Q_B = 7670, 7671
T_A, T_B, T_C, T_D = 7670, 7671, 7672, 7673
T_X = 7674  # in a queue the agent has no ro permission for
Q_X = 7672
T_E, Q_E = 7675, 7673  # history only outside a change window, in its own queue
ALL_QUEUES = [Q_A, Q_B]

_SEEDED: list[str] = []
_NOW = datetime.now(tz=UTC).replace(tzinfo=None, microsecond=0)


def _ago(**kw: float) -> datetime:
    return _NOW - timedelta(**kw)


def _epoch(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp())


def _cleanup_statements() -> tuple[str, ...]:
    return (
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 7670 AND 7689",
        "DELETE FROM article_data_mime WHERE article_id BETWEEN 7670 AND 7689",
        "DELETE FROM article WHERE id BETWEEN 7670 AND 7689",
        "DELETE FROM ticket WHERE id BETWEEN 7670 AND 7689",
        "DELETE FROM sessions WHERE session_id LIKE 'GI7670%'",
        "DELETE FROM queue WHERE id BETWEEN 7670 AND 7689",
        "DELETE FROM group_user WHERE group_id BETWEEN 7670 AND 7671",
        "DELETE FROM permission_groups WHERE id BETWEEN 7670 AND 7671",
        "DELETE FROM users WHERE id = 7670",
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _cleanup_statements():
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _ticket(
    conn: Any,
    tid: int,
    *,
    queue: int,
    state: str,
    created: datetime,
    changed: datetime,
    until: int = 0,
    escalation: int = 0,
    title: str = "GI search",
) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
            " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by) VALUES (:id, :tn, :title, :q, 1, 1, 7670, 1, 3,"
            " (SELECT id FROM ticket_state WHERE name = :st), 'GI7670', 'gi7670.cust', 0,"
            " :until, :esc, 0, 0, 0, 0, :c, 7670, :ch, 7670)"
        ),
        {
            "id": tid,
            "tn": f"{tid}001",
            "title": title,
            "q": queue,
            "st": state,
            "until": until,
            "esc": escalation,
            "c": created,
            "ch": changed,
        },
    )


def _history(
    conn: Any, tid: int, htype: str, when: datetime, *, queue: int, state: str, by: int = 7670
) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket_history (name, history_type_id, ticket_id, type_id, queue_id,"
            " owner_id, priority_id, state_id, create_time, create_by, change_time, change_by)"
            " VALUES (:n, (SELECT id FROM ticket_history_type WHERE name = :ht), :tid, 1, :q,"
            " 7670, 3, (SELECT id FROM ticket_state WHERE name = :st), :w, :by, :w, :by)"
        ),
        {"n": f"%%{htype}", "ht": htype, "tid": tid, "q": queue, "st": state, "w": when, "by": by},
    )


def _article(conn: Any, aid: int, tid: int, subject: str, created: datetime) -> None:
    conn.execute(
        text(
            "INSERT INTO article (id, ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tid, 3, 1, 1, :t, 7670, :t, 7670)"
        ),
        {"id": aid, "tid": tid, "t": created},
    )
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_to, a_subject, a_message_id,"
            " a_content_type, a_body, incoming_time, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'Max <max@example.org>', 'support@example.org', :s, :mid,"
            " 'text/plain', 'body', 1, :t, 7670, :t, 7670)"
        ),
        {"id": aid, "s": subject, "mid": f"<{aid}@example.org>", "t": created},
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    t0 = datetime(2024, 6, 1, 12, 0, 0)
    stamp = str(int(time.time()))
    with engine.begin() as conn:
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        for stmt in (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (7670, 'gi7670.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7670, 'gi7670-group', 1, :t, 1, :t, 1)",
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (7670, 7670, 'ro', :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7670, 'Q7670', 7670, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7671, 'gi7670-other', 1, :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7672, 'Q7672', 7671, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7671, 'Q7671', 7670, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7673, 'Q7673', 7670, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
        ):
            conn.execute(text(stmt), {"t": t0})
        for key, val in {
            "UserID": "7670",
            "UserLogin": "gi7670.agent",
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

        # A: created in Q7670 10 days ago, moved to Q7671 5 days ago, still open
        _ticket(conn, T_A, queue=Q_B, state="new", created=_ago(days=10), changed=_ago(days=5))
        _history(conn, T_A, "NewTicket", _ago(days=10), queue=Q_A, state="new")
        _history(conn, T_A, "Move", _ago(days=5), queue=Q_B, state="new")
        # B: created in Q7671 2 days ago, closed 1 day ago
        _ticket(
            conn,
            T_B,
            queue=Q_B,
            state="closed successful",
            created=_ago(days=2),
            changed=_ago(days=1),
        )
        _history(conn, T_B, "NewTicket", _ago(days=2), queue=Q_B, state="new")
        _history(conn, T_B, "StateUpdate", _ago(days=1), queue=Q_B, state="closed successful")
        # C: pending reminder due in an hour, escalated 10 minutes ago, changed an hour ago
        _ticket(
            conn,
            T_C,
            queue=Q_B,
            state="pending reminder",
            created=_ago(days=3),
            changed=_ago(hours=1),
            until=_epoch(_NOW + timedelta(hours=1)),
            escalation=_epoch(_ago(minutes=10)),
        )
        # D: open ticket with a stale until_time but NOT in a pending state
        _ticket(
            conn,
            T_D,
            queue=Q_B,
            state="open",
            created=_ago(days=30),
            changed=_ago(days=30),
            until=_epoch(_NOW + timedelta(hours=2)),
        )
        _ticket(conn, T_X, queue=Q_X, state="new", created=_ago(days=1), changed=_ago(days=1))
        # E: history 10 days and 1 day ago, nothing in between (only searched via Q_E)
        _ticket(conn, T_E, queue=Q_E, state="new", created=_ago(days=10), changed=_ago(days=1))
        _history(conn, T_E, "NewTicket", _ago(days=10), queue=Q_E, state="new")
        _history(conn, T_E, "Move", _ago(days=1), queue=Q_E, state="new")
        # articles: A has two matching ones (duplicate rows through the MIME join), B one
        _article(conn, 7670, T_A, "needle one", _ago(days=9))
        _article(conn, 7671, T_A, "needle two", _ago(days=8))
        _article(conn, 7672, T_B, "needle three", _ago(days=2))
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


async def _search(sync_url: str, params: dict[str, Any]) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            return await op_ticket_search(
                {"SessionID": SESSION, "QueueIDs": ALL_QUEUES, **params}, session, store
            )
    finally:
        await engine.dispose()


async def _ids(sync_url: str, params: dict[str, Any]) -> list[int]:
    result = await _search(sync_url, params)
    assert "Error" not in result, result
    return sorted(int(i) for i in result.get("TicketID", []))


pytestmark = pytest.mark.db


async def test_created_queue_and_state(sync_url: str) -> None:
    assert await _ids(sync_url, {"CreatedQueueIDs": [Q_A]}) == [T_A]
    assert await _ids(sync_url, {"CreatedQueues": ["Q7671"]}) == [T_B]
    assert await _ids(sync_url, {"CreatedStates": ["new"]}) == [T_A, T_B]
    assert await _ids(sync_url, {"CreatedStateIDs": "32000"}) == []
    # all Created* arguments constrain the same NewTicket row
    assert await _ids(sync_url, {"CreatedQueueIDs": [Q_A], "CreatedStates": ["new"]}) == [T_A]
    assert await _ids(sync_url, {"CreatedQueueIDs": [Q_B], "CreatedUserIDs": [7670]}) == [T_B]


async def test_created_unknown_name_gives_empty_result(sync_url: str) -> None:
    assert await _search(sync_url, {"CreatedQueues": ["NoSuchQueue7670"]}) == {}


async def test_change_window_bounds_apply_to_the_same_history_row(sync_url: str) -> None:
    day = 24 * 60
    # any history row within 7 days: A (Move), B
    assert await _ids(sync_url, {"TicketChangeTimeNewerMinutes": 7 * day}) == [T_A, T_B]
    # "changed between 7 and 3 days ago": A's Move (5 days) lies inside the window
    both = {"TicketChangeTimeNewerMinutes": 7 * day, "TicketChangeTimeOlderMinutes": 3 * day}
    assert await _ids(sync_url, both) == [T_A]
    # E has rows 10 days and 1 day ago only: each bound is met by a different row, but no
    # single row lies in the window (Znuny: both bounds on the one alias th1)
    assert await _ids(sync_url, {**both, "QueueIDs": [Q_E]}) == []
    assert await _ids(sync_url, {"TicketChangeTimeNewerMinutes": 7 * day, "QueueIDs": [Q_E]}) == [
        T_E
    ]
    assert await _ids(
        sync_url,
        {
            "ChangeTimeSearchType": "TimePoint",
            "TicketChangeTimePoint": "7",
            "TicketChangeTimePointFormat": "day",
            "TicketChangeTimePointStart": "Last",
        },
    ) == [T_A, T_B]


async def test_last_change_uses_ticket_change_time(sync_url: str) -> None:
    assert await _ids(sync_url, {"TicketLastChangeTimeNewerMinutes": 120}) == [T_C]
    older = {"TicketLastChangeTimeOlderDate": _ago(days=20).strftime("%Y-%m-%d %H:%M:%S")}
    assert await _ids(sync_url, older) == [T_D]


async def test_close_time_needs_closed_state_history(sync_url: str) -> None:
    assert await _ids(sync_url, {"TicketCloseTimeNewerMinutes": 2 * 24 * 60}) == [T_B]
    assert await _ids(sync_url, {"TicketCloseTimeNewerMinutes": 12 * 60}) == []
    assert await _ids(sync_url, {"TicketCloseTimeOlderMinutes": 12 * 60}) == [T_B]


async def test_pending_time_only_in_pending_states(sync_url: str) -> None:
    # D has a later until_time but is "open": the pending filter must not return it
    assert await _ids(sync_url, {"TicketPendingTimeNewerMinutes": 0}) == [T_C]
    assert await _ids(sync_url, {"TicketPendingTimeOlderMinutes": -90}) == [T_C]


async def test_escalation_zero_excluded(sync_url: str) -> None:
    # every ticket without escalation has 0 (<= now): it must not match "older than now"
    assert await _ids(sync_url, {"TicketEscalationTimeOlderMinutes": 0}) == [T_C]
    assert await _ids(sync_url, {"TicketEscalationTimeNewerMinutes": 60}) == [T_C]
    assert await _ids(sync_url, {"TicketEscalationTimeNewerMinutes": 5}) == []


async def test_create_time_window(sync_url: str) -> None:
    assert await _ids(sync_url, {"TicketCreateTimeNewerMinutes": 4 * 24 * 60}) == [T_B, T_C]
    older = {"TicketCreateTimeOlderMinutes": 20 * 24 * 60}
    assert await _ids(sync_url, older) == [T_D]


async def test_invalid_date_returns_empty_object(sync_url: str) -> None:
    assert await _search(sync_url, {"TicketChangeTimeNewerDate": "yesterday"}) == {}
    assert await _search(sync_url, {"TicketCreateTimeOlderDate": "2026-02-30 00:00:00"}) == {}


async def test_ticket_id_filter(sync_url: str) -> None:
    assert await _ids(sync_url, {"TicketID": T_B}) == [T_B]
    assert await _ids(sync_url, {"TicketID": [T_A, T_C, 99999999]}) == [T_A, T_C]


async def test_sort_with_distinct_on_postgres(sync_url: str) -> None:
    # two matching articles on A duplicate its row through the MIME join; DISTINCT must
    # still sort (PostgreSQL rejects ORDER BY columns missing from the select list)
    result = await _search(sync_url, {"MIMEBase_Subject": "*needle*", "SortBy": "Age"})
    assert result == {"TicketID": [T_B, T_A]}
    up = await _search(
        sync_url, {"MIMEBase_Subject": "*needle*", "SortBy": "Changed", "OrderBy": "Up"}
    )
    assert up == {"TicketID": [T_A, T_B]}
    two = await _search(
        sync_url,
        {"MIMEBase_Subject": "*needle*", "SortBy": ["Queue", "Age"], "OrderBy": ["Down", "Up"]},
    )
    assert two == {"TicketID": [T_A, T_B]}


async def test_empty_result_is_empty_object(sync_url: str) -> None:
    assert await _search(sync_url, {"Title": "no such title 7670"}) == {}


async def test_ticket_id_in_queue_without_permission_is_not_found(sync_url: str) -> None:
    only_x = await _search(sync_url, {"QueueIDs": None, "TicketID": T_X})
    assert only_x == {}
    mixed = await _search(sync_url, {"QueueIDs": None, "TicketID": [T_A, T_X]})
    assert mixed == {"TicketID": [T_A]}


async def test_out_of_range_ids_match_nothing_and_are_not_bound(sync_url: str) -> None:
    assert await _search(sync_url, {"CreatedStateIDs": 40000}) == {}
    assert await _search(sync_url, {"TicketID": 2**70}) == {}
    assert await _search(sync_url, {"QueueIDs": 2**40}) == {}
    assert await _search(sync_url, {"StateIDs": [40000]}) == {}
    assert await _search(sync_url, {"PriorityIDs": "70000"}) == {}
    assert await _search(sync_url, {"OwnerIDs": [2**31]}) == {}
    assert await _search(sync_url, {"LockIDs": ["x"]}) == {}
    assert await _search(sync_url, {"CreatedQueueIDs": 2**33}) == {}
    # valid ids next to invalid ones still match
    # an id longer than 16 digits is no integer for Znuny (Quote 'Integer'): empty search
    assert await _search(sync_url, {"TicketID": [T_A, 2**70]}) == {}
    assert await _search(sync_url, {"TicketID": [T_A, 10**16]}) == {}
    assert await _search(sync_url, {"QueueIDs": [Q_A, "12345678901234567"]}) == {}
    assert await _ids(sync_url, {"TicketID": [T_A, 10**15]}) == [T_A]
    assert await _ids(sync_url, {"StateIDs": [40000], "States": ["new"]}) == [T_A]


async def test_content_filters_are_substring_and_case_insensitive(sync_url: str) -> None:
    # Znuny GI sets ContentSearchPrefix/Suffix '*': no wildcards needed
    assert await _ids(sync_url, {"MIMEBase_Subject": "needle"}) == [T_A, T_B]
    assert await _ids(sync_url, {"MIMEBase_Subject": "NEEDLE two"}) == [T_A]
    assert await _ids(sync_url, {"MIMEBase_From": "max@example"}) == [T_A, T_B]
    assert await _ids(sync_url, {"Fulltext": "Needle"}) == [T_A, T_B]
    assert await _ids(sync_url, {"Fulltext": "no-such-text-7670"}) == []
    # only wildcards: Znuny skips the filter
    assert (await _ids(sync_url, {"MIMEBase_Subject": "*"})) == [T_A, T_B, T_C, T_D]


async def test_sort_by_pending_and_escalation_puts_zero_last(sync_url: str) -> None:
    up = await _search(sync_url, {"SortBy": "PendingTime", "OrderBy": "Up"})
    assert up["TicketID"][:2] == [T_C, T_D]
    down = await _search(sync_url, {"SortBy": "PendingTime", "OrderBy": "Down"})
    assert down["TicketID"][:2] == [T_D, T_C]
    assert set(down["TicketID"][2:]) == {T_A, T_B}
    for order in ("Up", "Down"):
        esc = await _search(sync_url, {"SortBy": "EscalationTime", "OrderBy": order})
        assert esc["TicketID"][0] == T_C
    for key in ("EscalationUpdateTime", "EscalationResponseTime", "EscalationSolutionTime"):
        every = await _search(sync_url, {"SortBy": key})
        assert sorted(every["TicketID"]) == [T_A, T_B, T_C, T_D]
