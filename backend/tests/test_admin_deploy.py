"""``POST /api/v1/admin/deploy``: deploy processes / ACLs from Tiqora.

HTTP through the admin router on MariaDB and PostgreSQL. Seed band 881900-881999.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision, TiqoraSettings
from tiqora.deployed import clear_revision_cache, latest_revision_info
from tiqora.deployed import watcher as watcher_module
from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION
from tiqora.deployed.sync_tables import pending_sync_count
from tiqora.deployed.watcher import check_deploy_state, revision_write_lock
from tiqora.domain import ticket_acl
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ownership import KEY_OWNERSHIP_ENABLED, VALUE_ENABLED
from tiqora.domain.settings_store import set_setting
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

URL = "/api/v1/admin/deploy"
GATE_DETAIL = (
    "Bereitstellen aus Tiqora braucht das Znuny-Add-on TiqoraSync ab Version 1.2.0 "
    "(zuletzt gesehen: nie) oder die Einstellung „Prozesse und ACLs in Tiqora "
    "bereitstellen (Znuny läuft nicht mehr)“."
)
BUSY_DETAIL = "Eine andere Bereitstellung läuft gerade. Bitte gleich noch einmal versuchen."
T0 = datetime(2026, 3, 4, 5, 6, 7)
ACL_ID = 881901
PROCESS_ID = 881901
SETTING_KEY = "process.deploy_from_tiqora"
_URLS: set[str] = set()

_CLEANUP = (
    "DELETE FROM tiqora_process_revision",
    "DELETE FROM tiqora_acl_revision",
    "DELETE FROM pm_entity_sync",
    "DELETE FROM acl_sync",
    "DELETE FROM pm_process WHERE id BETWEEN 881900 AND 881999",
    "DELETE FROM acl WHERE id BETWEEN 881900 AND 881999",
)

Make = Callable[..., AbstractAsyncContextManager[AsyncClient]]


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _wipe(conn: Any) -> None:
    for stmt in _CLEANUP:
        conn.execute(text(stmt))
    conn.execute(
        delete(TiqoraSettings).where(TiqoraSettings.key.in_([SETTING_KEY, KEY_OWNERSHIP_ENABLED]))
    )


def _reset(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        engine,
        tables=[  # type: ignore[list-item]
            TiqoraProcessRevision.__table__,
            TiqoraAclRevision.__table__,
            TiqoraSettings.__table__,
        ],
        checkfirst=True,
    )
    with engine.begin() as conn:
        _wipe(conn)
        conn.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Process-881901', 'Deploy me', 'S1', '{}', 'Description: x\n',"
                " :t, 1, :t, 1)"
            ),
            {"id": PROCESS_ID, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO acl (id, name, valid_id, stop_after_match, config_match,"
                " config_change, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'dep-acl-old', 1, 0, 'Properties: {}\n', 'Possible: {}\n',"
                " :t, 1, :t, 1)"
            ),
            {"id": ACL_ID, "t": T0},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _wipe(conn)
        engine.dispose()
    clear_revision_cache()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    _reset(sync_url)
    clear_revision_cache()
    ticket_acl._warned_live = False
    watcher_module._candidate.clear()
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    clear_revision_cache()


@pytest.fixture
def make_client(factory: async_sessionmaker[AsyncSession]) -> Make:
    @asynccontextmanager
    async def _make(*, ownership: bool = False, user_id: int = 1) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with factory() as session:
                yield session

        user = AuthenticatedUser(
            id=user_id, login="u@example.org", first_name="U", last_name="X", auth_method="session"
        )
        app = create_app(Settings(environment="test", schema_ownership=ownership))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


async def _enable_setting(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await save_section_values(
            s,
            PROCESS_DEPLOY_SECTION,
            {"deploy_from_tiqora": True},
            secret_key=Settings(environment="test").secret_key,
        )
        await s.commit()


async def _enable_marker(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await set_setting(s, KEY_OWNERSHIP_ENABLED, VALUE_ENABLED)
        await s.commit()


async def _edit_acl(factory: async_sessionmaker[AsyncSession], name: str) -> None:
    """What the ACL editor does: change the row and register it as unsynced."""
    async with factory() as s:
        await s.execute(text("UPDATE acl SET name = :n WHERE id = :i"), {"n": name, "i": ACL_ID})
        await s.execute(
            text(
                "INSERT INTO acl_sync (acl_id, sync_state, create_time, change_time)"
                " VALUES (:i, 'not_sync', :t, :t)"
            ),
            {"i": str(ACL_ID), "t": T0},
        )
        await s.commit()


async def _live_acl_names(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as s:
        return [r.name for r in await ticket_acl.load_valid_acls(s) if r.name.startswith("dep-")]


async def _count(factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with factory() as s:
        return int((await s.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())  # noqa: S608


async def test_gate_off_returns_409(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _edit_acl(factory, "dep-acl-new")
    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == GATE_DETAIL
    assert await _count(factory, "tiqora_acl_revision") == 0
    async with factory() as s:
        assert await pending_sync_count(s, "acl") == 1


async def test_setting_deploys_acl_and_engine_reads_new_revision(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as s:
        first = await check_deploy_state(s, "acl", ownership_active=False)
    assert first.action == "snapshot"
    assert await _live_acl_names(factory) == ["dep-acl-old"]

    await _edit_acl(factory, "dep-acl-new")
    assert await _live_acl_names(factory) == ["dep-acl-old"]  # draft is not live yet
    await _enable_setting(factory)

    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "acl" and body["action"] == "recorded"
    assert body["mode"] == "local"
    assert body["revision_id"] != first.revision_id

    async with factory() as s:
        info = await latest_revision_info(s, "acl")
        assert info is not None
        assert info.source == "tiqora" and info.deployed_by == 1
        assert info.id == body["revision_id"] and info.checksum == body["checksum"]
        assert await pending_sync_count(s, "acl") == 0
    assert await _live_acl_names(factory) == ["dep-acl-new"]
    assert await _count(factory, "tiqora_acl_revision") == 2

    # Nothing changed since: no new row, same revision.
    async with make_client() as client:
        again = await client.post(URL, json={"kind": "acl"})
    assert again.status_code == 200
    assert again.json() == {**body, "action": "unchanged"}
    assert await _count(factory, "tiqora_acl_revision") == 2


async def test_process_deploy_clears_pm_entity_sync(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as s:
        await check_deploy_state(s, "process", ownership_active=False)
        await s.execute(
            text("UPDATE pm_process SET name = 'Deployed' WHERE id = :i"), {"i": PROCESS_ID}
        )
        await s.execute(
            text(
                "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
                " change_time) VALUES ('Process', 'Process-881901', 'not_sync', :t, :t)"
            ),
            {"t": T0},
        )
        await s.commit()
    await _enable_marker(factory)
    async with make_client(ownership=True) as client:
        resp = await client.post(URL, json={"kind": "process"})
    assert resp.status_code == 200 and resp.json()["action"] == "recorded"
    async with factory() as s:
        assert await pending_sync_count(s, "process") == 0
        info = await latest_revision_info(s, "process")
        assert info is not None and info.source == "tiqora"
        config = (
            await s.execute(
                text("SELECT config FROM tiqora_process_revision WHERE id = :i"), {"i": info.id}
            )
        ).scalar_one()
    assert "Deployed" in config


async def test_schema_ownership_bypasses_setting(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _edit_acl(factory, "dep-acl-owned")
    await _enable_marker(factory)
    async with make_client(ownership=True) as client:
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 200 and resp.json()["action"] == "recorded"
    assert await _live_acl_names(factory) == ["dep-acl-owned"]


async def test_env_flag_without_db_marker_is_409(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _edit_acl(factory, "dep-acl-nomarker")
    async with make_client(ownership=True) as client:
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 409 and resp.json()["detail"] == GATE_DETAIL
    assert await _count(factory, "tiqora_acl_revision") == 0


async def test_failed_sync_clear_rolls_back_the_revision(
    make_client: Make,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("purge failed")

    await _edit_acl(factory, "dep-acl-atomic")
    await _enable_marker(factory)
    monkeypatch.setattr(watcher_module, "clear_sync_table", boom)
    async with make_client(ownership=True) as client:
        with pytest.raises(RuntimeError, match="purge failed"):
            await client.post(URL, json={"kind": "acl"})
    assert await _count(factory, "tiqora_acl_revision") == 0
    assert await _count(factory, "acl_sync") == 1


async def test_non_admin_gets_403(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _enable_setting(factory)
    async with make_client(user_id=881950) as client:
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 403
    assert await _count(factory, "tiqora_acl_revision") == 0


async def test_unknown_kind_is_422(make_client: Make) -> None:
    async with make_client(ownership=True) as client:
        resp = await client.post(URL, json={"kind": "dynamic_field"})
    assert resp.status_code == 422


async def test_lock_held_elsewhere_returns_503(
    make_client: Make,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(watcher_module, "_LOCK_TIMEOUT_SECONDS", 1)
    await _enable_marker(factory)
    async with (
        factory() as holder,
        revision_write_lock(holder, "acl"),
        make_client(ownership=True) as client,
    ):
        resp = await client.post(URL, json={"kind": "acl"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == BUSY_DETAIL
    assert await _count(factory, "tiqora_acl_revision") == 0
