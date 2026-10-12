"""Deploy through the Znuny add-on TiqoraSync while Znuny runs (plan D15, signal mode).

The tests write the heartbeat and status keys themselves, as the add-on would.
HTTP through the admin router on MariaDB and PostgreSQL. Seed band 898000-898999.
"""

from __future__ import annotations

import calendar
import json
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraAclRevision,
    TiqoraCacheInvalidation,
    TiqoraProcessRevision,
    TiqoraSettings,
)
from tiqora.deployed import clear_revision_cache, latest_revision_info
from tiqora.deployed import watcher as watcher_module
from tiqora.deployed.gate import deploy_mode
from tiqora.deployed.settings import PROCESS_DEPLOY_SECTION
from tiqora.deployed.sync_tables import pending_sync_count
from tiqora.deployed.watcher import check_deploy_state
from tiqora.deployed.znuny_sync import (
    DEPLOY_REQUESTED_KEY,
    DEPLOY_STATUS_KEY,
    HEARTBEAT_KEY,
    addon_can_deploy,
    read_znuny_sync_status,
)
from tiqora.domain import ticket_acl
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ownership import KEY_OWNERSHIP_ENABLED, VALUE_ENABLED
from tiqora.domain.settings_store import get_setting, set_setting
from tiqora.jobs.service import utc_now
from tiqora.process.dump import build_process_dump, dump_checksum
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

URL = "/api/v1/admin/deploy"
STATUS = "/api/v1/admin/deploy-status"
T0 = datetime(2026, 3, 4, 5, 6, 7)
PROCESS_ID = 898001
ACL_ID = 898001
SETTING_KEY = "process.deploy_from_tiqora"
_KEYS = [
    SETTING_KEY,
    KEY_OWNERSHIP_ENABLED,
    HEARTBEAT_KEY,
    *DEPLOY_STATUS_KEY.values(),
    *DEPLOY_REQUESTED_KEY.values(),
]
_URLS: set[str] = set()

_CLEANUP = (
    "DELETE FROM tiqora_process_revision",
    "DELETE FROM tiqora_acl_revision",
    "DELETE FROM pm_entity_sync",
    "DELETE FROM acl_sync",
    "DELETE FROM tiqora_cache_invalidation WHERE cache_type LIKE 'TiqoraDeploy\\_%'",
    "DELETE FROM pm_process WHERE id BETWEEN 898000 AND 898999",
    "DELETE FROM acl WHERE id BETWEEN 898000 AND 898999",
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
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(_KEYS)))


def _reset(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        engine,
        tables=[  # type: ignore[list-item]
            TiqoraProcessRevision.__table__,
            TiqoraAclRevision.__table__,
            TiqoraSettings.__table__,
            TiqoraCacheInvalidation.__table__,
        ],
        checkfirst=True,
    )
    with engine.begin() as conn:
        _wipe(conn)
        conn.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Process-898001', 'Sync me', 'S1', '{}', 'Description: x\n',"
                " :t, 1, :t, 1)"
            ),
            {"id": PROCESS_ID, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO acl (id, name, valid_id, stop_after_match, config_match,"
                " config_change, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'sync-acl-old', 1, 0, 'Properties: {}\n', 'Possible: {}\n',"
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
    async def _make(*, ownership: bool = False) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with factory() as session:
                yield session

        user = AuthenticatedUser(
            id=1, login="wf3@example.org", first_name="U", last_name="X", auth_method="session"
        )
        app = create_app(Settings(environment="test", schema_ownership=ownership))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


def _epoch(moment: datetime) -> int:
    return calendar.timegm(moment.timetuple())


async def _put(factory: async_sessionmaker[AsyncSession], key: str, value: object) -> None:
    async with factory() as s:
        await set_setting(s, key, value if isinstance(value, str) else json.dumps(value))


async def _heartbeat(
    factory: async_sessionmaker[AsyncSession],
    *,
    age: timedelta = timedelta(seconds=30),
    capabilities: tuple[str, ...] = ("process_deploy", "acl_deploy"),
    version: str = "1.2.0",
) -> None:
    await _put(
        factory,
        HEARTBEAT_KEY,
        {
            "version": version,
            "framework": "6.5.22",
            "capabilities": list(capabilities),
            "at": _epoch(utc_now() - age),
        },
    )


async def _addon_status(
    factory: async_sessionmaker[AsyncSession],
    kind: str,
    *,
    signal_id: int,
    revision_id: int,
    status: str = "ok",
    message: str = "",
) -> None:
    await _put(
        factory,
        DEPLOY_STATUS_KEY[kind],
        {
            "signal_id": signal_id,
            "revision_id": revision_id,
            "status": status,
            "message": message,
            "at": _epoch(utc_now()),
        },
    )


async def _draft_process(factory: async_sessionmaker[AsyncSession], name: str) -> None:
    """What the designer does: edit a row and register two unsynced entities."""
    async with factory() as s:
        await s.execute(
            text("UPDATE pm_process SET name = :n WHERE id = :i"), {"n": name, "i": PROCESS_ID}
        )
        for eid in ("Process-898001", "Activity-" + "a" * 32):
            await s.execute(text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid})
            await s.execute(
                text(
                    "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
                    " change_time) VALUES ('Process', :e, 'not_sync', :t, :t)"
                ),
                {"e": eid, "t": T0},
            )
        await s.commit()


async def _draft_acl(factory: async_sessionmaker[AsyncSession], name: str) -> None:
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


async def _purge(factory: async_sessionmaker[AsyncSession]) -> None:
    """What TiqoraSync's EntitySyncStatePurge does."""
    async with factory() as s:
        await s.execute(text("DELETE FROM pm_entity_sync"))
        await s.commit()


async def _count(factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with factory() as s:
        return int((await s.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())  # noqa: S608


async def _signals(factory: async_sessionmaker[AsyncSession]) -> list[tuple[int, str]]:
    async with factory() as s:
        rows = await s.execute(
            text(
                "SELECT id, cache_type FROM tiqora_cache_invalidation"
                " WHERE cache_type LIKE 'TiqoraDeploy\\_%' ORDER BY id"
            )
        )
        return [(int(r[0]), str(r[1])) for r in rows]


async def _baseline_revision(
    factory: async_sessionmaker[AsyncSession], kind: str = "process"
) -> int:
    async with factory() as s:
        out = await check_deploy_state(s, kind, ownership_active=False)  # type: ignore[arg-type]
    assert out.action == "snapshot" and out.revision_id is not None
    return out.revision_id


async def test_deploy_refused_without_fresh_heartbeat(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _draft_process(factory, "wf3 refused")

    async def refused() -> str:
        async with make_client() as client:
            resp = await client.post(URL, json={"kind": "process"})
        assert resp.status_code == 409, resp.text
        assert await _count(factory, "tiqora_process_revision") == 0
        assert await _signals(factory) == []
        assert await _count(factory, "pm_entity_sync") == 2
        return str(resp.json()["detail"])

    detail = await refused()  # no heartbeat key at all
    assert "TiqoraSync ab Version 1.2.0" in detail and "zuletzt gesehen: nie" in detail
    assert "Prozesse und ACLs in Tiqora bereitstellen (Znuny läuft nicht mehr)" in detail

    await _heartbeat(factory, age=timedelta(minutes=11))
    assert "Version 1.2.0" in await refused()  # too old

    await _heartbeat(factory, capabilities=("acl_deploy",))
    await refused()  # the add-on cannot deploy processes

    await _heartbeat(factory, capabilities=())
    await refused()

    await _put(factory, HEARTBEAT_KEY, "not json")
    await refused()
    await _put(factory, HEARTBEAT_KEY, {"version": "1.2.0", "capabilities": ["process_deploy"]})
    await refused()  # no timestamp


async def test_heartbeat_age_boundary(factory: async_sessionmaker[AsyncSession]) -> None:
    now = utc_now()
    await _heartbeat(factory, age=timedelta(minutes=9, seconds=50))
    async with factory() as s:
        assert await addon_can_deploy(s, "process", now=now)
        assert await addon_can_deploy(s, "acl", now=now)
        assert not await addon_can_deploy(s, "process", now=now + timedelta(minutes=1))


async def test_heartbeat_from_the_future_is_not_fresh(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utc_now()
    # A clock slightly ahead still counts; one far ahead must not stay "fresh" forever.
    await _heartbeat(factory, age=-timedelta(minutes=5))
    async with factory() as s:
        assert await addon_can_deploy(s, "process", now=now)
    await _heartbeat(factory, age=-timedelta(hours=2))
    async with factory() as s:
        assert not await addon_can_deploy(s, "process", now=now)


async def test_signal_mode_records_revision_and_queues_signal(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    first = await _baseline_revision(factory)
    await _draft_process(factory, "wf3 signalled")
    await _heartbeat(factory)
    assert await _count(factory, "pm_entity_sync") == 2

    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "process"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "signal" and body["action"] == "recorded"
    assert body["revision_id"] != first

    async with factory() as s:
        info = await latest_revision_info(s, "process")
        assert info is not None and info.id == body["revision_id"]
        assert info.source == "tiqora" and info.deployed_by == 1
        raw = (
            await s.execute(
                text("SELECT config FROM tiqora_process_revision WHERE id = :i"), {"i": info.id}
            )
        ).scalar_one()
        assert "wf3 signalled" in raw
        requested = json.loads(await get_setting(s, DEPLOY_REQUESTED_KEY["process"]) or "{}")
    signals = await _signals(factory)
    assert [name for _id, name in signals] == [f"TiqoraDeploy_process_{info.id}"]
    assert requested["signal_id"] == signals[0][0] and requested["revision_id"] == info.id
    # Znuny's add-on purges; Tiqora must not.
    assert await _count(factory, "pm_entity_sync") == 2


async def test_signal_mode_unchanged_still_queues_signal(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    first = await _baseline_revision(factory)
    # Znuny holds drafts, but Tiqora's content equals the newest revision.
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
                " change_time) VALUES ('Process', 'Process-898001', 'not_sync', :t, :t)"
            ),
            {"t": T0},
        )
        await s.commit()
    await _heartbeat(factory)
    async with make_client() as client:
        one = await client.post(URL, json={"kind": "process"})
        two = await client.post(URL, json={"kind": "process"})
    assert one.json()["action"] == "unchanged" and one.json()["revision_id"] == first
    assert two.json()["action"] == "unchanged" and two.json()["mode"] == "signal"
    assert await _count(factory, "tiqora_process_revision") == 1
    assert [name for _i, name in await _signals(factory)] == [f"TiqoraDeploy_process_{first}"] * 2
    assert await _count(factory, "pm_entity_sync") == 1


async def test_status_pending_until_addon_reports_same_revision(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _baseline_revision(factory)
    await _heartbeat(factory)

    async def sync() -> dict[str, Any]:
        async with make_client() as client:
            body = (await client.get(STATUS)).json()
        assert body["process"]["deploy_mode"] == "signal"
        return dict(body["process"]["znuny_sync"])

    # nothing was ever signalled
    assert (await sync())["state"] == "not_needed"

    await _draft_process(factory, "wf3 status")
    async with make_client() as client:
        deploy = (await client.post(URL, json={"kind": "process"})).json()
    signal_id, rev_id = (await _signals(factory))[0][0], deploy["revision_id"]
    pending = await sync()
    assert pending["state"] == "pending" and pending["revision_id"] == rev_id
    assert pending["overdue"] is False and pending["since"] is not None

    # the add-on's report for an older signal does not count
    await _addon_status(factory, "process", signal_id=signal_id - 1, revision_id=rev_id)
    assert (await sync())["state"] == "pending"

    await _addon_status(factory, "process", signal_id=signal_id, revision_id=rev_id)
    ok = await sync()
    assert ok["state"] == "ok" and ok["revision_id"] == rev_id

    await _addon_status(
        factory, "process", signal_id=signal_id, revision_id=rev_id, status="error",
        message="Datei ZZZProcessManagement.pm nicht schreibbar",
    )  # fmt: skip
    err = await sync()
    assert (
        err["state"] == "error"
        and err["message"] == "Datei ZZZProcessManagement.pm nicht schreibbar"
    )

    # a second, unchanged deploy queues a new signal for the same revision: pending again
    async with make_client() as client:
        again = (await client.post(URL, json={"kind": "process"})).json()
    assert again["action"] == "unchanged" and again["revision_id"] == rev_id
    assert (await sync())["state"] == "pending"
    new_signal = (await _signals(factory))[-1][0]
    assert new_signal > signal_id
    await _addon_status(factory, "process", signal_id=new_signal, revision_id=rev_id)
    assert (await sync())["state"] == "ok"


async def test_status_overdue_and_addon_missing(
    factory: async_sessionmaker[AsyncSession], make_client: Make
) -> None:
    await _baseline_revision(factory)
    await _heartbeat(factory)
    await _draft_process(factory, "wf3 overdue")
    async with make_client() as client:
        assert (await client.post(URL, json={"kind": "process"})).status_code == 200
    async with factory() as s:
        newest = await latest_revision_info(s, "process")
        soon = await read_znuny_sync_status(
            s, "process", newest=newest, mode="signal", now=utc_now() + timedelta(minutes=4)
        )
        late = await read_znuny_sync_status(
            s, "process", newest=newest, mode="signal", now=utc_now() + timedelta(minutes=6)
        )
        gone = await read_znuny_sync_status(
            s, "process", newest=newest, mode=None, now=utc_now() + timedelta(minutes=6)
        )
        local = await read_znuny_sync_status(
            s, "process", newest=newest, mode="local", now=utc_now()
        )
    assert (soon.state, soon.overdue) == ("pending", False)
    assert (late.state, late.overdue) == ("pending", True)
    assert gone.state == "addon_missing"
    assert local.state == "not_needed"


async def test_local_mode_unchanged_behaviour(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _baseline_revision(factory)
    await _draft_process(factory, "wf3 local")
    async with factory() as s:
        await set_setting(s, KEY_OWNERSHIP_ENABLED, VALUE_ENABLED)
    async with make_client(ownership=True) as client:
        resp = await client.post(URL, json={"kind": "process"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "local" and resp.json()["action"] == "recorded"
    assert await _count(factory, "pm_entity_sync") == 0
    assert await _signals(factory) == []

    # the admin setting alone is local mode as well
    async with factory() as s:
        await s.execute(delete(TiqoraSettings).where(TiqoraSettings.key == KEY_OWNERSHIP_ENABLED))
        await s.commit()
        await save_section_values(
            s,
            PROCESS_DEPLOY_SECTION,
            {"deploy_from_tiqora": True},
            secret_key=Settings(environment="test").secret_key,
        )
        await s.commit()
    await _draft_process(factory, "wf3 local 2")
    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "process"})
    assert resp.json()["mode"] == "local"
    assert await _count(factory, "pm_entity_sync") == 0
    assert await _signals(factory) == []


async def test_fresh_heartbeat_beats_setting(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _baseline_revision(factory)
    async with factory() as s:
        await save_section_values(
            s,
            PROCESS_DEPLOY_SECTION,
            {"deploy_from_tiqora": True},
            secret_key=Settings(environment="test").secret_key,
        )
        await s.commit()
    settings = Settings(environment="test")
    async with factory() as s:
        assert await deploy_mode(s, settings, "process") == "local"  # no add-on
    await _heartbeat(factory)
    async with factory() as s:
        assert await deploy_mode(s, settings, "process") == "signal"
        # a stale heartbeat proves nothing: the setting decides again
    await _heartbeat(factory, age=timedelta(minutes=30))
    async with factory() as s:
        assert await deploy_mode(s, settings, "process") == "local"
    await _heartbeat(factory)
    await _draft_process(factory, "wf3 beats")
    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "process"})
    assert resp.json()["mode"] == "signal"
    assert await _count(factory, "pm_entity_sync") == 2
    assert len(await _signals(factory)) == 1


async def test_watcher_unchanged_after_addon_purge(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _baseline_revision(factory)
    await _draft_process(factory, "wf3 purge")
    await _heartbeat(factory)
    async with make_client() as client:
        deployed = (await client.post(URL, json={"kind": "process"})).json()
    async with factory() as s:
        waiting = await check_deploy_state(s, "process", ownership_active=False)
    assert waiting.action == "waiting_for_deploy" and waiting.revision_id == deployed["revision_id"]

    await _purge(factory)  # the add-on dumped and purged
    async with factory() as s:
        one = await check_deploy_state(s, "process", ownership_active=False)
        two = await check_deploy_state(s, "process", ownership_active=False)
    assert one.action == two.action == "unchanged"
    assert one.revision_id == two.revision_id == deployed["revision_id"]
    assert await _count(factory, "tiqora_process_revision") == 2  # initial + tiqora, no duplicate


async def test_edit_between_deploy_and_purge_converges(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    await _baseline_revision(factory)
    await _draft_process(factory, "wf3 deployed")
    await _heartbeat(factory)
    async with make_client() as client:
        deployed = (await client.post(URL, json={"kind": "process"})).json()

    await _draft_process(factory, "wf3 edited later")  # designer edit before the add-on runs
    await _purge(factory)  # the add-on dumps the edit as well, then purges
    async with factory() as s:
        one = await check_deploy_state(s, "process", ownership_active=False)
        two = await check_deploy_state(s, "process", ownership_active=False)
        live = dump_checksum(await build_process_dump(s))
        info = await latest_revision_info(s, "process")
    assert one.action == "confirming" and two.action == "snapshot"
    assert info is not None and info.id == two.revision_id != deployed["revision_id"]
    assert info.source == "znuny" and info.checksum == live


async def test_acl_kind_same_rules(
    make_client: Make, factory: async_sessionmaker[AsyncSession]
) -> None:
    acl_first = await _baseline_revision(factory, "acl")
    await _draft_acl(factory, "sync-acl-new")

    await _heartbeat(factory, capabilities=("process_deploy",))
    async with make_client() as client:
        refused = await client.post(URL, json={"kind": "acl"})
    assert refused.status_code == 409 and await _signals(factory) == []

    await _heartbeat(factory, capabilities=("acl_deploy",))
    async with make_client() as client:
        resp = await client.post(URL, json={"kind": "acl"})
        status = (await client.get(STATUS)).json()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "signal" and body["action"] == "recorded"
    assert body["revision_id"] != acl_first
    assert [name for _i, name in await _signals(factory)] == [
        f"TiqoraDeploy_acl_{body['revision_id']}"
    ]
    assert await _count(factory, "acl_sync") == 1  # never cleared by Tiqora
    assert status["acl"]["deploy_mode"] == "signal"
    assert status["process"]["deploy_mode"] is None
    assert status["acl"]["znuny_sync"]["state"] == "pending"
    async with factory() as s:
        assert await pending_sync_count(s, "acl") == 1
        info = await latest_revision_info(s, "acl")
    assert info is not None and info.source == "tiqora"
