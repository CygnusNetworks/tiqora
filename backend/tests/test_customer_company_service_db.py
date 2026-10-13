"""``ensure_customer_company`` and the admin route that uses it (both dialects).

Seed band 55390-55429: every company this module writes has an id starting with ``PP55-``;
cache-signal rows are removed with the row marks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.customer_company_service import ensure_customer_company

pytestmark = pytest.mark.db

ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
TABLES = ("tiqora_cache_invalidation",)


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_company WHERE customer_id LIKE 'PP55-%'"))
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    sync_url: str = request.getfixturevalue(request.param)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    engine.dispose()
    _clean(sync_url)
    marks = snapshot_marks(sync_url, TABLES)
    async_engine = create_async_engine(to_async_url(sync_url))
    yield sync_url, async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    await async_engine.dispose()
    delete_above_marks(sync_url, marks)
    _clean(sync_url)


def _rows(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            return list(conn.execute(text(sql), params).all())
    finally:
        engine.dispose()


async def test_insert_then_noop(env: tuple[str, async_sessionmaker[AsyncSession]]) -> None:
    sync_url, factory = env
    marks = snapshot_marks(sync_url, TABLES)
    async with factory() as session:
        inserted = await ensure_customer_company(
            session, customer_id="PP55-FIRMA", name="Beispiel GmbH", actor_id=1, city="Musterstadt"
        )
        again = await ensure_customer_company(
            session, customer_id="PP55-FIRMA", name="Anders", actor_id=1
        )
        await session.commit()
    assert inserted is True
    assert again is False
    (row,) = _rows(
        sync_url,
        "SELECT name, valid_id, create_by, change_by, city FROM customer_company"
        " WHERE customer_id = 'PP55-FIRMA'",
    )
    assert tuple(row) == ("Beispiel GmbH", 1, 1, 1, "Musterstadt")
    signals = _rows(
        sync_url,
        "SELECT cache_type FROM tiqora_cache_invalidation WHERE id > :m",
        m=marks["tiqora_cache_invalidation"],
    )
    # one signal set for the insert; the no-op wrote none
    assert [r[0] for r in signals].count("CustomerCompany") == 1


async def test_admin_route_unchanged(env: tuple[str, async_sessionmaker[AsyncSession]]) -> None:
    sync_url, factory = env

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ROOT
    body = {"customer_id": "PP55-ROUTE", "name": "Route GmbH", "city": "Musterstadt"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/admin/customer-companies", json=body)
        duplicate = await client.post("/api/v1/admin/customer-companies", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["customer_id"] == "PP55-ROUTE"
    assert created.json()["name"] == "Route GmbH"
    assert created.json()["valid_id"] == 1
    assert duplicate.status_code == 409
    (row,) = _rows(
        sync_url,
        "SELECT name, create_by, change_by FROM customer_company WHERE customer_id = 'PP55-ROUTE'",
    )
    assert tuple(row) == ("Route GmbH", 1, 1)
