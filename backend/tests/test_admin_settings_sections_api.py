"""HTTP tests for ``/api/v1/admin/settings-sections`` (MariaDB)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import KEY_AVATARS_FAVICON_ENABLED, KEY_AVATARS_GRAVATAR_ENABLED

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/settings-sections"


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            delete(TiqoraSettings).where(
                TiqoraSettings.key.like("jobs.%")
                | TiqoraSettings.key.in_(
                    (KEY_AVATARS_GRAVATAR_ENABLED, KEY_AVATARS_FAVICON_ENABLED)
                )
            )
        )
    engine.dispose()


@pytest.fixture
def clean_url(mariadb_znuny_url: str) -> Iterator[str]:
    _clean(mariadb_znuny_url)
    yield mariadb_znuny_url
    _clean(mariadb_znuny_url)


@pytest.fixture
async def client(clean_url: str) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(clean_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_has_jobs_and_avatars(client: AsyncClient) -> None:
    resp = await client.get(BASE)
    assert resp.status_code == 200
    by_key = {s["key"]: s for s in resp.json()}
    assert {"jobs", "avatars"} <= set(by_key)
    assert by_key["jobs"]["title_key"] == "admin.settings.jobs.title"
    assert by_key["jobs"]["owner"] == "core"


@pytest.mark.asyncio
async def test_get_jobs_schema_and_defaults(client: AsyncClient) -> None:
    resp = await client.get(f"{BASE}/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["json_schema"]["properties"]["max_concurrency"]["maximum"] == 8
    assert body["values"] == {"max_concurrency": 2, "retention_days": 14}
    assert body["secrets_set"] == []
    assert body["i18n_prefix"] == "admin.settings.jobs"


@pytest.mark.asyncio
async def test_put_invalid_is_422_with_field_errors(client: AsyncClient) -> None:
    resp = await client.put(f"{BASE}/jobs", json={"values": {"max_concurrency": 99}})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["message"] == "Bitte die markierten Felder prüfen."
    assert "max_concurrency" in detail["errors"]
    again = await client.get(f"{BASE}/jobs")
    assert again.json()["values"]["max_concurrency"] == 2


@pytest.mark.asyncio
async def test_put_valid_persists(client: AsyncClient) -> None:
    resp = await client.put(f"{BASE}/jobs", json={"values": {"max_concurrency": 3}})
    assert resp.status_code == 200
    assert resp.json()["values"]["max_concurrency"] == 3
    again = await client.get(f"{BASE}/jobs")
    assert again.json()["values"] == {"max_concurrency": 3, "retention_days": 14}


@pytest.mark.asyncio
async def test_unknown_section_is_404(client: AsyncClient) -> None:
    assert (await client.get(f"{BASE}/nope")).status_code == 404
    assert (await client.put(f"{BASE}/nope", json={"values": {}})).status_code == 404


@pytest.mark.asyncio
async def test_avatars_endpoint_keeps_shape_and_persists(client: AsyncClient) -> None:
    first = await client.get("/api/v1/admin/avatars")
    assert first.status_code == 200
    assert first.json() == {"gravatar_enabled": True, "favicon_enabled": True}
    put = await client.put(
        "/api/v1/admin/avatars", json={"gravatar_enabled": False, "favicon_enabled": True}
    )
    assert put.status_code == 200
    assert put.json() == {"gravatar_enabled": False, "favicon_enabled": True}
    via_section = await client.get(f"{BASE}/avatars")
    assert via_section.json()["values"] == {"gravatar_enabled": False, "favicon_enabled": True}
