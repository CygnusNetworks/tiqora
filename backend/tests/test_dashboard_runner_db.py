"""Runner against real pools (both dialects): the cap leaves room for the API, the database
aborts slow statements first, and a client-side cancel never returns its connection to the pool.

Providers here are test-only (``test.*`` keys, sleeping in SQL). Dashboards belong to USER_A of
the ``_dashboard_helpers`` world (band 49710-49749); every row is removed again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests._dashboard_helpers import USER_A, Env, _agent, open_env, to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.api.v1.dashboards import get_dashboard_registry
from tiqora.config import Settings
from tiqora.dashboard import runner
from tiqora.dashboard.layout import Rect
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, DashboardProvider, ProviderContext
from tiqora.dashboard.runner import run_batch
from tiqora.dashboard.schemas import WidgetIn, WidgetLayouts
from tiqora.extensions.registry import Registry

pytestmark = pytest.mark.db


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1


@dataclass
class Probe:
    """What the sleeping providers saw."""

    running: int = 0
    max_running: int = 0
    backend_ids: list[int] = field(default_factory=list)


async def backend_id(session: AsyncSession) -> int:
    dialect = session.get_bind().dialect.name
    sql = "SELECT pg_backend_pid()" if dialect == "postgresql" else "SELECT CONNECTION_ID()"
    return int((await session.execute(text(sql))).scalar_one())


def sleeper(probe: Probe, seconds: float, *, timeout_seconds: float) -> DashboardProvider:
    async def run(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
        probe.running += 1
        probe.max_running = max(probe.max_running, probe.running)
        try:
            probe.backend_ids.append(await backend_id(ctx.session))
            dialect = ctx.session.get_bind().dialect.name
            fn = "pg_sleep" if dialect == "postgresql" else "SLEEP"
            await ctx.session.execute(text(f"SELECT {fn}(:s)"), {"s": seconds})
            return {"slept": seconds}
        finally:
            probe.running -= 1

    return DashboardProvider(
        key="test.sleep",
        version=1,
        settings_model=_Any,
        run=run,
        timeout_seconds=timeout_seconds,
    )


class _Any(BaseModel):
    model_config = ConfigDict(extra="allow")


def registry_with(p: DashboardProvider) -> Registry:
    reg = Registry()
    reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


@asynccontextmanager
async def small_pool(env: Env, *, size: int, overflow: int) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        to_async_url(env.sync_url), pool_size=size, max_overflow=overflow, pool_timeout=3
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def count_invalidations(engine: AsyncEngine) -> list[int]:
    seen: list[int] = []
    event.listen(engine.sync_engine.pool, "invalidate", lambda *a: seen.append(1))
    return seen


async def test_a_slow_dashboard_leaves_connections_for_other_requests(env: Env) -> None:
    probe = Probe()
    widgets = [
        WidgetIn(
            id=f"sleep{i:04d}",
            type_key="test.sleep",
            layout=WidgetLayouts(lg=Rect(x=0, y=i * 2, w=3, h=2)),
        )
        for i in range(8)
    ]
    d = await env.make_personal(USER_A, name="dash497 langsam", widgets=widgets)
    # five connections in all: four for the providers, one for everybody else
    async with small_pool(env, size=2, overflow=3) as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _db() -> Any:
            async with factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: factory
        app.dependency_overrides[get_current_user] = lambda: _agent(USER_A)
        reg = registry_with(sleeper(probe, 1.5, timeout_seconds=5))
        app.dependency_overrides[get_dashboard_registry] = lambda: reg
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            batch = asyncio.create_task(c.get(f"/api/v1/dashboards/{d.id}/data"))
            for _ in range(60):  # until the providers hold four connections
                if probe.running == 4 or batch.done():
                    break
                await asyncio.sleep(0.05)
            assert probe.running == 4, batch.done() and batch.result().json()
            started = time.monotonic()
            other = await c.get("/api/v1/dashboards")
            assert other.status_code == 200 and time.monotonic() - started < 0.8
            r = await batch
        assert r.status_code == 200
        assert {w["status"] for w in r.json()["widgets"].values()} == {"ok"}
        assert len(r.json()["widgets"]) == 8 and probe.max_running == 4
        assert engine.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_the_database_aborts_a_slow_statement_first(env: Env) -> None:
    probe = Probe()
    async with small_pool(env, size=1, overflow=0) as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        invalidated = count_invalidations(engine)
        reg = registry_with(sleeper(probe, 4.0, timeout_seconds=1))
        started = time.monotonic()
        out = await run_batch(factory, reg, USER_A, [W("a", "test.sleep")])
        elapsed = time.monotonic() - started
        assert out["a"].status == "pending"
        assert elapsed < 1.9  # server-side abort at 1 s, before the client's 2 s
        assert invalidated == []  # a clean abort keeps the connection
        async with factory() as s:
            # the same connection came back, without the statement limit
            assert await backend_id(s) == probe.backend_ids[0]
            if s.get_bind().dialect.name == "postgresql":
                limit = (await s.execute(text("SHOW statement_timeout"))).scalar_one()
                assert limit == "0"
            else:
                limit = (await s.execute(text("SELECT @@max_statement_time"))).scalar_one()
                assert float(limit) == 0


async def test_a_client_side_cancel_invalidates_the_connection(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a database that does not abort: the runner's own deadline cancels mid-query."""

    @asynccontextmanager
    async def no_limit(session: Any, seconds: int) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(runner, "statement_timeout", no_limit)
    probe = Probe()
    async with small_pool(env, size=1, overflow=0) as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        invalidated = count_invalidations(engine)
        reg = registry_with(sleeper(probe, 4.0, timeout_seconds=0.2))
        started = time.monotonic()
        out = await run_batch(factory, reg, USER_A, [W("a", "test.sleep")])
        assert out["a"].status == "pending" and time.monotonic() - started < 2.5
        assert invalidated == [1]
        assert engine.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        async with factory() as s:
            # a fresh connection: the cancelled one never went back to the pool
            assert await backend_id(s) != probe.backend_ids[0]
            assert int((await s.execute(text("SELECT 1"))).scalar_one()) == 1
