"""``DELETE /api/v1/tickets/{id}/links/{target_id}`` and the link routes around it.

Both dialects. Seed band 940550-940599 (``test_link_parity.py`` owns 940500-940549). Every
route runs its reads inside the route's own transaction (skill
``tiqora-route-read-blocks-session-begin``), so these tests go through the HTTP layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 1, 8, 0, 0)
AGENT = 940551
G_RW, G_RO = 940560, 940561
Q_RW, Q_RO = 940562, 940563
T1, T2, T3 = 940570, 940571, 940572  # T1/T2 in the rw queue, T3 in the read-only one
TICKETS = (T1, T2, T3)
SYSCONFIG_BASE = 940580
_SC_LO, _SC_HI = SYSCONFIG_BASE, SYSCONFIG_BASE + 9
_MARK_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_history")

# Normal and ParentChild are both offered for tickets and no TypeGroup ties them together, so a
# pair can hold both and the ``link_type`` filter has something to distinguish.
CONFIG_TWO_TYPES: dict[str, Any] = {
    "LinkObject::Type###Normal": {"SourceName": "Normal", "TargetName": "Normal"},
    "LinkObject::Type###ParentChild": {"SourceName": "Parent", "TargetName": "Child"},
    "LinkObject::PossibleLink###0200": {
        "Object1": "Ticket",
        "Object2": "Ticket",
        "Type": "Normal",
    },
    "LinkObject::PossibleLink###0201": {
        "Object1": "Ticket",
        "Object2": "Ticket",
        "Type": "ParentChild",
    },
}


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _clean(conn: Any) -> None:
    keys = ", ".join(f"'{t}'" for t in TICKETS)
    ids = ", ".join(map(str, TICKETS))
    for sql in (
        f"DELETE FROM link_relation WHERE source_key IN ({keys}) OR target_key IN ({keys})",
        "DELETE FROM link_object WHERE name = 'Ticket' AND id NOT IN"
        " (SELECT source_object_id FROM link_relation)"
        " AND id NOT IN (SELECT target_object_id FROM link_relation)",
        f"DELETE FROM sysconfig_modified WHERE id BETWEEN {_SC_LO} AND {_SC_HI}",
        f"DELETE FROM sysconfig_default WHERE id BETWEEN {_SC_LO} AND {_SC_HI}",
        f"DELETE FROM ticket_history WHERE ticket_id IN ({ids})",
        f"DELETE FROM ticket WHERE id IN ({ids})",
        f"DELETE FROM queue WHERE id IN ({Q_RW}, {Q_RO})",
        f"DELETE FROM group_user WHERE group_id IN ({G_RW}, {G_RO})",
        f"DELETE FROM permission_groups WHERE id IN ({G_RW}, {G_RO})",
        f"DELETE FROM users WHERE id = {AGENT}",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _clean(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'linkdel.agent', 'x', 'Link', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        for gid, name in ((G_RW, "linkdel-rw"), (G_RO, "linkdel-ro")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": NOW},
            )
        for gid, keys in ((G_RW, ("ro", "rw")), (G_RO, ("ro",))):
            for key in keys:
                conn.execute(
                    text(
                        "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                        " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                    ),
                    {"u": AGENT, "g": gid, "k": key, "t": NOW},
                )
        for qid, gid in ((Q_RW, G_RW), (Q_RO, G_RO)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                    " create_by, change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1,"
                    " :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"LinkDelQueue{qid}", "g": gid, "t": NOW},
            )
        for tid, qid in ((T1, Q_RW), (T2, Q_RW), (T3, Q_RO)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'link delete', :q, 1, 1, :u, 1, 3, 4, 0, 0, 0, 0, 0, 0,"
                    " 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20261001{tid}", "q": qid, "u": AGENT, "t": NOW},
            )
    engine.dispose()


@dataclass
class Env:
    sync_url: str
    factory: async_sessionmaker[AsyncSession]
    marks: dict[str, int] = field(default_factory=dict)

    def configure(self, settings: dict[str, Any]) -> None:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            set_sysconfig_rows(conn, SYSCONFIG_BASE, settings)
        engine.dispose()

    def client(self) -> AsyncClient:
        async def _db() -> Any:
            async with self.factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            id=AGENT,
            login="linkdel.agent",
            first_name="Link",
            last_name="Agent",
            auth_method="session",
        )
        app.dependency_overrides[get_db] = _db
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def links(self, client: AsyncClient, ticket_id: int) -> list[tuple[int, str]]:
        resp = await client.get(f"/api/v1/tickets/{ticket_id}/links")
        assert resp.status_code == 200, resp.text
        return sorted((int(r["other_ticket_id"]), str(r["link_type"])) for r in resp.json())

    async def history(self, ticket_id: int) -> list[str]:
        async with self.factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT h.name FROM ticket_history h JOIN ticket_history_type ht"
                        " ON ht.id = h.history_type_id WHERE h.ticket_id = :t"
                        " AND ht.name = 'TicketLinkDelete' ORDER BY h.id"
                    ),
                    {"t": ticket_id},
                )
            ).all()
        return [str(r[0]) for r in rows]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, _MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from tiqora.api.v1 import tickets as tickets_api

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: factory)
    e = Env(sync_url, factory, marks)
    e.configure(CONFIG_TWO_TYPES)
    try:
        yield e
    finally:
        await engine.dispose()
        delete_above_marks(sync_url, marks)
        cleanup = create_engine(sync_url)
        with cleanup.begin() as conn:
            _clean(conn)
        cleanup.dispose()


async def _link(client: AsyncClient, source: int, target: int, link_type: str) -> int:
    resp = await client.post(
        f"/api/v1/tickets/{source}/links",
        json={"target_ticket_id": target, "link_type": link_type},
    )
    return resp.status_code


async def test_delete_api_removes_link_either_direction(env: Env) -> None:
    async with env.client() as client:
        assert await _link(client, T1, T2, "ParentChild") == 201
        # Deleted from the other end than the one that created it.
        resp = await client.delete(f"/api/v1/tickets/{T2}/links/{T1}")
        assert resp.status_code == 204
        assert resp.content == b""
        assert await env.links(client, T1) == []
        assert await env.links(client, T2) == []
    assert len(await env.history(T1)) == 1
    assert len(await env.history(T2)) == 1


async def test_delete_api_requires_rw_on_both(env: Env) -> None:
    async with env.client() as client:
        assert await _link(client, T1, T2, "Normal") == 201
        # Link T1 -> T3 directly: the agent only has ro on T3's queue, so the API refuses to
        # create it; seed the row by hand to test the delete path.
        assert await _link(client, T1, T3, "Normal") == 403
        engine = create_engine(env.sync_url)
        with engine.begin() as conn:
            ids = {
                table: conn.execute(
                    text(f"SELECT id FROM {table} WHERE name = :n"), {"n": name}
                ).scalar_one()
                for table, name in (
                    ("link_object", "Ticket"),
                    ("link_type", "Normal"),
                    ("link_state", "Valid"),
                )
            }
            obj, typ, state = ids["link_object"], ids["link_type"], ids["link_state"]
            conn.execute(
                text(
                    "INSERT INTO link_relation (source_object_id, source_key, target_object_id,"
                    " target_key, type_id, state_id, create_time, create_by)"
                    " VALUES (:o, :s, :o, :t, :ty, :st, :now, 1)"
                ),
                {"o": obj, "s": str(T1), "t": str(T3), "ty": typ, "st": state, "now": NOW},
            )
        engine.dispose()
        # rw on the source only: 403, and the link survives.
        assert (await client.delete(f"/api/v1/tickets/{T1}/links/{T3}")).status_code == 403
        # rw on the target only (T3 is the source of the call): 403 as well.
        assert (await client.delete(f"/api/v1/tickets/{T3}/links/{T1}")).status_code == 403
        assert (await env.links(client, T1)) == [(T2, "Normal"), (T3, "Normal")]
        # Unknown tickets are 404.
        assert (await client.delete(f"/api/v1/tickets/{T1}/links/9405999")).status_code == 404
        # Both rw: fine.
        assert (await client.delete(f"/api/v1/tickets/{T1}/links/{T2}")).status_code == 204
        assert await env.links(client, T1) == [(T3, "Normal")]


async def test_delete_api_link_type_filter(env: Env) -> None:
    async with env.client() as client:
        assert await _link(client, T1, T2, "Normal") == 201
        assert await _link(client, T1, T2, "ParentChild") == 201  # no TypeGroup configured
        assert await env.links(client, T1) == [(T2, "Normal"), (T2, "ParentChild")]
        resp = await client.delete(f"/api/v1/tickets/{T1}/links/{T2}?link_type=ParentChild")
        assert resp.status_code == 204
        assert await env.links(client, T1) == [(T2, "Normal")]
        # Absent link or type: still success, nothing removed.
        for query in ("?link_type=ParentChild", "?link_type=NoSuchLinkType"):
            resp = await client.delete(f"/api/v1/tickets/{T1}/links/{T2}{query}")
            assert resp.status_code == 204
        assert await env.links(client, T1) == [(T2, "Normal")]
        # No filter removes whatever types are left.
        assert await _link(client, T1, T2, "ParentChild") == 201
        assert (await client.delete(f"/api/v1/tickets/{T1}/links/{T2}")).status_code == 204
        assert await env.links(client, T1) == []
        assert (await client.delete(f"/api/v1/tickets/{T1}/links/{T2}")).status_code == 204
    assert len(await env.history(T1)) == 3  # ParentChild, Normal+ParentChild: one row per link


async def test_create_api_reports_link_conflicts_as_409(env: Env) -> None:
    env.configure({**CONFIG_TWO_TYPES, "LinkObject::TypeGroup###0001": ["Normal", "ParentChild"]})
    async with env.client() as client:
        assert await _link(client, T1, T2, "ParentChild") == 201
        assert await _link(client, T1, T2, "ParentChild") == 201  # idempotent
        assert await _link(client, T2, T1, "ParentChild") == 409  # opposite direction
        assert await _link(client, T1, T2, "Normal") == 409  # same type group
        # not a possible link: the type picker list (Task 12) refuses it before the service
        assert await _link(client, T1, T2, "NoSuchLinkType") == 422
        assert await env.links(client, T1) == [(T2, "ParentChild")]


async def test_create_api_without_sysconfig_link_rows_still_201(env: Env) -> None:
    env.configure({})  # a database whose configuration was never rebuilt
    async with env.client() as client:
        assert await _link(client, T1, T2, "Normal") == 201
        assert await env.links(client, T2) == [(T1, "Normal")]
