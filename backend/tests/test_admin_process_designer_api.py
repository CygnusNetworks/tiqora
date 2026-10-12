"""``/api/v1/admin/process-designer`` CRUD routes on MariaDB and PostgreSQL.

Seed band 894700-894999 (sub-band of the writer/designer band). The writer generates all
entity ids, so only the non-admin user id comes from the band. Every row is named
``wf3 ...``; the fixture deletes those ``pm_*`` rows, their ``pm_entity_sync`` rows and the
cache signals written above the recorded mark.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/process-designer"
NON_ADMIN = 894700
TA_MODULE = "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet"
_URLS: set[str] = set()
_TABLES = (
    "pm_process",
    "pm_activity",
    "pm_activity_dialog",
    "pm_transition",
    "pm_transition_action",
)


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _purge(
    sync_url: str,
    signal_mark: int | None = None,
    keep_sync: set[tuple[str, str]] | None = None,
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        if keep_sync is not None:  # sync rows of deleted entities have no pm_* row left
            for etype, eid in conn.execute(
                text("SELECT entity_type, entity_id FROM pm_entity_sync")
            ).all():
                if (etype, eid) not in keep_sync:
                    conn.execute(
                        text(
                            "DELETE FROM pm_entity_sync WHERE entity_type = :t AND entity_id = :e"
                        ),
                        {"t": etype, "e": eid},
                    )
        for table in _TABLES:
            ids = conn.execute(
                text(f"SELECT entity_id FROM {table} WHERE name LIKE 'wf3%'")
            ).scalars()
            for eid in list(ids):
                conn.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})
            conn.execute(text(f"DELETE FROM {table} WHERE name LIKE 'wf3%'"))
        if signal_mark is not None:
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": signal_mark}
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraCacheInvalidation.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
        keep_sync = {
            (r[0], r[1])
            for r in conn.execute(text("SELECT entity_type, entity_id FROM pm_entity_sync")).all()
        }
    sync_engine.dispose()
    _purge(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    _purge(sync_url, mark, keep_sync)


@pytest.fixture
def make_client(factory: async_sessionmaker[AsyncSession]) -> Any:
    @asynccontextmanager
    async def _make(user_id: int = 1) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with factory() as session:
                yield session

        user = AuthenticatedUser(
            id=user_id,
            login="wf3@example.org",
            first_name="W",
            last_name="F",
            auth_method="session",
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


async def _sync_rows(
    factory: async_sessionmaker[AsyncSession], entity_id: str
) -> list[tuple[str, str]]:
    async with factory() as s:
        rows = await s.execute(
            text("SELECT entity_type, sync_state FROM pm_entity_sync WHERE entity_id = :e"),
            {"e": entity_id},
        )
        return [(r[0], r[1]) for r in rows.all()]


async def test_non_admin_gets_403(make_client: Any) -> None:
    async with make_client(NON_ADMIN) as client:
        assert (await client.get(f"{BASE}/processes")).status_code == 403
        resp = await client.post(f"{BASE}/processes", json={"name": "wf3 nope"})
        assert resp.status_code == 403
        assert (await client.get(f"{BASE}/catalog")).status_code == 403


async def test_create_edit_delete_round_trip(
    make_client: Any, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with make_client() as client:
        created = await client.post(
            f"{BASE}/processes", json={"name": "wf3 round trip", "description": "d"}
        )
        assert created.status_code == 201
        draft = created.json()
        pid = draft["process"]["entity_id"]
        assert draft["state_name"] == "Inactive"
        assert draft["process"]["sync_state"] == "not_sync"
        assert await _sync_rows(factory, pid) == [("Process", "not_sync")]

        listed = (await client.get(f"{BASE}/processes")).json()
        row = next(r for r in listed if r["entity_id"] == pid)
        assert row["name"] == "wf3 round trip"
        assert row["sync_state"] == "not_sync"
        assert row["state_entity_id"] == "S2"

        dialog = await client.post(
            f"{BASE}/entities/ActivityDialog",
            json={
                "name": "wf3 dialog",
                "config": {
                    "DescriptionShort": "d",
                    "Fields": {"Title": {"Display": 1}},
                    "FieldOrder": ["Title"],
                    "Interface": ["AgentInterface"],
                },
            },
        )
        assert dialog.status_code == 201
        did = dialog.json()["entity_id"]
        activity = await client.post(
            f"{BASE}/entities/Activity",
            json={"name": "wf3 act", "config": {"ActivityDialog": {"1": did}}},
        )
        assert activity.status_code == 201
        aid = activity.json()["entity_id"]

        updated = await client.put(
            f"{BASE}/processes/{pid}",
            json={
                "expected_version": draft["process"]["version"],
                "name": "wf3 round trip 2",
                "description": "d2",
                "state_entity_id": "S2",
                "start_activity": aid,
                "start_activity_dialog": did,
                "path": {aid: {}},
                "layout": {aid: {"left": "10", "top": 20}},
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["process"]["name"] == "wf3 round trip 2"
        assert body["process"]["config"]["StartActivity"] == aid
        assert [a["entity_id"] for a in body["activities"]] == [aid]
        assert body["layout"][aid] == {"left": "10", "top": "20"}

        got = (await client.get(f"{BASE}/processes/{pid}")).json()
        assert got["process"]["version"] == body["process"]["version"]

        entity = dialog.json()
        edited = await client.put(
            f"{BASE}/entities/ActivityDialog/{did}",
            json={
                "expected_version": entity["version"],
                "name": "wf3 dialog 2",
                "config": entity["config"],
            },
        )
        assert edited.status_code == 200
        assert edited.json()["name"] == "wf3 dialog 2"

        copied = await client.post(f"{BASE}/processes/{pid}/copy")
        assert copied.status_code == 201
        cid = copied.json()["process"]["entity_id"]
        assert cid != pid

        assert (await client.delete(f"{BASE}/processes/{cid}")).status_code == 204
        assert (await client.get(f"{BASE}/processes/{cid}")).status_code == 404
        assert (await client.delete(f"{BASE}/processes/{pid}")).status_code == 204
        assert (await client.delete(f"{BASE}/entities/Activity/{aid}")).status_code == 204
        assert (await client.delete(f"{BASE}/entities/ActivityDialog/{did}")).status_code == 204
    assert await _sync_rows(factory, pid) == [("Process", "deleted")]


async def test_conflict_detail_is_german(make_client: Any) -> None:
    async with make_client() as client:
        draft = (await client.post(f"{BASE}/processes", json={"name": "wf3 conflict"})).json()
        pid = draft["process"]["entity_id"]
        payload = {
            "expected_version": draft["process"]["version"],
            "name": "wf3 conflict a",
            "description": "",
            "state_entity_id": "S2",
            "path": {},
            "layout": {},
        }
        assert (await client.put(f"{BASE}/processes/{pid}", json=payload)).status_code == 200
        stale = await client.put(
            f"{BASE}/processes/{pid}", json={**payload, "name": "wf3 conflict b"}
        )
        assert stale.status_code == 409
        assert "inzwischen geändert" in stale.json()["detail"]

        trans = (
            await client.post(
                f"{BASE}/entities/Transition",
                json={"name": "wf3 tr", "config": {"Condition": {}, "ConditionLinking": "and"}},
            )
        ).json()
        ok = await client.put(
            f"{BASE}/entities/Transition/{trans['entity_id']}",
            json={
                "expected_version": trans["version"],
                "name": "wf3 tr 2",
                "config": trans["config"],
            },
        )
        assert ok.status_code == 200
        stale_entity = await client.put(
            f"{BASE}/entities/Transition/{trans['entity_id']}",
            json={
                "expected_version": trans["version"],
                "name": "wf3 tr 3",
                "config": trans["config"],
            },
        )
        assert stale_entity.status_code == 409
        assert "inzwischen geändert" in stale_entity.json()["detail"]


async def test_active_process_cannot_be_deleted_and_unknown_is_404(make_client: Any) -> None:
    async with make_client() as client:
        draft = (await client.post(f"{BASE}/processes", json={"name": "wf3 active"})).json()
        pid = draft["process"]["entity_id"]
        activated = await client.put(
            f"{BASE}/processes/{pid}",
            json={
                "expected_version": draft["process"]["version"],
                "name": "wf3 active",
                "description": "",
                "state_entity_id": "S1",
                "path": {},
                "layout": {},
            },
        )
        assert activated.status_code == 200
        blocked = await client.delete(f"{BASE}/processes/{pid}")
        assert blocked.status_code == 409
        assert "inaktive" in blocked.json()["detail"]
        missing = await client.get(f"{BASE}/processes/Process-{'0' * 32}")
        assert missing.status_code == 404
        bad_ref = await client.put(
            f"{BASE}/processes/{pid}",
            json={
                "expected_version": activated.json()["process"]["version"],
                "name": "wf3 active",
                "description": "",
                "state_entity_id": "S1",
                "path": {f"Activity-{'0' * 32}": {}},
                "layout": {},
            },
        )
        assert bad_ref.status_code == 422


async def test_entity_type_path_rejects_process(make_client: Any) -> None:
    async with make_client() as client:
        listed = await client.get(f"{BASE}/entities/Process")
        assert listed.status_code == 422
        created = await client.post(
            f"{BASE}/entities/Process", json={"name": "wf3 sneaky", "config": {}}
        )
        assert created.status_code == 422
        deleted = await client.delete(f"{BASE}/entities/Process/Process-{'0' * 32}")
        assert deleted.status_code == 422


async def test_in_use_detail_names_the_users(make_client: Any) -> None:
    async with make_client() as client:
        act = (
            await client.post(
                f"{BASE}/entities/Activity", json={"name": "wf3 used act", "config": {}}
            )
        ).json()
        aid = act["entity_id"]
        draft = (await client.post(f"{BASE}/processes", json={"name": "wf3 user proc"})).json()
        updated = await client.put(
            f"{BASE}/processes/{draft['process']['entity_id']}",
            json={
                "expected_version": draft["process"]["version"],
                "name": "wf3 user proc",
                "description": "",
                "state_entity_id": "S2",
                "path": {aid: {}},
                "layout": {},
            },
        )
        assert updated.status_code == 200
        blocked = await client.delete(f"{BASE}/entities/Activity/{aid}")
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "Wird noch verwendet von: wf3 user proc"
        summaries = (await client.get(f"{BASE}/entities/Activity")).json()
        mine = next(s for s in summaries if s["entity_id"] == aid)
        assert mine["used_by"] == [draft["process"]["entity_id"]]
        # The entity survived the refused delete (one commit per request).
        assert (await client.get(f"{BASE}/entities/Activity")).status_code == 200
        assert any(s["entity_id"] == aid for s in summaries)


async def test_catalog_is_served(make_client: Any) -> None:
    async with make_client() as client:
        resp = await client.get(f"{BASE}/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert "AgentInterface" in body["interfaces"]
    assert any(a["name"] for a in body["actions"])
