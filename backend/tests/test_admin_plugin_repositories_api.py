"""Plugin repository admin endpoints against a real database (both dialects).

Band 62240-62259: repository names ``plg62240 …``, urls under ``*.plg62240.example.org``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import make_keypair
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin import plugin_repositories
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPluginRepository
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now
from tiqora.plugins.repositories import url_key

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/plugin-repositories"
_, PUB, _ = make_keypair()
_, PUB2, _ = make_keypair()
U1 = "https://one.plg62240.example.org/index.json"
U2 = "https://two.plg62240.example.org/index.json"
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
KEY_USER = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="api_key"
)
AGENT = AuthenticatedUser(
    id=7, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await s.execute(
            delete(TiqoraPluginRepository).where(TiqoraPluginRepository.url.like("%plg62240%"))
        )
        await s.commit()


@pytest.fixture(name="url_fixture", params=["mariadb_znuny_url", "postgres_znuny_url"])
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


def _client(
    factory: async_sessionmaker[AsyncSession],
    *,
    user: AuthenticatedUser | None = ROOT,
    **settings: Any,
) -> AsyncClient:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test", **settings))
    app.state.start_restart_watch = False
    app.dependency_overrides[get_db] = _db
    if user is not None:
        app.dependency_overrides[get_admin_user] = lambda: user
    else:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(url: str = U1, **kw: Any) -> dict[str, Any]:
    return {"name": "plg62240 one", "url": url, **kw}


async def _row(factory: async_sessionmaker[AsyncSession], url: str) -> TiqoraPluginRepository:
    async with factory() as s:
        return (
            await s.execute(select(TiqoraPluginRepository).where(TiqoraPluginRepository.url == url))
        ).scalar_one()


async def test_create_repository_validates(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(url_fixture) as c:
        r = await c.post(BASE, json=_body("file:///srv/repo/index.json"))
        assert r.status_code == 422 and "nur https" in r.json()["detail"]
        r = await c.post(BASE, json=_body("https://u:p@one.plg62240.example.org/index.json"))
        assert r.status_code == 422 and "Zugangsdaten" in r.json()["detail"]
        r = await c.post(BASE, json=_body(public_key="ed25519:AAAA"))
        assert r.status_code == 422 and "Schlüssel" in r.json()["detail"]
        r = await c.post(BASE, json=_body(public_key=PUB))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["origin"] == "admin" and body["enabled"] is True
        assert body["public_key"] == PUB and body["verified"] is False
        assert body["plugin_count"] == 0 and body["repository_name"] is None
        r = await c.post(BASE, json=_body())
        assert r.status_code == 409
    row = await _row(url_fixture, U1)
    assert row.url_key == url_key(U1) and row.created_by == 1 and row.index_verified is False


async def test_create_race_on_the_same_address_is_409(
    url_fixture: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 17 M1: two requests pass the pre-check at once; the unique index decides -> 409."""

    async def never_taken(*_a: Any, **_kw: Any) -> bool:
        return False

    monkeypatch.setattr(plugin_repositories, "_url_taken", never_taken)
    async with _client(url_fixture) as c:
        assert (await c.post(BASE, json=_body())).status_code == 201
        r = await c.post(BASE, json=_body(name="plg62240 two"))
        assert r.status_code == 409 and r.json()["detail"] == plugin_repositories.DUPLICATE
        other = await c.post(BASE, json=_body(U2, name="plg62240 three"))
        assert other.status_code == 201
        r = await c.put(f"{BASE}/{other.json()['id']}", json=_body(name="plg62240 three"))
        assert r.status_code == 409


async def test_write_endpoints_refuse_api_keys_and_non_admins(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(url_fixture) as c:
        created = (await c.post(BASE, json=_body())).json()["id"]
    async with _client(url_fixture, user=KEY_USER) as c:
        for r in (
            await c.post(BASE, json=_body(U2)),
            await c.put(f"{BASE}/{created}", json=_body()),
            await c.delete(f"{BASE}/{created}"),
        ):
            assert r.status_code == 403 and r.json()["detail"] == "Nur in der Weboberfläche möglich"
        assert (await c.get(BASE)).status_code == 200  # read stays open to admin:ro
    async with _client(url_fixture, user=None) as c:
        assert (await c.get(BASE)).status_code == 403
    async with url_fixture() as s:
        urls = (
            (
                await s.execute(
                    select(TiqoraPluginRepository.url).where(
                        TiqoraPluginRepository.url.like("%plg62240%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(urls) == [U1]


async def test_update_and_delete_admin_repository(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    async with _client(url_fixture) as c:
        rid = (await c.post(BASE, json=_body(public_key=PUB))).json()["id"]
        other = (await c.post(BASE, json=_body(U2, name="plg62240 two"))).json()["id"]
        # mark the index as verified, then change only the key -> verification is dropped
        async with url_fixture() as s:
            row = await s.get(TiqoraPluginRepository, rid)
            assert row is not None
            row.index_verified, row.index_json = True, '{"x": 1}'
            await s.commit()
        r = await c.put(f"{BASE}/{rid}", json=_body(public_key=PUB2, name="plg62240 renamed"))
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "plg62240 renamed" and r.json()["verified"] is False
        assert (await _row(url_fixture, U1)).index_json == '{"x": 1}'  # same address keeps it
        # same key and same url: enabled flag only
        r = await c.put(f"{BASE}/{rid}", json=_body(public_key=PUB2, enabled=False))
        assert r.json()["enabled"] is False
        # moving to the other repository's address is a conflict; to a new one clears the index
        assert (await c.put(f"{BASE}/{rid}", json=_body(U2))).status_code == 409
        new = "https://three.plg62240.example.org/index.json"
        r = await c.put(f"{BASE}/{rid}", json=_body(new))
        assert r.status_code == 200 and r.json()["url"] == new
        moved = await _row(url_fixture, new)
        assert moved.url_key == url_key(new) and moved.index_json is None
        assert (
            await c.put(f"{BASE}/{rid}", json=_body("http://x.example.org/i"))
        ).status_code == 422
        assert (await c.put(f"{BASE}/999999", json=_body())).status_code == 404
        assert (await c.delete(f"{BASE}/{rid}")).status_code == 204
        assert (await c.delete(f"{BASE}/{rid}")).status_code == 404
        listed = (await c.get(BASE)).json()
        assert [x["id"] for x in listed if "plg62240" in x["url"]] == [other]


async def test_env_repository_read_only(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    env = json.dumps([{"name": "plg62240 env", "url": U1}])
    async with _client(url_fixture, plugin_repositories=env) as c:
        listed = (await c.get(BASE)).json()
        mine = [x for x in listed if x["url"] == U1]
        assert len(mine) == 1 and mine[0]["origin"] == "env" and mine[0]["enabled"] is True
        rid = mine[0]["id"]
        for r in (
            await c.put(f"{BASE}/{rid}", json=_body()),
            await c.delete(f"{BASE}/{rid}"),
        ):
            assert r.status_code == 409 and "Server-Konfiguration" in r.json()["detail"]
    assert (await _row(url_fixture, U1)).origin == "env"
    # the env entry is gone on the next list: disabled, not deleted
    async with _client(url_fixture, plugin_repositories="") as c:
        mine = [x for x in (await c.get(BASE)).json() if x["url"] == U1]
        assert mine[0]["enabled"] is False and mine[0]["origin"] == "env"


async def test_list_shows_verification_and_counts(
    url_fixture: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now().replace(microsecond=0)
    index = {
        "index_version": 1,
        "repository": {
            "id": "org.example.repo",
            "name": "Beispiel-Quelle",
            "generated_at": "2026-10-12T08:00:00Z",
        },
        "plugins": [{"id": "org.example.a"}, {"id": "org.example.b"}],
    }
    async with url_fixture() as s:
        s.add(
            TiqoraPluginRepository(
                name="plg62240 idx",
                url=U1,
                url_key=url_key(U1),
                enabled=True,
                origin="admin",
                index_json=json.dumps(index),
                index_verified=True,
                last_checked_at=now,
                last_ok_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        s.add(
            TiqoraPluginRepository(
                name="plg62240 broken",
                url=U2,
                url_key=url_key(U2),
                enabled=True,
                origin="admin",
                index_json="not json",
                last_error="Server nicht erreichbar",
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    async with _client(url_fixture) as c:
        listed = {x["url"]: x for x in (await c.get(BASE)).json()}
    good, bad = listed[U1], listed[U2]
    assert good["verified"] is True and good["plugin_count"] == 2
    assert good["repository_name"] == "Beispiel-Quelle"
    assert good["last_ok_at"] is not None and good["last_error"] is None
    assert bad["verified"] is False and bad["plugin_count"] == 0
    assert bad["repository_name"] is None and bad["last_error"] == "Server nicht erreichbar"
