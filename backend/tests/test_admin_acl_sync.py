"""Admin ACL writes leave ``acl_sync`` rows like Znuny's ACLAdd/ACLUpdate/ACLDelete.

HTTP through the admin router on MariaDB and PostgreSQL. Seed band 882500-882999: ACL names
start with ``sync882500-`` so cleanup can find rows created through the API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/acl"
PREFIX = "sync882500-"


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _clean(url: str) -> None:
    engine = create_engine(url)
    TiqoraBase.metadata.create_all(
        engine,
        tables=[TiqoraCacheInvalidation.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tiqora_cache_invalidation"))
        conn.execute(text("DELETE FROM acl_sync"))
        conn.execute(text("DELETE FROM acl WHERE name LIKE :p"), {"p": PREFIX + "%"})
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def clean_url(request: pytest.FixtureRequest) -> Iterator[str]:
    url: str = request.getfixturevalue(request.param)
    _clean(url)
    yield url
    _clean(url)


@pytest.fixture
async def factory(clean_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_async_url(clean_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    root = AuthenticatedUser(
        id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_admin_user] = lambda: root
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _sync_rows(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
    async with factory() as session:
        rows = await session.execute(
            text("SELECT acl_id, sync_state FROM acl_sync ORDER BY sync_state")
        )
        return [(r[0], r[1]) for r in rows]


def _body(**over: Any) -> dict[str, Any]:
    return {
        "name": PREFIX + "a",
        "valid_id": 1,
        "config_match": "Properties: {}\n",
        "config_change": "Possible: {}\n",
        **over,
    }


@pytest.mark.asyncio
async def test_create_update_delete_write_acl_sync_like_znuny(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await client.post(BASE, json=_body())
    assert created.status_code == 201
    acl_id = created.json()["id"]
    assert await _sync_rows(factory) == [(str(acl_id), "not_sync")]

    updated = await client.patch(f"{BASE}/{acl_id}", json={"comments": "changed"})
    assert updated.status_code == 200
    # Znuny inserts, never upserts: a second row.
    assert await _sync_rows(factory) == [(str(acl_id), "not_sync")] * 2

    deleted = await client.delete(f"{BASE}/{acl_id}")
    assert deleted.status_code == 204
    rows = await _sync_rows(factory)
    assert rows == [
        (str(acl_id), "deleted"),
        (str(acl_id), "not_sync"),
        (str(acl_id), "not_sync"),
    ]
    async with factory() as session:
        times = (await session.execute(text("SELECT create_time, change_time FROM acl_sync"))).all()
    assert len(times) == 3 and all(c is not None and m is not None for c, m in times)


@pytest.mark.asyncio
async def test_noop_update_writes_no_acl_sync_row(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await client.post(BASE, json=_body(name=PREFIX + "b", comments="same"))
    acl_id = created.json()["id"]
    before = created.json()["change_time"]
    # Same values (and None-vs-'' equivalence for comments/description).
    resp = await client.patch(
        f"{BASE}/{acl_id}",
        json={"name": PREFIX + "b", "comments": "same", "description": "", "stop_after_match": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["change_time"] == before
    assert await _sync_rows(factory) == [(str(acl_id), "not_sync")]  # only the create row

    changed = await client.patch(f"{BASE}/{acl_id}", json={"valid_id": 2})
    assert changed.status_code == 200 and changed.json()["valid_id"] == 2
    assert await _sync_rows(factory) == [(str(acl_id), "not_sync")] * 2
