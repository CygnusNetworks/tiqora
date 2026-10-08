"""API tests for GET /ticket-filters/fields and POST /ticket-filters/count.

Seed ids use the 916xx range; the module deletes every row it created.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

NOW = datetime(2026, 2, 1, 12, 0, 0)
AGENT_A = 91600
G1 = 91610
Q1 = 91620
Q2 = 91621
T_OPEN = 91630  # Q1, open
T_ARCHIVED = 91631  # Q1, archived
T_Q2 = 91632  # Q2
TICKETS = (T_OPEN, T_ARCHIVED, T_Q2)
ROOT = 1

_SEEDED: list[str] = []


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _cleanup_statements() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("DELETE FROM ticket WHERE id IN :ids", {"ids": TICKETS}),
        ("DELETE FROM queue WHERE id IN :ids", {"ids": (Q1, Q2)}),
        ("DELETE FROM group_user WHERE group_id IN :ids", {"ids": (G1,)}),
        ("DELETE FROM permission_groups WHERE id IN :ids", {"ids": (G1,)}),
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
        engine.dispose()
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _run(conn, _cleanup_statements())
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'tfapi.agent.a', 'x', 'Filter', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_A, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'tfapi-g1', 1, :t, 1, :t, 1)"
            ),
            {"id": G1, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_A, "gid": G1, "t": NOW},
        )
        # Root (the admin) reads these queues too, so the admin-only archive switch is visible.
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": ROOT, "gid": G1, "t": NOW},
        )
        for qid in (Q1, Q2):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "name": f"TfApiQueue{qid}", "gid": G1, "t": NOW},
            )
        for tid, qid, archived in ((T_OPEN, Q1, 0), (T_ARCHIVED, Q1, 1), (T_Q2, Q2, 0)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Filter API ticket', :qid, 1, 1, 1, 1, 3, 4,"
                    " 0, 0, 0, 0, 0, 0, :arch, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20260116{tid}", "qid": qid, "arch": archived, "t": NOW},
            )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id, login=f"tfapi.{user_id}", first_name="T", last_name="F", auth_method="session"
    )
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _cond(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"where": {"field": field, "op": op, "value": value}}


async def test_fields_lists_catalog_with_options_source(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.get("/api/v1/ticket-filters/fields")
    assert resp.status_code == 200
    by_key = {f["key"]: f for f in resp.json()}
    queue = by_key["queue_id"]
    assert queue["options_source"] == "queue"
    assert queue["type"] == "id"
    assert queue["label_key"] == "ticketFilter.fields.queue_id"
    assert "in" in queue["ops"]
    assert by_key["channel"]["enum_values"] == ["email", "telegram", "webchat", "phone"]
    assert by_key["state_type"]["ops"] == sorted(["eq", "neq", "in", "not_in"])


async def test_count_in_queue(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": _cond("queue_id", "eq", Q1)},
        )
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}  # the archived ticket is out of scope


async def test_count_empty_filter_counts_scope_only(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post("/api/v1/ticket-filters/count", json={"filter": {}})
    assert resp.status_code == 200
    assert resp.json() == {"count": 2}


async def test_invalid_field_is_422_with_path(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": _cond("no_such_field", "eq", 1)},
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["path"] == "where.field"
    assert "no_such_field" in detail["message"]


async def test_invalid_value_is_422_with_path(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": {"where": {"all": [_cond("queue_id", "eq", "x")["where"]]}}},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["path"] == "where.all[0].value"


async def test_malformed_filter_shape_is_422(factory: async_sessionmaker[AsyncSession]) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": {"version": 2}},
        )
    assert resp.status_code == 422


async def test_include_archived_ignored_for_non_admin(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory, AGENT_A) as client:
        resp = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": _cond("queue_id", "eq", Q1), "include_archived": True},
        )
    assert resp.status_code == 200
    assert resp.json() == {"count": 1}


async def test_include_archived_honoured_for_admin(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(factory, ROOT) as client:
        base = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": _cond("queue_id", "eq", Q1)},
        )
        archived = await client.post(
            "/api/v1/ticket-filters/count",
            json={"filter": _cond("queue_id", "eq", Q1), "include_archived": True},
        )
    assert base.status_code == archived.status_code == 200
    assert archived.json()["count"] == base.json()["count"] + 1
