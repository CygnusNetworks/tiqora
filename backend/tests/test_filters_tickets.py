"""DB tests for the permission-scoped ticket filter compiler (MariaDB + PostgreSQL).

Seed ids use the 914xx range; the module deletes every row it created.
"""

from __future__ import annotations

import calendar
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.filters import Filter, FilterError
from tiqora.filters.tickets import (
    DF_FIELD_PREFIX,
    TICKET_FILTER_FIELDS,
    TicketQueryScope,
    compile_ticket_filter,
    count_tickets,
    ticket_filter_fields,
    ticket_query_scope,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 2, 1, 12, 0, 0)
NOW_EPOCH = calendar.timegm(NOW.timetuple())

AGENT_A = 91400  # ro on G1 only
OTHER_AGENT = 91401  # no group at all
G1 = 91410
G2 = 91411
Q1 = 91420
Q2 = 91421
Q3 = 91422
T1 = 91430  # Q1, new, owner A, DFs set
T2 = 91431  # Q2, open, owner root, locked, escalated an hour ago
T3 = 91432  # Q3 (G2) — invisible to A
T4 = 91433  # Q1, closed, archived
TICKETS = (T1, T2, T3, T4)
DF_AREA = 91440
DF_DUE = 91441
DF_IDS = (DF_AREA, DF_DUE)

_SEEDED: list[str] = []


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup_statements() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM ticket_watcher WHERE ticket_id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM dynamic_field_value WHERE field_id IN :ids", {"ids": DF_IDS}),
        ("DELETE FROM dynamic_field WHERE id IN :ids", {"ids": DF_IDS}),
        ("DELETE FROM ticket WHERE id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM queue WHERE id IN :ids", {"ids": (Q1, Q2, Q3)}),
        ("DELETE FROM group_user WHERE group_id IN :ids", {"ids": (G1, G2)}),
        ("DELETE FROM permission_groups WHERE id IN :ids", {"ids": (G1, G2)}),
        ("DELETE FROM users WHERE id IN :ids", {"ids": (AGENT_A, OTHER_AGENT)}),
    ]


def _run(conn: Any, stmts: list[tuple[str, dict[str, Any]]]) -> None:
    for sql, params in stmts:
        stmt = text(sql)
        if "ids" in params:
            stmt = stmt.bindparams(bindparam("ids", expanding=True))
        conn.execute(stmt, params)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in _SEEDED:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _run(conn, _cleanup_statements())
        engine.dispose()
    _SEEDED.clear()


def _insert_ticket(
    conn: Any,
    *,
    tid: int,
    queue_id: int,
    state_id: int,
    owner_id: int,
    lock_id: int,
    created: datetime,
    escalation_time: int = 0,
    archived: int = 0,
    title: str = "Wave0 filter ticket",
) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tn, :title, :qid, :lock, 1, :uid, 1, 3, :st,"
            " 0, 0, :esc, 0, 0, 0, :arch, :t, 1, :t, 1)"
        ),
        {
            "id": tid,
            "tn": f"20260110{tid}",
            "title": title,
            "qid": queue_id,
            "lock": lock_id,
            "uid": owner_id,
            "st": state_id,
            "esc": escalation_time,
            "arch": archived,
            "t": created,
        },
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _run(conn, _cleanup_statements())
        for uid, login in ((AGENT_A, "filters.agent.a"), (OTHER_AGENT, "filters.agent.b")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Filter', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": login, "t": NOW},
            )
        for gid, name in ((G1, "filters-g1"), (G2, "filters-g2")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "name": name, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_A, "gid": G1, "t": NOW},
        )
        for qid, gid in ((Q1, G1), (Q2, G1), (Q3, G2)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "name": f"FiltersQueue{qid}", "gid": gid, "t": NOW},
            )
        # Znuny seed ids: state 1 new / 2 closed successful / 4 open; lock 1 unlock / 2 lock.
        _insert_ticket(
            conn,
            tid=T1,
            queue_id=Q1,
            state_id=1,
            owner_id=AGENT_A,
            lock_id=1,
            created=datetime(2026, 1, 10),
            title="Wave0 100% done_x",
        )
        _insert_ticket(
            conn,
            tid=T2,
            queue_id=Q2,
            state_id=4,
            owner_id=1,
            lock_id=2,
            created=datetime(2026, 1, 20),
            escalation_time=NOW_EPOCH - 3600,
        )
        _insert_ticket(
            conn,
            tid=T3,
            queue_id=Q3,
            state_id=1,
            owner_id=1,
            lock_id=1,
            created=datetime(2026, 1, 12),
        )
        _insert_ticket(
            conn,
            tid=T4,
            queue_id=Q1,
            state_id=2,
            owner_id=1,
            lock_id=1,
            created=datetime(2026, 1, 5),
            archived=1,
        )
        for fid, name, label, ftype, order in (
            (DF_AREA, "WaveZeroArea", "Bereich", "Dropdown", 9140),
            (DF_DUE, "WaveZeroDue", "Fällig", "Date", 9141),
        ):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 0, :name, :label, :o, :ft, 'Ticket', '---', 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "name": name, "label": label, "o": order, "ft": ftype, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                " VALUES (:f, :o, 'net')"
            ),
            {"f": DF_AREA, "o": T1},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_date)"
                " VALUES (:f, :o, :d)"
            ),
            {"f": DF_DUE, "o": T1, "d": datetime(2026, 2, 1)},
        )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session(request: pytest.FixtureRequest) -> Any:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        s.info["sync_url"] = sync_url
        yield s
    await engine.dispose()


async def _ids(
    session: AsyncSession, raw: dict[str, Any], scope: TicketQueryScope, now: datetime = NOW
) -> set[int]:
    stmt = await compile_ticket_filter(session, Filter.model_validate(raw), scope, now=now)
    return set() if stmt is None else {t.id for t in (await session.execute(stmt)).scalars()}


def _cond(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"where": {"field": field, "op": op, "value": value}}


@pytest.mark.asyncio
async def test_scope_is_never_widened_by_filter(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert scope.queue_ids == frozenset({Q1, Q2})
    assert await _ids(session, _cond("queue_id", "eq", Q3), scope) == set()
    widening = {
        "where": {
            "any": [
                {"field": "queue_id", "op": "eq", "value": Q3},
                {"not": {"field": "queue_id", "op": "eq", "value": Q3}},
            ]
        }
    }
    assert await _ids(session, widening, scope) == {T1, T2}
    assert await _ids(session, _cond("ticket_id", "eq", T3), scope) == set()
    assert await _ids(session, {}, scope) == {T1, T2}  # archived T4 excluded by scope


@pytest.mark.asyncio
async def test_include_archived_scope(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A, include_archived=True)
    assert await _ids(session, {}, scope) == {T1, T2, T4}
    assert await _ids(session, _cond("archived", "is_true"), scope) == {T4}


@pytest.mark.asyncio
async def test_empty_scope_returns_none(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, OTHER_AGENT)
    assert scope.queue_ids == frozenset()
    assert await compile_ticket_filter(session, Filter(), scope, now=NOW) is None
    assert await count_tickets(session, Filter(), scope, now=NOW) == 0


@pytest.mark.asyncio
async def test_state_type_and_locked_and_unassigned(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("state_type", "in", ["new"]), scope) == {T1}
    assert await _ids(session, _cond("state_type", "neq", "new"), scope) == {T2}
    assert await _ids(session, _cond("locked", "is_true"), scope) == {T2}
    assert await _ids(session, _cond("locked", "is_false"), scope) == {T1}
    assert await _ids(session, _cond("unassigned", "is_true"), scope) == {T2}
    assert await _ids(session, _cond("unassigned", "is_false"), scope) == {T1}


@pytest.mark.asyncio
async def test_escalated_and_within(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("escalated", "is_true"), scope) == {T2}
    assert await _ids(session, _cond("escalated", "is_false"), scope) == {T1}
    assert await _ids(session, _cond("escalation_due_within", "lte", 7200), scope) == {T2}
    # The compiler uses the passed clock: a day earlier T2 was not escalated yet...
    yesterday = NOW - timedelta(days=1)
    assert await _ids(session, _cond("escalated", "is_true"), scope, yesterday) == set()
    # ...but it was due within the next 25 hours.
    assert await _ids(session, _cond("escalation_due_within", "lte", 90000), scope, yesterday) == {
        T2
    }


@pytest.mark.asyncio
async def test_ai_escalated_flag(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("ai_escalated", "is_true"), scope) == set()
    sync_url = session.info["sync_url"]
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at) VALUES (:tid, :t)"
            ),
            {"tid": T1, "t": NOW},
        )
    engine.dispose()
    await session.rollback()  # MariaDB: drop the session's repeatable-read snapshot
    try:
        assert await _ids(session, _cond("ai_escalated", "is_true"), scope) == {T1}
        assert await _ids(session, _cond("escalated", "is_true"), scope) == {T1, T2}
    finally:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id = :t"), {"t": T1})
        engine.dispose()


@pytest.mark.asyncio
async def test_created_between_and_relative(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    between = _cond("created", "between", {"from": "2026-01-15", "to": "2026-02-01"})
    assert await _ids(session, between, scope) == {T2}
    within = _cond("created", "within_last", {"amount": 2, "unit": "day"})
    assert await _ids(session, within, scope, datetime(2026, 1, 21)) == {T2}
    older = _cond("created", "older_than", {"amount": 1, "unit": "week"})
    assert await _ids(session, older, scope, datetime(2026, 1, 21)) == {T1}


@pytest.mark.asyncio
async def test_neq_includes_nulls(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("service_id", "neq", 999), scope) == {T1, T2}
    assert await _ids(session, _cond("service_id", "eq", 999), scope) == set()
    # NOT over a nullable column stays two-valued: NULL rows are "not 999".
    negated = {"where": {"not": {"field": "service_id", "op": "eq", "value": 999}}}
    assert await _ids(session, negated, scope) == {T1, T2}


@pytest.mark.asyncio
async def test_string_ops_escape_like_wildcards(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("title", "contains", "100%"), scope) == {T1}
    assert await _ids(session, _cond("title", "contains", "done_x"), scope) == {T1}
    assert await _ids(session, _cond("title", "contains", "%"), scope) == {T1}
    assert await _ids(session, _cond("title", "contains", "_"), scope) == {T1}
    assert await _ids(session, _cond("title", "starts_with", "wave0"), scope) == {T1, T2}
    assert await _ids(session, _cond("title", "starts_with", "100"), scope) == set()


@pytest.mark.asyncio
async def test_channel_and_watcher(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await _ids(session, _cond("channel", "eq", "email"), scope) == {T1, T2}
    assert await _ids(session, _cond("channel", "in", ["telegram", "webchat"]), scope) == set()
    assert await _ids(session, _cond("watched_by", "eq", AGENT_A), scope) == set()
    sync_url = session.info["sync_url"]
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ticket_watcher (ticket_id, user_id, create_time, create_by,"
                " change_time, change_by) VALUES (:tid, :uid, :t, 1, :t, 1)"
            ),
            {"tid": T2, "uid": AGENT_A, "t": NOW},
        )
    engine.dispose()
    await session.rollback()
    assert await _ids(session, _cond("watched_by", "eq", AGENT_A), scope) == {T2}


@pytest.mark.asyncio
async def test_dynamic_fields(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    area = f"{DF_FIELD_PREFIX}WaveZeroArea"
    due = f"{DF_FIELD_PREFIX}WaveZeroDue"
    assert await _ids(session, _cond(area, "eq", "net"), scope) == {T1}
    assert await _ids(session, _cond(area, "neq", "net"), scope) == {T2}
    assert await _ids(session, _cond(area, "is_empty"), scope) == {T2}
    assert await _ids(session, _cond(area, "is_not_empty"), scope) == {T1}
    assert await _ids(session, _cond(area, "in", ["net", "x"]), scope) == {T1}
    assert await _ids(session, _cond(area, "not_in", ["net"]), scope) == {T2}
    assert await _ids(session, _cond(area, "contains", "ne"), scope) == {T1}
    assert await _ids(session, _cond(due, "before", "2026-03-01"), scope) == {T1}
    assert await _ids(session, _cond(due, "before", "2026-02-01"), scope) == set()
    assert await _ids(session, _cond(due, "is_empty"), scope) == {T2}
    # A DF value never leaks a ticket outside the scope.
    negated = {"where": {"not": {"field": area, "op": "eq", "value": "net"}}}
    assert await _ids(session, negated, scope) == {T2}


@pytest.mark.asyncio
async def test_unknown_df_is_filter_error(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    with pytest.raises(FilterError) as err:
        await compile_ticket_filter(
            session, Filter.model_validate(_cond("df.Nope", "eq", "x")), scope, now=NOW
        )
    assert err.value.path == "where.field"


@pytest.mark.asyncio
async def test_invalid_filter_raises_even_for_empty_scope(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, OTHER_AGENT)
    with pytest.raises(FilterError):
        await compile_ticket_filter(
            session, Filter.model_validate(_cond("queue_id", "contains", "x")), scope, now=NOW
        )


@pytest.mark.asyncio
async def test_count_tickets(session: AsyncSession) -> None:
    scope = await ticket_query_scope(session, AGENT_A)
    assert await count_tickets(session, Filter(), scope, now=NOW) == 2
    flt = Filter.model_validate(_cond("state_type", "eq", "new"))
    assert await count_tickets(session, flt, scope, now=NOW) == 1


@pytest.mark.asyncio
async def test_ticket_filter_fields_lists_static_and_dfs(session: AsyncSession) -> None:
    fields = {f.key: f for f in await ticket_filter_fields(session)}
    assert set(TICKET_FILTER_FIELDS) <= set(fields)
    assert "queue_id" in fields
    assert fields["queue_id"].options_source == "queue"
    assert fields["queue_id"].label_key == "ticketFilter.fields.queue_id"
    area = fields["df.WaveZeroArea"]
    assert area.type == "string"
    assert area.options_source == "DynamicField_WaveZeroArea"
    assert area.label == "Bereich"
    assert fields["df.WaveZeroDue"].type == "datetime"
    assert fields["df.WaveZeroDue"].options_source is None
