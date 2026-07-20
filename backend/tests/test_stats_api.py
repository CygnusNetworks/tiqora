"""DB integration tests for ``/api/v1/stats/*`` endpoints.

Reuses the seed helper from ``test_stats_service.py`` (allowed queue + one
open, one closed ticket; a denied queue with one open ticket that must
never leak through the REST layer either).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_stats_service import _mysql_async, _seed


async def _client_for(mariadb_znuny_url: str, ids: dict[str, Any]) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=ids["user_id"],
        login=ids["login"],
        first_name="Read",
        last_name="Er",
        auth_method="session",
    )

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), engine


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_volume_json(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/stats/volume", params={"queue_id": ids["queue_id"]})
    await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["granularity"] == "day"
    assert sum(p["created"] for p in body["points"]) == 2
    assert sum(p["closed"] for p in body["points"]) == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_open_snapshot_permission_filtered(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/stats/open-snapshot", params={"dimension": "queue"})
    await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "queue"
    queue_ids = [item["id"] for item in body["items"]]
    assert ids["queue_id"] in queue_ids
    assert ids["denied_queue_id"] not in queue_ids


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_sla_json(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/stats/sla")
    await engine.dispose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["escalated"] == 1


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_agent_workload_csv(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/stats/agent-workload.csv")
    await engine.dispose()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw[3:].decode("utf-8").splitlines()
    assert lines[0].split(";") == ["Login", "Name", "OwnedOpen", "ClosedInPeriod"]
    assert any(ids["login"] in line for line in lines[1:])


@pytest.mark.db
@pytest.mark.asyncio
async def test_stats_backlog_csv(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    client, engine = await _client_for(mariadb_znuny_url, ids)
    async with client:
        resp = await client.get("/api/v1/stats/backlog.csv")
    await engine.dispose()

    assert resp.status_code == 200
    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw[3:].decode("utf-8").splitlines()
    assert lines[0].split(";") == ["Bucket", "OpenCount"]


@pytest.mark.asyncio
async def test_stats_volume_requires_auth() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.config import Settings

    app = create_app(Settings(environment="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/stats/volume")
    assert resp.status_code == 401
