"""Restart endpoints and api registration against a real database (both dialects).

Band 62120-62139: process hostnames and restart reasons start with ``plg62120``.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tiqora.api import app as app_module
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob, TiqoraProcess, TiqoraRestart
from tiqora.domain.auth import AuthenticatedUser
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins import restart
from tiqora.plugins.restart import utcnow

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/plugins/restart"
REASON = "plg62120 Erweiterung installiert"
HOST = "plg62120-host"
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT = AuthenticatedUser(
    id=7, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(delete(TiqoraProcess).where(TiqoraProcess.hostname.like("plg62120%")))
        await s.execute(delete(TiqoraRestart).where(TiqoraRestart.reason.like("plg62120%")))
        await s.execute(delete(TiqoraJob).where(TiqoraJob.dedupe_key.like("plg62120%")))
        await s.commit()


@pytest.fixture(name="factory", params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory_fixture(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(to_async_url(request.getfixturevalue(request.param)))
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clean(sm)
    try:
        yield sm
    finally:
        await _clean(sm)
        await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restart, "_REQUESTED", False)


def _app(factory: async_sessionmaker[AsyncSession], *, admin: bool = True, **settings: Any) -> Any:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test", **settings))
    app.state.start_restart_watch = False  # a running loop would interfere with the asserts
    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: ROOT
    else:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(name="client")
async def client_fixture(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with _client(_app(factory)) as c:
        yield c


async def _live_process(factory: async_sessionmaker[AsyncSession], role: str) -> None:
    async with factory() as s:
        s.add(
            TiqoraProcess(
                instance_id=f"{role}:{HOST}:1:t",
                role=role,
                hostname=HOST,
                pid=1,
                boot_generation=0,
                started_at=utcnow(),
                heartbeat_at=utcnow(),
                exiting_at=None,
            )
        )
        await s.commit()


async def test_restart_request_and_status(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await client.get(BASE)).status_code == 200
    await _live_process(factory, "worker")
    r = await client.post(BASE, json={"reason": REASON})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["generation"] > 0 and body["reason"] == REASON
    roles = [x["role"] for x in body["roles"]]
    assert "api" in roles and "worker" in roles
    assert roles.index("worker") < roles.index("api")  # api restarts last
    assert body["coordinated"] is True and body["finished_at"] is None
    got = (await client.get(BASE)).json()
    assert got["generation"] == body["generation"] and got["reason"] == REASON


async def test_restart_request_conflict(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    first = await client.post(BASE, json={"reason": REASON})
    assert first.status_code == 201
    again = await client.post(BASE, json={"reason": REASON})
    assert again.status_code == 409
    assert again.json()["detail"] == "Ein Neustart läuft bereits"
    # a finished request does not block the next one
    async with factory() as s:
        await s.execute(
            TiqoraRestart.__table__.update()  # type: ignore[attr-defined]
            .where(TiqoraRestart.generation == first.json()["generation"])
            .values(finished_at=utcnow())
        )
        await s.commit()
    assert (await client.post(BASE, json={"reason": REASON})).status_code == 201


async def test_stuck_request_can_be_superseded(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as s:
        s.add(
            TiqoraRestart(
                requested_at=utcnow() - timedelta(hours=3),
                requested_by=1,
                reason=REASON,
                roles="api",
            )
        )
        await s.commit()
    assert (await client.post(BASE, json={"reason": REASON})).status_code == 201


async def test_restart_refused_while_plugin_job_runs(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as s:
        s.add(
            TiqoraJob(
                kind="core.plugin_install",
                status="running",
                payload="{}",
                dedupe_key="plg62120:job",
                worker_id=f"{socket.gethostname()}:{os.getpid()}",
                created_at=utcnow(),
            )
        )
        await s.commit()
    r = await client.post(BASE, json={"reason": REASON})
    assert r.status_code == 409
    assert r.json()["detail"] == "Eine Erweiterung wird gerade installiert oder entfernt"
    async with factory() as s:
        assert (
            await s.execute(select(TiqoraRestart).where(TiqoraRestart.reason == REASON))
        ).first() is None


async def test_restart_requires_admin(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_admin(self: PermissionEngine, user_id: int) -> bool:
        return False

    monkeypatch.setattr(PermissionEngine, "is_admin", not_admin)
    async with _client(_app(factory, admin=False)) as c:
        assert (await c.post(BASE, json={"reason": REASON})).status_code == 403
        assert (await c.get(BASE)).status_code == 403
    async with factory() as s:
        assert (
            await s.execute(select(TiqoraRestart).where(TiqoraRestart.reason == REASON))
        ).first() is None


async def test_restart_cross_site_post_is_refused(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(environment="test")
    async with _client(_app(factory, csrf_origin_check_enabled=True)) as c:
        c.cookies.set(settings.session_cookie_name, "x")
        r = await c.post(BASE, json={"reason": REASON}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    async with factory() as s:
        assert (
            await s.execute(select(TiqoraRestart).where(TiqoraRestart.reason == REASON))
        ).first() is None


async def test_status_says_manual_when_disabled(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        await restart.request_restart(s, by=1, reason=REASON, now=utcnow())
        await s.commit()
    async with _client(_app(factory, plugins_coordinated_restart=False)) as c:
        got = (await c.get(BASE)).json()
        assert got["reason"] == REASON and got["coordinated"] is False
        r = await c.post(BASE, json={"reason": REASON})
        assert r.status_code == 409 and "selbst neu" in r.json()["detail"]


async def test_route_is_not_shadowed_by_plugin_id_routes(client: AsyncClient) -> None:
    # POST /plugins/restart must reach the restart route, not a {plugin_id} route
    r = await client.post(BASE, json={"reason": REASON})
    assert r.status_code == 201 and "generation" in r.json()


async def test_request_restart_ensure_roles_counts_api_without_heartbeat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        generation = await restart.request_restart(
            s, by=1, reason=REASON, now=utcnow(), ensure_roles=("api",)
        )
        await s.commit()
        row = (
            await s.execute(select(TiqoraRestart).where(TiqoraRestart.generation == generation))
        ).scalar_one()
    assert "api" in row.roles.split(",")


async def test_lifespan_registration_registers_and_unregisters_api(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(session_factory=factory, settings=Settings(environment="test"))
    )
    watch = await app_module._start_process_registration(app)  # type: ignore[arg-type]
    assert watch is not None
    try:
        async with factory() as s:
            row = (
                await s.execute(
                    select(TiqoraProcess).where(TiqoraProcess.instance_id == watch.instance_id)
                )
            ).scalar_one()
        assert row.role == "api" and row.exiting_at is None
        assert not watch.task.done()
    finally:
        await restart.stop_process_watch(watch)
    assert watch.task.cancelled()
    async with factory() as s:
        assert (
            await s.execute(
                select(TiqoraProcess).where(TiqoraProcess.instance_id == watch.instance_id)
            )
        ).first() is None
