"""Shared world and ``Env`` for the dashboard DB/API tests (band 49710-49749, names ``dash497``).

A: ``ro`` on GROUP_1 (queue QUEUE_1) and member of ROLE_1; B: ``ro`` on GROUP_2 (queue
QUEUE_2); C: no groups, member of ROLE_2 (invalid role, ``roles.valid_id = 2``).
``open_env`` seeds the world for every test and removes everything again on exit.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.dashboard import start, store
from tiqora.dashboard.schemas import (
    DashboardCreateIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardTabOut,
    WidgetIn,
    WidgetOut,
    WidgetSettingsPatchIn,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now

USER_A, USER_B, USER_C = 49710, 49711, 49712
GROUP_1, GROUP_2, QUEUE_1, QUEUE_2, ROLE_1, ROLE_2 = 49715, 49716, 49717, 49718, 49720, 49721
#: owner of ``foreign_dashboard_id``: no Znuny user (the owner column has no FK)
OTHER_OWNER = 49713
TICKETS: dict[str, int] = {
    "a_open": 49730,
    "a_new": 49731,
    "a_escalated": 49732,
    "a_pending": 49733,
    "unowned_new": 49734,
    "b_open": 49735,
}
now = utc_now().replace(microsecond=0)
today_noon_utc = now.replace(hour=12, minute=0, second=0)

_P = {
    "ulo": 49710,
    "uhi": 49714,
    "glo": 49715,
    "ghi": 49716,
    "qlo": 49717,
    "qhi": 49718,
    "rlo": 49720,
    "rhi": 49721,
    "tlo": 49730,
    "thi": 49749,
    "dlo": 49700,
    "dhi": 49999,
    "clo": 49800,
    "chi": 49809,
}
_OWN = "(owner_user_id BETWEEN :dlo AND :dhi OR name LIKE 'dash497%')"
_CLEANUP = (
    "DELETE FROM tiqora_dashboard_counter_sample WHERE user_id BETWEEN :dlo AND :dhi",
    f"DELETE FROM tiqora_dashboard_widget WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard_group WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard_role WHERE dashboard_id IN"
    f" (SELECT id FROM tiqora_dashboard WHERE {_OWN})",
    f"DELETE FROM tiqora_dashboard WHERE {_OWN}",
    "DELETE FROM calendar_appointment_ticket WHERE calendar_id BETWEEN :clo AND :chi",
    "DELETE FROM calendar_appointment WHERE calendar_id BETWEEN :clo AND :chi",
    "DELETE FROM calendar WHERE id BETWEEN :clo AND :chi",
    "DELETE FROM tiqora_ai_ticket_state WHERE ticket_id BETWEEN :tlo AND :thi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :tlo AND :thi",
    "DELETE FROM article WHERE ticket_id BETWEEN :tlo AND :thi",
    "DELETE FROM ticket WHERE id BETWEEN :tlo AND :thi",
    "DELETE FROM group_user WHERE user_id BETWEEN :ulo AND :uhi OR group_id BETWEEN :glo AND :ghi",
    "DELETE FROM role_user WHERE user_id BETWEEN :ulo AND :uhi OR role_id BETWEEN :rlo AND :rhi",
    "DELETE FROM roles WHERE id BETWEEN :rlo AND :rhi",
    "DELETE FROM queue WHERE id BETWEEN :qlo AND :qhi",
    "DELETE FROM permission_groups WHERE id BETWEEN :glo AND :ghi",
    "DELETE FROM users WHERE id BETWEEN :ulo AND :uhi",
)
_USER_NAMES = {
    USER_A: ("dash497.a", "Anna", "Alpha"),
    USER_B: ("dash497.b", "Boris", "Beta"),
    USER_C: ("dash497.c", "Carla", "Gamma"),
}


def to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def cleanup_world(sync_url: str) -> None:
    """Delete the dashboard rows of the band, then the seeded Znuny rows."""
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _P)
    engine.dispose()


def seed_world(sync_url: str) -> None:
    """Idempotent: removes the band, then inserts users, groups, queues, roles, tickets."""
    cleanup_world(sync_url)
    t = datetime(2026, 10, 1, 8, 0)
    epoch = int(now.timestamp())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        state = {
            name: int(
                conn.execute(
                    text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
                ).scalar_one()
            )
            for name in ("new", "open", "pending reminder")
        }
        for uid, (login, first, last) in _USER_NAMES.items():
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :ln, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "ln": last, "t": t},
            )
        for gid, qid in ((GROUP_1, QUEUE_1), (GROUP_2, QUEUE_2)):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"dash497-group-{gid}", "t": t},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"dash497-queue-{qid}", "g": gid, "t": t},
            )
        for uid, gid in ((USER_A, GROUP_1), (USER_B, GROUP_2)):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "t": t},
            )
        for rid, valid in ((ROLE_1, 1), (ROLE_2, 2)):
            conn.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, :v, :t, 1, :t, 1)"
                ),
                {"id": rid, "n": f"dash497-role-{rid}", "v": valid, "t": t},
            )
        for uid, rid in ((USER_A, ROLE_1), (USER_C, ROLE_2)):
            conn.execute(
                text(
                    "INSERT INTO role_user (user_id, role_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:u, :r, :t, 1, :t, 1)"
                ),
                {"u": uid, "r": rid, "t": t},
            )
        rows = [
            # key, queue, owner, state, until_time, escalation_solution_time
            ("a_open", QUEUE_1, USER_A, "open", 0, 0),
            ("a_new", QUEUE_1, USER_A, "new", 0, 0),
            ("a_escalated", QUEUE_1, USER_A, "open", 0, epoch - 3600),
            ("a_pending", QUEUE_1, USER_A, "pending reminder", epoch + 7200, 0),
            ("unowned_new", QUEUE_1, 1, "new", 0, 0),
            ("b_open", QUEUE_2, USER_B, "open", 0, 0),
        ]
        for key, queue, owner, st, until, esc in rows:
            tid = TICKETS[key]
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, NULL, NULL,"
                    " 0, :until, :esc, 0, 0, :esc, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026101049{tid}", "title": f"dash497 {key}", "q": queue,
                 "u": owner, "s": state[st], "until": until, "esc": esc, "t": t},
            )  # fmt: skip
        conn.execute(
            text("INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at) VALUES (:i, :t)"),
            {"i": TICKETS["a_open"], "t": now},
        )
    engine.dispose()


def _admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="OTRS", auth_method="session"
    )


def _agent(uid: int) -> AuthenticatedUser:
    login, first, last = _USER_NAMES.get(uid, (f"dash497.u{uid}", "Una", "Known"))
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


class Env:
    def __init__(
        self,
        sync_url: str,
        factory: async_sessionmaker[AsyncSession],
        client: AsyncClient,
        anon: AsyncClient,
        admin: AsyncClient,
        current: list[AuthenticatedUser],
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client  #: acts as ``as_user``'s user (default USER_A)
        self.anon = anon  #: no authentication override
        self.admin = admin  #: acts as the Znuny admin (id 1)
        self._current = current
        self._restore_escalations: dict[int, int] = {}
        self.foreign_dashboard_id = 0
        self.now = now

    # --- sessions and store shortcuts ------------------------------------------

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.factory() as s:
            yield s

    async def run(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kw: Any) -> Any:
        """Run a store function in its own session and commit."""
        async with self.factory() as s:
            result = await fn(s, *args, **kw)
            await s.commit()
            return result

    def as_user(self, uid: int) -> AsyncClient:
        self._current[0] = _agent(uid)
        return self.client

    async def list(self, user_id: int) -> list[DashboardTabOut]:
        return await self.run(store.list_visible, user_id, admin=False)  # type: ignore[no-any-return]

    async def get(self, dashboard_id: int, user_id: int) -> DashboardOut:
        return await self.run(store.get_dashboard, dashboard_id, user_id, admin=False)  # type: ignore[no-any-return]

    async def create(self, user_id: int, body: DashboardCreateIn) -> DashboardOut:
        return await self.run(store.create_dashboard, user_id, body, admin=False)  # type: ignore[no-any-return]

    async def save(self, dashboard_id: int, user_id: int, body: DashboardSaveIn) -> DashboardOut:
        return await self.run(store.save_dashboard, dashboard_id, user_id, body, admin=False)  # type: ignore[no-any-return]

    async def patch(
        self,
        dashboard_id: int,
        widget_id: str,
        user_id: int,
        settings: dict[str, Any],
        *,
        version: int,
    ) -> WidgetOut:
        body = WidgetSettingsPatchIn(settings=settings, version=version)
        return await self.run(  # type: ignore[no-any-return]
            store.patch_widget_settings, dashboard_id, widget_id, user_id, body, admin=False
        )

    async def delete(self, dashboard_id: int, user_id: int) -> None:
        await self.run(store.delete_dashboard, dashboard_id, user_id, admin=False)

    async def set_order(self, user_id: int, ids: Sequence[int]) -> None:
        await self.run(store.set_order, user_id, ids)

    async def ensure_start(self, user_id: int) -> tuple[DashboardOut, bool]:
        return await self.run(start.ensure_start, user_id)  # type: ignore[no-any-return]

    # --- builders ----------------------------------------------------------------

    async def make_personal(
        self,
        user_id: int,
        *,
        name: str,
        widgets: Sequence[WidgetIn] = (),
        share_group_ids: Sequence[int] = (),
    ) -> DashboardOut:
        """Create through the store (limit applies), then add widgets and shares directly."""
        d = await self.create(user_id, DashboardCreateIn(name=name))
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            for i, w in enumerate(widgets):
                _insert_widget(conn, d.id, w, i)
            for gid in share_group_ids:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_dashboard_group (dashboard_id, group_id)"
                        " VALUES (:d, :g)"
                    ),
                    {"d": d.id, "g": gid},
                )
            if share_group_ids:
                conn.execute(
                    text("UPDATE tiqora_dashboard SET kind = 'shared' WHERE id = :d"), {"d": d.id}
                )
        engine.dispose()
        return await self.get(d.id, user_id)

    async def make_template(
        self,
        *,
        name: str,
        role_ids: Sequence[int],
        locked: bool,
        sort: int = 0,
        widgets: Sequence[WidgetIn] | None = None,
    ) -> DashboardOut:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_dashboard (owner_user_id, name, sort, kind, locked,"
                    " filters, version, created_at, changed_at)"
                    " VALUES (NULL, :n, :s, 'template', :l, '{}', 1, :t, :t)"
                ),
                {"n": name, "s": sort, "l": locked, "t": now},
            )
            did = int(
                conn.execute(
                    text(
                        "SELECT MAX(id) FROM tiqora_dashboard WHERE name = :n AND kind = 'template'"
                    ),
                    {"n": name},
                ).scalar_one()
            )
            for rid in role_ids:
                conn.execute(
                    text(
                        "INSERT INTO tiqora_dashboard_role (dashboard_id, role_id) VALUES (:d, :r)"
                    ),
                    {"d": did, "r": rid},
                )
            for i, w in enumerate(widgets or ()):
                _insert_widget(conn, did, w, i)
        engine.dispose()
        return await self.run(store.get_dashboard, did, 1, admin=True)  # type: ignore[no-any-return]

    async def make_copy_of_template(self, user_id: int, template_id: int) -> DashboardOut:
        tpl = await self.run(store.get_dashboard, template_id, 1, admin=True)
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_dashboard (owner_user_id, name, sort, kind, locked,"
                    " source_key, source_template_id, source_template_version, filters,"
                    " version, created_at, changed_at)"
                    " VALUES (:u, :n, 0, 'personal', :f, :k, :tid, :tv, '{}', 1, :t, :t)"
                ),
                {"u": user_id, "n": tpl.name, "f": False, "k": f"tpl:{template_id}",
                 "tid": template_id, "tv": tpl.version, "t": now},
            )  # fmt: skip
            did = int(
                conn.execute(
                    text("SELECT id FROM tiqora_dashboard WHERE owner_user_id = :u"
                         " AND source_key = :k"),
                    {"u": user_id, "k": f"tpl:{template_id}"},
                ).scalar_one()
            )  # fmt: skip
            for i, w in enumerate(tpl.widgets):
                copy = WidgetIn.model_validate(w.model_dump() | {"id": uuid.uuid4().hex})
                _insert_widget(conn, did, copy, i)
        engine.dispose()
        return await self.get(did, user_id)

    # --- inspection --------------------------------------------------------------

    def _sql(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            rows = conn.execute(text(sql), params).all()
        engine.dispose()
        return list(rows)

    async def widget_ids(self, dashboard_id: int) -> set[str]:
        rows = self._sql(
            "SELECT id FROM tiqora_dashboard_widget WHERE dashboard_id = :d", d=dashboard_id
        )
        return {str(r[0]) for r in rows}

    async def count_dashboards(self, user_id: int) -> list[int]:
        """Ids of the dashboards the user owns."""
        rows = self._sql(
            "SELECT id FROM tiqora_dashboard WHERE owner_user_id = :u ORDER BY id", u=user_id
        )
        return [int(r[0]) for r in rows]

    async def count_rows(self, table: str, **where: Any) -> int:
        assert table.replace("_", "").isalnum(), table
        cond = " AND ".join(f"{k} = :{k}" for k in where) or "1 = 1"
        return int(self._sql(f"SELECT COUNT(*) FROM {table} WHERE {cond}", **where)[0][0])  # noqa: S608

    async def sample_rows(self, user_id: int) -> list[tuple[str, str, int]]:
        rows = self._sql(
            "SELECT counter_key, CAST(sample_day AS CHAR), value"
            " FROM tiqora_dashboard_counter_sample WHERE user_id = :u"
            " ORDER BY counter_key, sample_day",
            u=user_id,
        )
        return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]

    async def sample_keys(self, user_id: int) -> set[str]:
        return {k for k, _, _ in await self.sample_rows(user_id)}

    def set_escalation(self, ticket_id: int, solution: int) -> None:
        """Set ``escalation_solution_time``; restored by the fixture on exit."""
        if ticket_id not in self._restore_escalations:
            old = self._sql(
                "SELECT escalation_solution_time FROM ticket WHERE id = :i", i=ticket_id
            )
            self._restore_escalations[ticket_id] = int(old[0][0])
        self._sql_write(
            "UPDATE ticket SET escalation_solution_time = :s, escalation_time = :s WHERE id = :i",
            s=solution,
            i=ticket_id,
        )

    def _sql_write(self, sql: str, **params: Any) -> None:
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            conn.execute(text(sql), params)
        engine.dispose()

    def body_of(
        self, dashboard: DashboardOut, widgets: Sequence[WidgetIn] | None = None
    ) -> DashboardSaveIn:
        """A save body with the dashboard's current version."""
        items = (
            list(widgets)
            if widgets is not None
            else [WidgetIn.model_validate(w.model_dump()) for w in dashboard.widgets]
        )
        return DashboardSaveIn(
            name=dashboard.name, icon=dashboard.icon, version=dashboard.version, widgets=items
        )


def _insert_widget(conn: Any, dashboard_id: int, w: WidgetIn, position: int) -> None:
    conn.execute(
        text(
            "INSERT INTO tiqora_dashboard_widget (id, dashboard_id, type_key, type_version,"
            " title_override, settings, layout, position)"
            " VALUES (:id, :d, :k, :v, :to, :s, :l, :p)"
        ),
        {
            "id": w.id,
            "d": dashboard_id,
            "k": w.type_key,
            "v": w.type_version,
            "to": w.title_override,
            "s": json.dumps(w.settings, ensure_ascii=False, sort_keys=True),
            "l": json.dumps(w.layout.model_dump(mode="json", exclude_none=True), sort_keys=True),
            "p": position,
        },
    )


@asynccontextmanager
async def open_env(sync_url: str) -> AsyncIterator[Env]:
    """Seed the world, yield an ``Env``, and remove every row again."""
    seed_world(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    current = [_agent(USER_A)]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    anon_app = create_app(Settings(environment="test"))
    anon_app.dependency_overrides[get_db] = _db
    admin_app = create_app(Settings(environment="test"))
    admin_app.dependency_overrides[get_db] = _db
    admin_app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_current_user] = lambda: current[0]

    def _client(a: Any) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=a), base_url="http://test")

    env: Env | None = None
    try:
        async with _client(app) as c, _client(anon_app) as a, _client(admin_app) as ad:
            env = Env(sync_url, factory, c, a, ad, current)
            d = await store_foreign(env)
            env.foreign_dashboard_id = d
            yield env
    finally:
        if env is not None:
            for tid, old in env._restore_escalations.items():
                env._sql_write(
                    "UPDATE ticket SET escalation_solution_time = :s WHERE id = :i", s=old, i=tid
                )
        await engine.dispose()
        cleanup_world(sync_url)


async def store_foreign(env: Env) -> int:
    """A dashboard of someone who is none of A, B, C: invisible to all of them."""
    d = await env.create(OTHER_OWNER, DashboardCreateIn(name="dash497 foreign"))
    return d.id
