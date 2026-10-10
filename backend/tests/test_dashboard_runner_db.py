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


def sleeper(
    probe: Probe, seconds: float, *, timeout_seconds: float, key: str = "test.sleep"
) -> DashboardProvider:
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
        key=key,
        version=1,
        settings_model=_Any,
        run=run,
        timeout_seconds=timeout_seconds,
    )


class _Any(BaseModel):
    model_config = ConfigDict(extra="allow")


def registry_with(*providers: DashboardProvider) -> Registry:
    reg = Registry()
    for p in providers:
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


async def test_concurrent_loads_share_the_engine_cap_and_leave_the_pool_to_the_api(
    env: Env,
) -> None:
    """Pool of 4 -> at most 2 provider connections for batches, single fetches and previews."""
    probe = Probe()
    widgets = [
        WidgetIn(
            id=f"sleep{i:04d}",
            type_key="test.sleep",
            layout=WidgetLayouts(lg=Rect(x=0, y=i * 2, w=3, h=2)),
        )
        for i in range(3)
    ]
    d = await env.make_personal(USER_A, name="dash497 langsam", widgets=widgets)
    async with small_pool(env, size=4, overflow=0) as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _db() -> Any:
            async with factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: factory
        app.dependency_overrides[get_current_user] = lambda: _agent(USER_A)
        reg = registry_with(sleeper(probe, 0.5, timeout_seconds=5))
        app.dependency_overrides[get_dashboard_registry] = lambda: reg
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            base = f"/api/v1/dashboards/{d.id}"
            calls = [
                *(c.get(f"{base}/data") for _ in range(2)),
                c.get(f"{base}/widgets/sleep0001/data"),
                c.post("/api/v1/dashboard-widget-types/test.sleep/data", json={}),
            ]
            load = asyncio.gather(*calls)
            for _ in range(60):  # until the providers hold their connections
                if probe.running == 2 or load.done():
                    break
                await asyncio.sleep(0.05)
            assert probe.running == 2
            started = time.monotonic()
            other = await c.get("/api/v1/dashboards")
            assert other.status_code == 200 and time.monotonic() - started < 1.5
            batch1, batch2, single, preview = await load
        statuses = [w["status"] for b in (batch1, batch2) for w in b.json()["widgets"].values()]
        assert statuses == ["ok"] * 6
        assert single.json()["status"] == preview.json()["status"] == "ok"
        assert probe.max_running == 2  # 8 provider runs, never more than half the pool
        assert engine.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_a_widget_starved_past_its_budget_is_pending_without_sql(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pool of 2 -> cap 1. The statement timeout is what is left of the budget at its start."""
    limits: list[int] = []
    real_timeout = runner.statement_timeout

    @asynccontextmanager
    async def recording(session: Any, seconds: int) -> AsyncIterator[None]:
        limits.append(seconds)
        async with real_timeout(session, seconds):
            yield

    monkeypatch.setattr(runner, "statement_timeout", recording)
    hog, starved, waiter = Probe(), Probe(), Probe()

    reg = registry_with(
        sleeper(hog, 1.5, timeout_seconds=5, key="test.hog"),
        sleeper(starved, 0.0, timeout_seconds=2, key="test.starved"),
        sleeper(waiter, 0.0, timeout_seconds=5, key="test.waiter"),
    )
    async with small_pool(env, size=2, overflow=0) as engine:
        checkouts: list[int] = []
        event.listen(engine.sync_engine.pool, "checkout", lambda *a: checkouts.append(1))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        first = asyncio.create_task(run_batch(factory, reg, USER_A, [W("h", "test.hog")]))
        for _ in range(100):  # until the hog holds the only slot
            if hog.running:
                break
            await asyncio.sleep(0.02)
        second = run_batch(factory, reg, USER_A, [W("s", "test.starved")])
        third = run_batch(factory, reg, USER_A, [W("w", "test.waiter")])
        out_h, out_s, out_w = await asyncio.gather(first, second, third)
    assert (out_h["h"].status, out_s["s"].status, out_w["w"].status) == ("ok", "pending", "ok")
    assert starved.backend_ids == []  # the starved provider never ran a statement
    assert len(checkouts) == 2  # ... and never took a connection (hog and waiter did)
    hog_limit, waiter_limit = limits  # the starved one never reached its statement
    assert hog_limit == 4  # 5 s budget, a moment spent on the checkout
    assert waiter_limit in (2, 3)  # waited ~1.5 s for the slot: what was left


async def test_the_database_aborts_a_slow_statement_first(env: Env) -> None:
    probe = Probe()
    async with small_pool(env, size=1, overflow=0) as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        invalidated = count_invalidations(engine)
        reg = registry_with(sleeper(probe, 4.0, timeout_seconds=2))
        started = time.monotonic()
        out = await run_batch(factory, reg, USER_A, [W("a", "test.sleep")])
        elapsed = time.monotonic() - started
        assert out["a"].status == "pending"
        assert elapsed < 2.5  # server-side abort at 1 s (what was left: 1.9x s), client at 3 s
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
        reg = registry_with(sleeper(probe, 4.0, timeout_seconds=1.2))
        started = time.monotonic()
        out = await run_batch(factory, reg, USER_A, [W("a", "test.sleep")])
        assert out["a"].status == "pending" and time.monotonic() - started < 3.5
        assert invalidated == [1]
        assert engine.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        async with factory() as s:
            # a fresh connection: the cancelled one never went back to the pool
            assert await backend_id(s) != probe.backend_ids[0]
            assert int((await s.execute(text("SELECT 1"))).scalar_one()) == 1
