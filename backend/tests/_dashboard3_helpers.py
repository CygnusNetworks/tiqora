"""Shared world for the dashboards phase-2 DB/API tests (band 61200-61599, names ``dash3``).

``dash3a`` (A, 61210): ``ro`` + ``rw`` on group 61215 (queue Q1 61213).
``dash3b`` (B, 61211): ``ro`` on group 61216 (queue Q2 61214) only.
``dash3admin`` (ADMIN, 61212): ``rw`` on the ``admin`` group.
``open_env3`` seeds the world for every test and removes everything again on exit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._dashboard_helpers import FakeRedis, to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.dashboard import store
from tiqora.dashboard.schemas import DashboardCreateIn, DashboardOut
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

__all__ = [
    "A",
    "ADMIN",
    "B",
    "FakeRedis",
    "GROUP_1",
    "GROUP_2",
    "Q1",
    "Q2",
    "ROLE_A",
    "Env3",
    "cleanup_world",
    "open_env3",
    "seed_world",
    "to_async_url",
]

A, B, ADMIN = 61210, 61211, 61212
Q1, Q2, GROUP_1, GROUP_2 = 61213, 61214, 61215, 61216
#: role of user A (role-template tests)
ROLE_A = 61218
_P = {"ulo": 61210, "uhi": 61219, "glo": 61215, "ghi": 61216, "olo": 61200, "ohi": 61599}
_NAMES = {
    A: ("dash3a", "Anna", "Alpha"),
    B: ("dash3b", "Boris", "Beta"),
    ADMIN: ("dash3admin", "Ada", "Admin"),
}
_OWN = "(owner_user_id BETWEEN :olo AND :ohi OR name LIKE 'dash3%')"
_CLEANUP = (
    "DELETE FROM tiqora_dashboard_kiosk_item WHERE token_id IN"
    " (SELECT id FROM tiqora_dashboard_kiosk_token WHERE created_by BETWEEN :olo AND :ohi)",
    "DELETE FROM tiqora_dashboard_kiosk_item WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    "DELETE FROM tiqora_dashboard_kiosk_token WHERE created_by BETWEEN :olo AND :ohi",
    "DELETE FROM tiqora_dashboard_counter_sample WHERE user_id BETWEEN :olo AND :ohi",
    f"DELETE FROM tiqora_dashboard_widget WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard_group WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard_role WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard WHERE {_OWN}",
    "DELETE FROM group_user WHERE user_id BETWEEN :ulo AND :uhi OR group_id BETWEEN :glo AND :ghi",
    "DELETE FROM role_user WHERE user_id BETWEEN :ulo AND :uhi OR role_id = 61218",
    "DELETE FROM roles WHERE id = 61218",
    "DELETE FROM queue WHERE id BETWEEN 61213 AND 61214",
    "DELETE FROM permission_groups WHERE id BETWEEN :glo AND :ghi",
    "DELETE FROM users WHERE id BETWEEN :ulo AND :uhi",
)


def cleanup_world(sync_url: str) -> None:
    """Delete the dashboard rows of the band, then the seeded Znuny rows."""
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _P)
    engine.dispose()


def seed_world(sync_url: str) -> None:
    """Idempotent: removes the band, then inserts users, groups, queues and memberships."""
    cleanup_world(sync_url)
    t = datetime(2026, 10, 1, 8, 0)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, (login, first, last) in _NAMES.items():
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :ln, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "ln": last, "t": t},
            )
        for gid, qid in ((GROUP_1, Q1), (GROUP_2, Q2)):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"dash3-group-{gid}", "t": t},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"dash3-queue-{qid}", "g": gid, "t": t},
            )
        conn.execute(
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'dash3-role', 1, :t, 1, :t, 1)"
            ),
            {"id": ROLE_A, "t": t},
        )
        conn.execute(
            text(
                "INSERT INTO role_user (user_id, role_id, create_time, create_by,"
                " change_time, change_by) VALUES (:u, :r, :t, 1, :t, 1)"
            ),
            {"u": A, "r": ROLE_A, "t": t},
        )
        admin_group = int(
            conn.execute(text("SELECT id FROM permission_groups WHERE name = 'admin'")).scalar_one()
        )
        grants = [(A, GROUP_1, "ro"), (A, GROUP_1, "rw"), (B, GROUP_2, "ro")]
        grants.append((ADMIN, admin_group, "rw"))
        for uid, gid, key in grants:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": t},
            )
    engine.dispose()


def _agent(uid: int) -> AuthenticatedUser:
    login, first, last = _NAMES[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


class Env3:
    def __init__(
        self,
        sync_url: str,
        factory: async_sessionmaker[AsyncSession],
        client: AsyncClient,
        current: list[AuthenticatedUser],
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client  #: acts as ``as_user``'s user (default A)
        self._current = current
        self.app: Any = None

    def as_user(self, uid: int) -> AsyncClient:
        self._current[0] = _agent(uid)
        return self.client

    async def run(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kw: Any) -> Any:
        """Run a store function in its own session and commit."""
        async with self.factory() as s:
            result = await fn(s, *args, **kw)
            await s.commit()
            return result

    def sql(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            res = conn.execute(text(sql), params)
            rows = list(res.all()) if res.returns_rows else []
        engine.dispose()
        return rows

    # --- dashboards ---------------------------------------------------------------

    async def create_personal(self, *, owner: int, name: str = "dash3 board") -> DashboardOut:
        return await self.run(  # type: ignore[no-any-return]
            store.create_dashboard, owner, DashboardCreateIn(name=name), admin=False
        )

    async def get(self, user: int, dashboard_id: int) -> DashboardOut:
        return await self.run(store.get_dashboard, dashboard_id, user, admin=user == ADMIN)  # type: ignore[no-any-return]

    def share(self, dashboard_id: int, group_id: int) -> None:
        """Share a personal dashboard with a group (direct rows, no sharing-rights check)."""
        self.sql(
            "INSERT INTO tiqora_dashboard_group (dashboard_id, group_id) VALUES (:d, :g)",
            d=dashboard_id,
            g=group_id,
        )
        self.sql("UPDATE tiqora_dashboard SET kind = 'shared' WHERE id = :d", d=dashboard_id)

    async def patch_filters_raw(
        self, user: int, dashboard_id: int, filters: dict[str, Any], *, version: int
    ) -> Response:
        return await self.as_user(user).patch(
            f"/api/v1/dashboards/{dashboard_id}/filters",
            json={"filters": filters, "version": version},
        )

    async def patch_filters(
        self, user: int, dashboard_id: int, filters: dict[str, Any], *, version: int
    ) -> DashboardOut:
        r = await self.patch_filters_raw(user, dashboard_id, filters, version=version)
        assert r.status_code == 200, r.text
        return DashboardOut.model_validate(r.json())

    def stored_filters(self, dashboard_id: int) -> str:
        rows = self.sql("SELECT filters FROM tiqora_dashboard WHERE id = :d", d=dashboard_id)
        return str(rows[0][0])

    def set_stored_filters(self, dashboard_id: int, raw: str) -> None:
        self.sql("UPDATE tiqora_dashboard SET filters = :f WHERE id = :d", f=raw, d=dashboard_id)


@asynccontextmanager
async def open_env3(sync_url: str) -> AsyncIterator[Env3]:
    """Seed the world, yield an ``Env3`` and remove every row again."""
    seed_world(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    current = [_agent(A)]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: factory
    app.dependency_overrides[get_current_user] = lambda: current[0]
    env: Env3 | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            env = Env3(sync_url, factory, c, current)
            env.app = app
            yield env
    finally:
        await engine.dispose()
        cleanup_world(sync_url)
