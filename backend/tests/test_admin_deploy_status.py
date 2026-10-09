"""``GET /admin/deploy-status`` and ``GET /admin/processes``.

HTTP through the admin router on MariaDB and PostgreSQL. Seed band 881800-881899.
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
from tiqora.deployed import clear_revision_cache, record_revision
from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ownership import KEY_OWNERSHIP_ENABLED
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

STATUS = "/api/v1/admin/deploy-status"
PROCESSES = "/api/v1/admin/processes"
T0 = datetime(2026, 3, 4, 5, 6, 7)
SETTING_KEY = "process.deploy_from_tiqora"
_URLS: set[str] = set()

_CLEANUP = (
    "DELETE FROM tiqora_process_revision",
    "DELETE FROM tiqora_acl_revision",
    "DELETE FROM pm_entity_sync",
    "DELETE FROM acl_sync",
    "DELETE FROM pm_process WHERE id BETWEEN 881800 AND 881899",
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
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    clear_revision_cache()


@pytest.fixture
def make_client(factory: async_sessionmaker[AsyncSession]) -> Make:
    @asynccontextmanager
    async def _make(*, ownership: bool = False, admin: bool = True) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with factory() as session:
                yield session

        # Admin = user 1 (root); other ids are plain agents without admin rights.
        user = AuthenticatedUser(
            id=1 if admin else 881899,
            login="u@example.org",
            first_name="U",
            last_name="X",
            auth_method="session",
        )
        app = create_app(Settings(environment="test", schema_ownership=ownership))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


def _dump() -> dict[str, Any]:
    def common(eid: str, name: str) -> dict[str, Any]:
        return {"ID": 1, "EntityID": eid, "Name": name}

    return {
        "Process": {
            "Process-881801": {
                **common("Process-881801", "Dormant"),
                "StateEntityID": "S2",
                "State": "Inactive",
                "StartActivity": "Activity-881801",
                "StartActivityDialog": "ActivityDialog-881801",
                "Path": {},
            },
            "Process-881802": {
                **common("Process-881802", "Running"),
                "StateEntityID": "S1",
                "State": "Active",
                "StartActivity": "Activity-881801",
                "StartActivityDialog": "ActivityDialog-881802",
                "Path": {},
            },
        },
        "Activity": {
            "Activity-881801": {**common("Activity-881801", "A1"), "ActivityDialog": {}},
        },
        "ActivityDialog": {
            "ActivityDialog-881801": {
                **common("ActivityDialog-881801", "D1"),
                "Interface": ["AgentInterface"],
                "Fields": {},
                "FieldOrder": [],
            },
            "ActivityDialog-881802": {
                **common("ActivityDialog-881802", "D2"),
                "Interface": [],
                "Fields": {},
                "FieldOrder": [],
            },
        },
        "Transition": {},
        "TransitionAction": {},
    }


async def _seed_revisions(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await record_revision(s, "process", dump=_dump(), source="znuny", deployed_by=1)
        await record_revision(
            s, "acl", dump={"ACL": {}, "ACLs": {}}, source="initial", deployed_by=None
        )
        await s.commit()


async def _add_acl_sync(factory: async_sessionmaker[AsyncSession], n: int) -> None:
    async with factory() as s:
        for i in range(n):
            await s.execute(
                text(
                    "INSERT INTO acl_sync (acl_id, sync_state, create_time, change_time)"
                    " VALUES (:i, 'not_sync', :t, :t)"
                ),
                {"i": str(881800 + i), "t": T0},
            )
        await s.commit()


async def test_status_without_revision_is_running_live(make_client: Make) -> None:
    async with make_client() as client:
        resp = await client.get(STATUS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for kind in ("process", "acl"):
        assert body[kind] == {"revision": None, "pending_changes": 0, "running_live": True}
    assert body["ownership_active"] is False
    assert body["can_deploy_from_tiqora"] is False


async def test_status_reports_pending_and_revision(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_revisions(factory)
    await _add_acl_sync(factory, 2)
    async with make_client() as client:
        body = (await client.get(STATUS)).json()
    assert body["acl"]["pending_changes"] == 2
    assert body["acl"]["running_live"] is False
    assert body["acl"]["revision"]["source"] == "initial"
    assert body["acl"]["revision"]["deployed_by"] is None
    assert body["process"]["pending_changes"] == 0
    assert body["process"]["running_live"] is False
    rev = body["process"]["revision"]
    assert rev["source"] == "znuny" and rev["deployed_by"] == 1
    assert rev["entity_count"] > 0
    assert rev["deployed_at"].endswith("Z") or "+" in rev["deployed_at"]


async def test_can_deploy_follows_setting_and_ownership(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with make_client() as client:
        assert (await client.get(STATUS)).json()["can_deploy_from_tiqora"] is False
    async with factory() as s:
        await save_section_values(
            s,
            PROCESS_DEPLOY_SECTION,
            {"deploy_from_tiqora": True},
            secret_key=Settings(environment="test").secret_key,
        )
        await s.commit()
    async with make_client() as client:
        body = (await client.get(STATUS)).json()
    assert body["can_deploy_from_tiqora"] is True
    assert body["ownership_active"] is False


async def test_admin_processes_lists_inactive_too(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_revisions(factory)
    async with make_client() as client:
        resp = await client.get(PROCESSES)
    assert resp.status_code == 200, resp.text
    by_id = {p["entity_id"]: p for p in resp.json()}
    assert by_id["Process-881801"] == {
        "entity_id": "Process-881801",
        "name": "Dormant",
        "state_entity_id": "S2",
        "state_name": "Inactive",
        "activity_count": 1,
        "start_interfaces": ["AgentInterface"],
    }
    assert by_id["Process-881802"]["state_name"] == "Active"
    assert by_id["Process-881802"]["start_interfaces"] == []


async def test_requires_admin(make_client: Make) -> None:
    async with make_client(admin=False) as client:
        assert (await client.get(STATUS)).status_code == 403
        assert (await client.get(PROCESSES)).status_code == 403
