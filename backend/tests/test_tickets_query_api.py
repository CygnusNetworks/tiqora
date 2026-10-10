"""HTTP tests for ``POST /api/v1/tickets/query`` (both dialects).

Seed ids 48170-48199 (titles start with ``rep48``): agent 48170 (``ro`` on group 48171), readable
queue 48171, foreign queue 48172 (group 48172, nobody holds it); tickets 48180 (alpha, readable,
2026-01-02), 48181 (beta, readable, archived, 2026-01-03) and 48182 (gamma, foreign queue).
Every row the module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any, get_args

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.api.v1.tickets as tickets_api
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.schemas import TicketSortKey
from tiqora.domain.ticket_service import TicketService
from tiqora.reporting.sql import ReportQueryTimeout

pytestmark = pytest.mark.db

LO, HI = 48170, 48199
AGENT, GROUP, QUEUE, FOREIGN_GROUP, FOREIGN_QUEUE = 48170, 48171, 48171, 48172, 48172
T_ALPHA, T_BETA, T_GAMMA = 48180, 48181, 48182
NOW = datetime(2026, 1, 1, 12, 0, 0)
_SEEDED: set[str] = set()

_CLEANUP = (
    f"DELETE FROM ticket WHERE id BETWEEN {T_ALPHA} AND {T_GAMMA}",
    f"DELETE FROM queue WHERE id BETWEEN {LO} AND {HI}",
    f"DELETE FROM group_user WHERE user_id BETWEEN {LO} AND {HI}",
    f"DELETE FROM permission_groups WHERE id BETWEEN {LO} AND {HI}",
    f"DELETE FROM users WHERE id BETWEEN {LO} AND {HI}",
)
#: per test: drop every membership except the seeded ``ro`` one (``_grant_admin`` must not leak)
_RESET = (f"DELETE FROM group_user WHERE user_id BETWEEN {LO} AND {HI} AND group_id <> {GROUP}",)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    _run_sql(sync_url, _CLEANUP)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)  # tiqora_settings (reporting budget)
    audit = {"t": NOW}
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                f" VALUES ({AGENT}, 'rep48.query', 'x', 'Quinn', 'Query', 1, :t, 1, :t, 1)"
            ),
            audit,
        )
        for gid in (GROUP, FOREIGN_GROUP):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    f" VALUES ({gid}, 'rep48-g{gid}', 1, :t, 1, :t, 1)"
                ),
                audit,
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    f" VALUES ({gid}, 'Rep48Queue{gid}', {gid}, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                audit,
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                f" VALUES ({AGENT}, {GROUP}, 'ro', :t, 1, :t, 1)"
            ),
            audit,
        )
        rows = (
            (T_ALPHA, "alpha", QUEUE, 0, datetime(2026, 1, 2, 9, 0)),
            (T_BETA, "beta", QUEUE, 1, datetime(2026, 1, 3, 9, 0)),
            (T_GAMMA, "gamma", FOREIGN_QUEUE, 0, datetime(2026, 1, 4, 9, 0)),
        )
        for tid, word, qid, archived, created in rows:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    f" VALUES ({tid}, '2026010{tid}', 'rep48 {word}', {qid}, 1, 1, 1, 1, 3, 4,"
                    f" 0, 0, 0, 0, 0, 0, {archived}, :c, 1, :c, 1)"
                ),
                {"c": created},
            )
    engine.dispose()


def _grant_admin(sync_url: str, user_id: int) -> None:
    """``rw`` on the Znuny group named ``admin`` (what ``PermissionEngine.is_admin`` checks)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        gid = conn.execute(text("SELECT id FROM permission_groups WHERE name = 'admin'")).scalar()
        assert gid is not None, "the Znuny seed has no group 'admin'"
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'rw', :t, 1, :t, 1)"
            ),
            {"uid": user_id, "gid": gid, "t": NOW},
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, client: AsyncClient) -> None:
        self.sync_url = sync_url
        self.client = client
        self.user_id = AGENT

    def as_user(self, user_id: int) -> None:
        self.user_id = user_id


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    if sync_url not in _SEEDED:
        _seed(sync_url)
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=holder[0].user_id,
        login=f"rep48.{holder[0].user_id}",
        first_name="Quinn",
        last_name="Query",
        auth_method="session",
    )
    app.dependency_overrides[get_db] = _db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, c))
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


def _cond(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def test_sort_keys_match_service() -> None:
    assert set(get_args(TicketSortKey)) == set(TicketService._SORT_COLUMNS)


async def test_query_filters_sorts_and_pages(env: Env) -> None:
    env.as_user(AGENT)
    body = {
        "filter": {"where": _cond("title", "starts_with", "rep48")},
        "sort": "created",
        "order": "asc",
        "limit": 1,
    }
    page = (await env.client.post("/api/v1/tickets/query", json=body)).json()
    # archived (beta) and foreign-queue (gamma) tickets are excluded
    assert page["total"] == 1
    assert [t["id"] for t in page["items"]] == [T_ALPHA]
    assert page["limit"] == 1 and page["offset"] == 0


async def test_query_equals_list_tickets_for_same_params(env: Env) -> None:
    env.as_user(AGENT)
    listed = (
        await env.client.get("/api/v1/tickets", params={"queue_id": QUEUE, "sort": "tn"})
    ).json()
    queried = (
        await env.client.post(
            "/api/v1/tickets/query",
            json={"filter": {"where": _cond("queue_id", "eq", QUEUE)}, "sort": "tn"},
        )
    ).json()
    assert [t["id"] for t in listed["items"]] == [T_ALPHA]
    assert queried == listed  # same enrichment, same page shape


async def test_include_archived_admin_only(env: Env) -> None:
    env.as_user(AGENT)
    body = {"filter": {"where": _cond("queue_id", "eq", QUEUE)}, "include_archived": True}
    assert (await env.client.post("/api/v1/tickets/query", json=body)).json()["total"] == 1
    _grant_admin(env.sync_url, AGENT)  # rw on group "admin" for this test only
    page = (await env.client.post("/api/v1/tickets/query", json=body)).json()
    assert page["total"] == 2
    assert {t["id"] for t in page["items"]} == {T_ALPHA, T_BETA}


async def test_pagination_and_total_are_consistent(env: Env) -> None:
    env.as_user(AGENT)
    _grant_admin(env.sync_url, AGENT)
    base = {
        "filter": {"where": _cond("queue_id", "eq", QUEUE)},
        "include_archived": True,
        "sort": "created",
        "order": "asc",
        "limit": 1,
    }
    first = (await env.client.post("/api/v1/tickets/query", json=base)).json()
    second = (await env.client.post("/api/v1/tickets/query", json={**base, "offset": 1})).json()
    assert [t["id"] for t in first["items"]] == [T_ALPHA]
    assert [t["id"] for t in second["items"]] == [T_BETA]
    assert first["total"] == second["total"] == 2
    assert second["offset"] == 1


async def test_foreign_queue_in_filter_matches_nothing(env: Env) -> None:
    env.as_user(AGENT)
    resp = await env.client.post(
        "/api/v1/tickets/query",
        json={"filter": {"where": _cond("queue_id", "in", [FOREIGN_QUEUE, QUEUE])}},
    )
    assert resp.status_code == 200
    assert [t["id"] for t in resp.json()["items"]] == [T_ALPHA]
    only_foreign = await env.client.post(
        "/api/v1/tickets/query",
        json={"filter": {"where": _cond("queue_id", "eq", FOREIGN_QUEUE)}},
    )
    assert only_foreign.status_code == 200
    assert only_foreign.json()["items"] == [] and only_foreign.json()["total"] == 0


async def test_user_without_queues_gets_an_empty_page(env: Env) -> None:
    env.as_user(48199)  # unknown user: holds no group
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}})
    assert resp.status_code == 200
    assert resp.json()["items"] == [] and resp.json()["total"] == 0


async def test_invalid_filter_is_422_with_path(env: Env) -> None:
    env.as_user(AGENT)
    resp = await env.client.post(
        "/api/v1/tickets/query", json={"filter": {"where": _cond("nope", "eq", 1)}}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["path"] == "where.field"
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}, "sort": "bogus"})
    assert resp.status_code == 422
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}, "limit": 201})
    assert resp.status_code == 422


async def test_query_route_is_not_shadowed_by_ticket_id(env: Env) -> None:
    env.as_user(AGENT)
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_timeout_is_504_with_message(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise ReportQueryTimeout("statement exceeded 30s")

    monkeypatch.setattr(TicketService, "page_from_stmt", _boom)
    env.as_user(AGENT)
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}})
    assert resp.status_code == 504
    assert resp.json()["detail"]["message"] == tickets_api.QUERY_TIMEOUT_MESSAGE
