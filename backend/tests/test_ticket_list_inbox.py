"""DB tests for the inbox additions to the agent ticket list.

Covers the ``todo``/``open_only`` state views, the ``unassigned`` and
``escalating_within`` filters, ``sort=activity`` (newest article, falling
back to the ticket's creation), ``last_article_time``/``last_sender_type``
on list items and the ``GET /tickets/facets`` segment/chip counts.

Seed ids use the 876xx range — disjoint from other DB test files sharing the
session-scoped testcontainer DB. The module deletes everything it seeds
after its last test (``_delete_seeded_rows``), so it does not leak rows.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketService

pytestmark = pytest.mark.db

AGENT_ID = 87600
GROUP_ID = 87620
QUEUE_ID = 87600

# Znuny initial_insert ids.
ROOT_ID = 1
STATE_NEW, STATE_CLOSED, STATE_OPEN, STATE_PENDING = 1, 2, 4, 6
LOCK_UNLOCK, LOCK_LOCK = 1, 2
SENDER_AGENT, SENDER_SYSTEM, SENDER_CUSTOMER = 1, 2, 3

T_NEW_ROOT = 87601  # new, owned by root, no articles, created 10:00
T_OPEN_LOCKED = 87602  # open, locked, created 08:00, newest article 12:00
T_PENDING_ROOT = 87603  # pending reminder, root, escalates in 10 min
T_CLOSED = 87604  # closed, escalation long past
T_OPEN_OVERDUE = 87605  # open, escalation_update_time long past
TICKET_IDS = (T_NEW_ROOT, T_OPEN_LOCKED, T_PENDING_ROOT, T_CLOSED, T_OPEN_OVERDUE)
ARTICLE_IDS = (87611, 87612, 87613, 87614, 87615, 87616)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 6, 1, hour, minute, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    """The DELETEs removing everything :func:`_seed` creates (children first).

    Run before seeding (idempotent re-runs) and after the module (no leak).
    """
    articles = {f"a{i}": aid for i, aid in enumerate(ARTICLE_IDS)}
    a_in = ", ".join(f":{k}" for k in articles)
    tickets = {f"t{i}": tid for i, tid in enumerate(TICKET_IDS)}
    t_in = ", ".join(f":{k}" for k in tickets)
    return (
        (f"DELETE FROM article WHERE id IN ({a_in})", articles),
        (f"DELETE FROM ticket WHERE id IN ({t_in})", tickets),
        ("DELETE FROM queue WHERE id = :id", {"id": QUEUE_ID}),
        (
            "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
            {"uid": AGENT_ID, "gid": GROUP_ID},
        ),
        ("DELETE FROM permission_groups WHERE id = :id", {"id": GROUP_ID}),
        ("DELETE FROM users WHERE id = :id", {"id": AGENT_ID}),
    )


_SEEDED: set[str] = set()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in _SEEDED:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt, params in _seed_statements():
                conn.execute(text(stmt), params)
        engine.dispose()
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    now = int(time.time())
    engine = create_engine(sync_url)
    _SEEDED.add(sync_url)
    with engine.begin() as conn:
        # The list enrichment reads tiqora_ai_* tables; without them its
        # rollback-on-missing-table path expires the loaded tickets.
        TiqoraBase.metadata.create_all(conn)
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'reader.inbox876', 'x', 'Inbox', 'Reader', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "t": _t(6)},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'inbox876-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP_ID, "t": _t(6)},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_ID, "gid": GROUP_ID, "t": _t(6)},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Inbox876Queue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE_ID, "gid": GROUP_ID, "t": _t(6)},
        )

        # (id, state, owner, lock, created, escalation_time, escalation_update_time)
        for tid, state, owner, lock, created, esc, esc_update in (
            (T_NEW_ROOT, STATE_NEW, ROOT_ID, LOCK_UNLOCK, _t(10), 0, 0),
            (T_OPEN_LOCKED, STATE_OPEN, AGENT_ID, LOCK_LOCK, _t(8), 0, 0),
            (T_PENDING_ROOT, STATE_PENDING, ROOT_ID, LOCK_UNLOCK, _t(9), now + 600, 0),
            (T_CLOSED, STATE_CLOSED, AGENT_ID, LOCK_UNLOCK, _t(11), 1000, 0),
            (T_OPEN_OVERDUE, STATE_OPEN, AGENT_ID, LOCK_UNLOCK, _t(7), 0, 1000),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :qid, :lock, 1,"
                    " :uid, 1, 3, :state,"
                    " 0, 0, :esc, :esc_update, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2024060187{tid}",
                    "title": f"Inbox ticket {tid}",
                    "qid": QUEUE_ID,
                    "lock": lock,
                    "uid": owner,
                    "state": state,
                    "esc": esc,
                    "esc_update": esc_update,
                    "t": created,
                },
            )

        # T_OPEN_LOCKED: customer at 08:00, then system + agent note both at
        # 12:00 — the higher article id (the agent note) wins the tie.
        for aid, tid, sender, created in (
            (87611, T_OPEN_LOCKED, SENDER_CUSTOMER, _t(8)),
            (87612, T_OPEN_LOCKED, SENDER_SYSTEM, _t(12)),
            (87613, T_OPEN_LOCKED, SENDER_AGENT, _t(12)),
            (87614, T_PENDING_ROOT, SENDER_CUSTOMER, _t(9, 30)),
            (87615, T_CLOSED, SENDER_CUSTOMER, _t(11)),
            (87616, T_OPEN_OVERDUE, SENDER_CUSTOMER, _t(7, 30)),
        ):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, :tid, :sender, 1, :visible, 0, :t, 1, :t, 1)"
                ),
                {
                    "aid": aid,
                    "tid": tid,
                    "sender": sender,
                    # The agent note is internal — it still counts as activity.
                    "visible": 0 if sender == SENDER_AGENT else 1,
                    "t": created,
                },
            )
    engine.dispose()


URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]


async def _ids(ts: TicketService, **kwargs: Any) -> set[int]:
    page = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, limit=50, **kwargs)
    return {i.id for i in page.items}


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_state_views_and_filters(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        ts = TicketService(session)
        assert await _ids(ts, state_type="todo") == {T_NEW_ROOT, T_OPEN_LOCKED, T_OPEN_OVERDUE}
        assert await _ids(ts, state_type="open_only") == {T_OPEN_LOCKED, T_OPEN_OVERDUE}
        # "open" keeps its viewable meaning (new + open + pending).
        assert await _ids(ts, state_type="open") == {
            T_NEW_ROOT,
            T_OPEN_LOCKED,
            T_PENDING_ROOT,
            T_OPEN_OVERDUE,
        }

        assert await _ids(ts, unassigned=True) == {T_NEW_ROOT, T_PENDING_ROOT}
        assert await _ids(ts, unassigned=False) == {T_OPEN_LOCKED, T_CLOSED, T_OPEN_OVERDUE}
        assert await _ids(ts, state_type="todo", unassigned=True) == {T_NEW_ROOT}

        # Overdue ones are included; the pending one escalates in 10 minutes.
        assert await _ids(ts, escalating_within=3600) == {
            T_PENDING_ROOT,
            T_CLOSED,
            T_OPEN_OVERDUE,
        }
        assert await _ids(ts, escalating_within=0) == {T_CLOSED, T_OPEN_OVERDUE}
        assert await _ids(ts, escalated=True) == {T_CLOSED, T_OPEN_OVERDUE}
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_deadline_sort_puts_the_most_urgent_first(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The pinned SLA block fetches its top N with sort=deadline. Sorting by
    activity and re-sorting on the client dropped the most overdue tickets
    whenever they were not also among the most recently active."""
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        ts = TicketService(session)
        page = await ts.list_tickets(
            AGENT_ID, queue_id=QUEUE_ID, sort="deadline", order="asc", limit=50
        )
        # Both overdue ones share the epoch (1000, once as escalation_time and
        # once as escalation_update_time) and tie-break on id; the pending one
        # is due in 10 min; tickets without any deadline come last.
        assert [i.id for i in page.items] == [
            T_CLOSED,
            T_OPEN_OVERDUE,
            T_PENDING_ROOT,
            T_NEW_ROOT,
            T_OPEN_LOCKED,
        ]
        top = await ts.list_tickets(
            AGENT_ID,
            queue_id=QUEUE_ID,
            sort="deadline",
            order="asc",
            escalating_within=3600,
            limit=1,
        )
        assert [i.id for i in top.items] == [T_CLOSED]
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_activity_sort_and_last_article(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        ts = TicketService(session)

        created = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, sort="created", limit=50)
        assert [i.id for i in created.items] == [
            T_CLOSED,
            T_NEW_ROOT,
            T_PENDING_ROOT,
            T_OPEN_LOCKED,
            T_OPEN_OVERDUE,
        ]

        # T_OPEN_LOCKED was created early but has the newest article (12:00);
        # T_NEW_ROOT has no article and sorts by its creation (10:00).
        activity = await ts.list_tickets(AGENT_ID, queue_id=QUEUE_ID, sort="activity", limit=50)
        assert [i.id for i in activity.items] == [
            T_OPEN_LOCKED,
            T_CLOSED,
            T_NEW_ROOT,
            T_PENDING_ROOT,
            T_OPEN_OVERDUE,
        ]
        asc = await ts.list_tickets(
            AGENT_ID, queue_id=QUEUE_ID, sort="activity", order="asc", limit=50
        )
        assert [i.id for i in asc.items] == [i.id for i in reversed(activity.items)]

        by_id = {i.id: i for i in activity.items}
        locked_item = by_id[T_OPEN_LOCKED]
        assert locked_item.last_article_time is not None
        assert locked_item.last_article_time.replace(tzinfo=None) == _t(12)
        assert locked_item.last_sender_type == "agent"
        assert by_id[T_PENDING_ROOT].last_sender_type == "customer"
        pending_time = by_id[T_PENDING_ROOT].last_article_time
        assert pending_time is not None
        assert pending_time.replace(tzinfo=None) == _t(9, 30)
        assert by_id[T_NEW_ROOT].last_article_time is None
        assert by_id[T_NEW_ROOT].last_sender_type is None

        exported = [item async for item in ts.iter_tickets_for_export(AGENT_ID, queue_id=QUEUE_ID)]
        assert {i.id for i in exported} == set(TICKET_IDS)
        exported_sorted = [
            item.id
            async for item in ts.iter_tickets_for_export(
                AGENT_ID, queue_id=QUEUE_ID, sort="activity", unassigned=True
            )
        ]
        assert exported_sorted == [T_NEW_ROOT, T_PENDING_ROOT]
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_facet_counts(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        ts = TicketService(session)

        assert await ts.facet_counts(AGENT_ID, queue_id=QUEUE_ID) == {
            "states": {
                "todo": 3,
                "new": 1,
                "open_only": 2,
                "pending": 1,
                "closed": 1,
                "all": 5,
            },
            "flags": {"escalated": 2, "locked": 1, "unassigned": 2},
            "channels": {"email": 5, "telegram": 0, "webchat": 0},
        }

        # states ignore the state filter but apply the flag; flags apply the
        # state filter but ignore the flags.
        assert await ts.facet_counts(
            AGENT_ID, queue_id=QUEUE_ID, state_type="todo", unassigned=True
        ) == {
            "states": {
                "todo": 1,
                "new": 1,
                "open_only": 0,
                "pending": 1,
                "closed": 0,
                "all": 2,
            },
            "flags": {"escalated": 1, "locked": 1, "unassigned": 1},
            # Channels apply the state filter and the flags (all but channel).
            "channels": {"email": 1, "telegram": 0, "webchat": 0},
        }

        # No ro permission at all -> everything zero.
        empty = await ts.facet_counts(ROOT_ID + 999_999, queue_id=QUEUE_ID)
        assert set(empty["states"].values()) == {0}
        assert set(empty["flags"].values()) == {0}
        assert set(empty["channels"].values()) == {0}
    await engine.dispose()


@pytest.mark.asyncio
async def test_facets_and_list_routes(mariadb_znuny_url: str) -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=AGENT_ID,
        login="reader.inbox876",
        first_name="Inbox",
        last_name="Reader",
        auth_method="session",
    )
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        facets = await client.get(
            "/api/v1/tickets/facets",
            params={"queue_id": QUEUE_ID, "state_type": "todo", "locked": "true"},
        )
        listed = await client.get(
            "/api/v1/tickets",
            params={
                "queue_id": QUEUE_ID,
                "state_type": "todo",
                "unassigned": "false",
                "escalating_within": 3600,
                "sort": "activity",
            },
        )
        bad = await client.get("/api/v1/tickets", params={"escalating_within": -1})
        csv_resp = await client.get(
            "/api/v1/tickets/export.csv",
            params={"queue_id": QUEUE_ID, "unassigned": "true", "sort": "activity"},
        )
    await engine.dispose()

    assert facets.status_code == 200, facets.text
    assert facets.json() == {
        # Only T_OPEN_LOCKED is locked.
        "states": {"todo": 1, "new": 0, "open_only": 1, "pending": 0, "closed": 0, "all": 1},
        "flags": {"escalated": 1, "locked": 1, "unassigned": 1},
        "channels": {"email": 1, "telegram": 0, "webchat": 0},
    }

    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [i["id"] for i in items] == [T_OPEN_OVERDUE]
    assert items[0]["last_sender_type"] == "customer"
    assert items[0]["last_article_time"].startswith("2024-06-01T07:30:00")

    assert bad.status_code == 422

    assert csv_resp.status_code == 200
    body = csv_resp.content.decode("utf-8-sig").splitlines()[1:]
    assert [line.split(";")[0] for line in body] == [
        f"2024060187{T_NEW_ROOT}",
        f"2024060187{T_PENDING_ROOT}",
    ]
