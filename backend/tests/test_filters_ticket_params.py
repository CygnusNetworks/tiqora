"""DB parity test: list query params -> filter bridge == ``_filtered_ticket_stmt``.

Gate for moving ``GET /api/v1/tickets`` onto the compiler later. Seed ids use the
915xx range; the module deletes every row it created.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import VIEW_STATE_TYPES, TicketService
from tiqora.filters import AllOf, Condition, Filter
from tiqora.filters.ticket_params import TicketListParams, filter_from_list_params
from tiqora.filters.tickets import compile_ticket_filter, ticket_query_scope

pytestmark = pytest.mark.db

NOW = datetime(2026, 2, 1, 12, 0, 0)
AGENT_A = 91500
G1 = 91510
G2 = 91511
Q1 = 91520
Q2 = 91521
Q3 = 91522
T1 = 91530  # Q1 new, owner A, unlocked, watched, Telegram, customer, responsible A
T2 = 91531  # Q2 open, owner root, locked, SLA-escalated
T3 = 91532  # Q3 (G2) invisible
T4 = 91533  # Q1 closed, archived
T5 = 91534  # Q1 closed, phone first, escalates within the hour, responsible A
T6 = 91535  # Q2 open, owner A, tmp_lock, e-mail then phone, AI handoff
TICKETS = (T1, T2, T3, T4, T5, T6)
ARTICLES: dict[int, tuple[int, str]] = {
    91540: (T1, "Telegram"),
    91541: (T2, "Email"),
    91542: (T5, "Phone"),
    91543: (T6, "Email"),
    91544: (T6, "Phone"),
}
CUSTOMER = "parity.customer@example.org"
STATE_NEW = 1

_SEEDED: list[str] = []

PARAM_SETS: list[dict[str, Any]] = [
    {},
    {"queue_id": Q1},
    {"queue_id": Q3},
    {"state_type": "open"},
    {"state_type": "todo"},
    {"state_type": "closed"},
    {"state_id": STATE_NEW},
    {"owner_id": AGENT_A},
    {"locked": True},
    {"locked": False},
    {"watcher_user_id": AGENT_A},
    {"escalated": True},
    {"ai_escalated": True},
    {"unassigned": True},
    {"unassigned": False},
    {"escalating_within": 7200},
    {"channel": ["telegram"]},
    {"channel": ["email", "phone"]},
    {"customer_user_id": CUSTOMER},
    {"responsible_id": AGENT_A},
    {"include_archived": True},
    # combinations and the remaining view names / edge cases
    {"state_type": "new"},
    {"state_type": "pending"},
    {"state_type": "open_only", "owner_id": AGENT_A},
    {"state_type": "nonexistent"},
    {"escalated": True, "channel": ["email"]},
    {"escalating_within": 0},
    {"customer_id": "PARITY-CUST"},
    {"service_id": 999},
    {"include_archived": True, "state_type": "closed", "unassigned": True},
    {"watcher_user_id": 91599},
]


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
        ("DELETE FROM article WHERE id IN :ids", {"ids": tuple(ARTICLES)}),
        ("DELETE FROM ticket WHERE id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM queue WHERE id IN :ids", {"ids": (Q1, Q2, Q3)}),
        ("DELETE FROM group_user WHERE group_id IN :ids", {"ids": (G1, G2)}),
        ("DELETE FROM permission_groups WHERE id IN :ids", {"ids": (G1, G2)}),
        ("DELETE FROM users WHERE id IN :ids", {"ids": (AGENT_A,)}),
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
            conn.execute(
                text(
                    "DELETE FROM communication_channel"
                    " WHERE name = 'Telegram' AND package_name = 'ParityTest'"
                )
            )
        engine.dispose()
    _SEEDED.clear()


def _insert_ticket(conn: Any, *, tid: int, **kw: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " customer_id, customer_user_id, timeout, until_time, escalation_time,"
            " escalation_update_time, escalation_response_time, escalation_solution_time,"
            " archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tn, 'Parity ticket', :qid, :lock, 1, :uid, :resp, 3, :st,"
            " :cid, :cuid, 0, 0, :esc, 0, 0, 0, :arch, :t, 1, :t, 1)"
        ),
        {
            "id": tid,
            "tn": f"20260115{tid}",
            "qid": kw["queue_id"],
            "lock": kw.get("lock_id", 1),
            "uid": kw.get("owner_id", 1),
            "resp": kw.get("responsible", 1),
            "st": kw["state_id"],
            "cid": kw.get("customer_id"),
            "cuid": kw.get("customer_user_id"),
            "esc": kw.get("escalation_time", 0),
            "arch": kw.get("archived", 0),
            "t": NOW,
        },
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    now_epoch = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _run(conn, _cleanup_statements())
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'parity.agent.a', 'x', 'Parity', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_A, "t": NOW},
        )
        for gid, name in ((G1, "parity-g1"), (G2, "parity-g2")):
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
                {"id": qid, "name": f"ParityQueue{qid}", "gid": gid, "t": NOW},
            )
        # Znuny seed ids: state 1 new / 2 closed / 4 open; lock 1 unlock / 2 lock / 3 tmp_lock.
        _insert_ticket(
            conn, tid=T1, queue_id=Q1, state_id=1, owner_id=AGENT_A, responsible=AGENT_A,
            customer_id="PARITY-CUST", customer_user_id=CUSTOMER,
        )  # fmt: skip
        _insert_ticket(
            conn, tid=T2, queue_id=Q2, state_id=4, lock_id=2, escalation_time=now_epoch - 3600
        )
        _insert_ticket(conn, tid=T3, queue_id=Q3, state_id=1)
        _insert_ticket(conn, tid=T4, queue_id=Q1, state_id=2, archived=1)
        _insert_ticket(
            conn, tid=T5, queue_id=Q1, state_id=2, responsible=AGENT_A,
            escalation_time=now_epoch + 3600,
        )  # fmt: skip
        _insert_ticket(conn, tid=T6, queue_id=Q2, state_id=4, lock_id=3, owner_id=AGENT_A)

        channel_ids = dict(conn.execute(text("SELECT name, id FROM communication_channel")).all())
        if "Telegram" not in channel_ids:
            new_id = max(channel_ids.values()) + 1
            conn.execute(
                text(
                    "INSERT INTO communication_channel (id, name, module, package_name,"
                    " channel_data, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 'Telegram', 'Kernel::System::CommunicationChannel::Telegram',"
                    " 'ParityTest', :data, 1, :t, 1, :t, 1)"
                ),
                {"id": new_id, "data": b"---\n", "t": NOW},
            )
            channel_ids["Telegram"] = new_id
        for article_id, (ticket_id, channel) in sorted(ARTICLES.items()):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, :tid, 3, :cc, 1, 0, :t, 1, :t, 1)"
                ),
                {"aid": article_id, "tid": ticket_id, "cc": channel_ids[channel], "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO ticket_watcher (ticket_id, user_id, create_time, create_by,"
                " change_time, change_by) VALUES (:tid, :uid, :t, 1, :t, 1)"
            ),
            {"tid": T1, "uid": AGENT_A, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at) VALUES (:tid, :t)"
            ),
            {"tid": T6, "t": NOW},
        )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session(request: pytest.FixtureRequest) -> Any:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _ids(session: AsyncSession, stmt: Any) -> set[int]:
    if stmt is None:
        return set()
    return {t.id for t in (await session.execute(stmt)).scalars()} & set(TICKETS)


@pytest.mark.parametrize("params", PARAM_SETS, ids=lambda p: str(p))
async def test_bridge_matches_list_endpoint(params: dict[str, Any], session: AsyncSession) -> None:
    p = TicketListParams.model_validate(params)
    legacy = await TicketService(session)._filtered_ticket_stmt(
        AGENT_A,
        queue_id=p.queue_id,
        state_id=p.state_id,
        state_type=p.state_type,
        owner_id=p.owner_id,
        customer_id=p.customer_id,
        customer_user_id=p.customer_user_id,
        responsible_id=p.responsible_id,
        service_id=p.service_id,
        locked=p.locked,
        watcher_user_id=p.watcher_user_id,
        escalated=True if p.escalated else None,
        ai_escalated=True if p.ai_escalated else None,
        unassigned=p.unassigned,
        escalating_within=p.escalating_within,
        channel=p.channel,
        include_archived=p.include_archived,
    )
    scope = await ticket_query_scope(session, AGENT_A, include_archived=p.include_archived)
    compiled = await compile_ticket_filter(session, filter_from_list_params(p), scope)
    assert await _ids(session, legacy) == await _ids(session, compiled)


async def test_parity_cases_are_not_vacuous(session: AsyncSession) -> None:
    """The seed must make the interesting cases return different, non-empty sets."""
    scope = await ticket_query_scope(session, AGENT_A)

    async def ids(params: dict[str, Any]) -> set[int]:
        p = TicketListParams.model_validate(params)
        sc = await ticket_query_scope(session, AGENT_A, include_archived=p.include_archived)
        return await _ids(
            session, await compile_ticket_filter(session, filter_from_list_params(p), sc)
        )

    assert scope.queue_ids == frozenset({Q1, Q2})
    assert await ids({}) == {T1, T2, T5, T6}
    assert await ids({"include_archived": True}) == {T1, T2, T4, T5, T6}
    assert await ids({"channel": ["telegram"]}) == {T1}
    assert await ids({"channel": ["phone"]}) == {T5}
    assert await ids({"escalated": True}) == {T2, T6}
    assert await ids({"ai_escalated": True}) == {T6}
    assert await ids({"escalating_within": 7200}) == {T2, T5}
    assert await ids({"locked": True}) == {T2, T6}
    assert await ids({"watcher_user_id": AGENT_A}) == {T1}


def test_bridge_builds_one_condition_per_param() -> None:
    flt = filter_from_list_params(
        TicketListParams(
            state_type="todo",
            watcher_user_id=3,
            escalating_within=60,
            escalated=False,
            ai_escalated=True,
            include_archived=True,
            channel=["email"],
            locked=False,
        )  # fmt: skip
    )
    assert flt.version == 1
    assert isinstance(flt.where, AllOf)
    conds = {(c.field, c.op): c.value for c in flt.where.all if isinstance(c, Condition)}
    assert conds == {
        ("state_type", "in"): sorted(VIEW_STATE_TYPES["todo"]),
        ("watched_by", "eq"): 3,
        ("escalation_due_within", "lte"): 60,
        ("ai_escalated", "is_true"): None,
        ("channel", "in"): ["email"],
        ("locked", "is_false"): None,
    }
    assert filter_from_list_params(TicketListParams()) == Filter()
    single = filter_from_list_params(TicketListParams(state_type="closed"))
    assert isinstance(single.where, AllOf)
    assert single.where.all == [Condition(field="state_type", op="eq", value="closed")]
