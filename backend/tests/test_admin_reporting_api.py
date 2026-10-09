"""HTTP tests for ``/api/v1/admin/reporting`` (MariaDB).

No ticket rows are seeded; the module only creates the state row and backfill jobs, which
it deletes again. Non-admin user id 47650 lies in the reporting band 47620-47659.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.auth import AuthenticatedUser
from tiqora.reporting.state import load_state

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/reporting"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT = AuthenticatedUser(
    id=47650, login="rep47.agent", first_name="Rita", last_name="Report", auth_method="session"
)
_CLEANUP = (
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind = 'core.report_backfill')",
    "DELETE FROM tiqora_job WHERE kind = 'core.report_backfill'",
)


def _clean(url: str) -> None:
    engine = create_engine(url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
    engine.dispose()


@pytest.fixture
def clean_url(mariadb_znuny_url: str) -> Iterator[str]:
    _clean(mariadb_znuny_url)
    yield mariadb_znuny_url
    _clean(mariadb_znuny_url)


def _app(url: str) -> tuple[Any, Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url.replace("mysql+pymysql://", "mysql+aiomysql://", 1))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    return app, engine, factory


@pytest.fixture
async def admin(
    clean_url: str,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    app, engine, factory = _app(clean_url)
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory
    await engine.dispose()


@pytest.fixture
async def agent(clean_url: str) -> AsyncIterator[AsyncClient]:
    """A plain agent: the real admin check runs against the database."""
    app, engine, _factory = _app(clean_url)
    app.dependency_overrides[get_current_user] = lambda: AGENT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_status_for_admin(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = admin
    resp = await client.get(f"{BASE}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False and body["backfill_status"] == "pending"
    assert body["backfill_job_id"] is None and body["backfill_progress"] is None
    assert body["history_lag"] >= 0 and body["last_history_id"] == 0
    assert body["gap_count"] == 0 and body["dirty_count"] == 0
    assert body["fact_rows"] == 0 and body["event_rows"] == 0
    assert body["last_snapshot_date"] is None and body["snapshot_time_zone"] is None


async def test_non_admin_is_forbidden(agent: AsyncClient) -> None:
    assert (await agent.get(f"{BASE}/status")).status_code == 403
    resp = await agent.post(f"{BASE}/backfill", json={})
    assert resp.status_code == 403


async def test_backfill_is_queued_and_deduplicated(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    first = await client.post(f"{BASE}/backfill", json={"since": "2024-01-01", "snapshots": False})
    assert first.status_code == 202
    body = first.json()
    assert set(body) == {"job_id", "status"} and body["status"] == "queued"
    second = await client.post(f"{BASE}/backfill", json={})
    assert second.status_code == 202 and second.json() == body  # joined the pending job

    async with factory() as s:
        job = await s.get(TiqoraJob, body["job_id"])
        assert job is not None
        assert (job.kind, job.dedupe_key, job.created_by) == (
            "core.report_backfill",
            "report_backfill",
            1,
        )
        assert '"since":"2024-01-01"' in job.payload.replace(" ", "")
        assert '"snapshots":false' in job.payload.replace(" ", "")
        state = await load_state(s)
        assert state.backfill_job_id == body["job_id"]
        assert state.backfill_status == "pending"
    status_body = (await client.get(f"{BASE}/status")).json()
    assert status_body["backfill_job_id"] == body["job_id"]


async def test_restart_resets_state_only_without_an_active_job(
    admin: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = admin
    async with factory() as s:
        state = await load_state(s, for_update=True)
        state.backfill_status = "done"
        await s.commit()
    first = await client.post(f"{BASE}/backfill", json={"restart": True})
    assert first.status_code == 202
    async with factory() as s:
        assert (await load_state(s)).backfill_status == "pending"  # no job was active

    # a job is active now: the running state belongs to it and stays untouched
    async with factory() as s:
        state = await load_state(s, for_update=True)
        state.backfill_status = "running"
        await s.execute(
            update(TiqoraJob).where(TiqoraJob.id == first.json()["job_id"]).values(status="running")
        )
        await s.commit()
    again = await client.post(f"{BASE}/backfill", json={"restart": True})
    assert again.status_code == 202
    assert again.json() == {"job_id": first.json()["job_id"], "status": "running"}
    async with factory() as s:
        assert (await load_state(s)).backfill_status == "running"
