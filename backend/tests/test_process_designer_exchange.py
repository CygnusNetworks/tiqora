"""Process import / export in Znuny's file format (plan D13, Task 15).

Seed band 896000-896399: the dynamic field ``TestDropdownACLProcess`` (the sample files use it)
is created with id 896002 and deleted with the module; the non-admin user id is 896001. The
imported entities get generated ids and the sample names (not ``wf3``), so the fixture
snapshots the ``pm_*`` entity ids before each test and deletes every row, sync row and cache
signal that appeared meanwhile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCacheInvalidation, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.designer.exchange import (
    MAX_ENTITIES,
    ImportRefused,
    export_process_yaml,
    import_process_yaml,
)
from tiqora.process.designer.writer import EntityNotFound, InvalidReference, ProcessWriter
from tiqora.process.designer.yaml_codec import dump_znuny_yaml, load_znuny_yaml, stringify_scalars

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/process-designer"
SAMPLES = Path(__file__).parent / "fixtures" / "process_samples"
NON_ADMIN = 896001
DF_ID = 896002  # and 896003
DF_NAME = "TestDropdownACLProcess"
DF_NAMES = {DF_ID: DF_NAME, DF_ID + 1: "TestTextZeroProcess"}
USER = 1
KINDS = ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction")
_TABLES = {
    "Process": "pm_process",
    "Activity": "pm_activity",
    "ActivityDialog": "pm_activity_dialog",
    "Transition": "pm_transition",
    "TransitionAction": "pm_transition_action",
}
_SECTIONS = ("Activities", "ActivityDialogs", "Transitions", "TransitionActions")
_URLS: set[str] = set()


def _sample(name: str) -> str:
    return (SAMPLES / f"{name}.yml").read_text(encoding="utf-8")


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _entity_ids(conn: Any) -> dict[str, set[str]]:
    return {
        kind: set(conn.execute(text(f"SELECT entity_id FROM {table}")).scalars())
        for kind, table in _TABLES.items()
    }


def _purge(sync_url: str, before: dict[str, set[str]] | None, signal_mark: int | None) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        if before is not None:
            for kind, table in _TABLES.items():
                for eid in set(conn.execute(text(f"SELECT entity_id FROM {table}")).scalars()):
                    if eid not in before[kind]:
                        conn.execute(text(f"DELETE FROM {table} WHERE entity_id = :e"), {"e": eid})
                        conn.execute(
                            text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid}
                        )
        if signal_mark is not None:
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": signal_mark}
            )
        for df_id in DF_NAMES:
            conn.execute(text("DELETE FROM dynamic_field WHERE id = :i"), {"i": df_id})
    engine.dispose()


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    session: AsyncSession
    sync_url: str
    signal_mark: int
    before: dict[str, set[str]]

    def writer(self) -> ProcessWriter:
        return ProcessWriter(self.session, user_id=USER)

    async def import_(self, content: str, *, overwrite: bool = False) -> Any:
        result = await import_process_yaml(
            self.session, self.writer(), content, overwrite_existing=overwrite
        )
        await self.session.commit()
        return result

    async def count(self, table: str) -> int:
        return int((await self.session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())

    async def new_ids(self, kind: str) -> set[str]:
        rows = await self.session.execute(text(f"SELECT entity_id FROM {_TABLES[kind]}"))
        return set(rows.scalars()) - self.before[kind]

    async def sync_state(self, kind: str, entity_id: str) -> str | None:
        row = (
            await self.session.execute(
                text(
                    "SELECT sync_state FROM pm_entity_sync"
                    " WHERE entity_type = :t AND entity_id = :e"
                ),
                {"t": kind, "e": entity_id},
            )
        ).first()
        return None if row is None else str(row[0])

    async def seed_df(self) -> None:
        for df_id, name in DF_NAMES.items():
            await self.session.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :id, 'Dropdown', 'Ticket', '---', 1,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": df_id, "n": name},
            )
        await self.session.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[TiqoraCacheInvalidation.__table__, TiqoraSettings.__table__],  # type: ignore[list-item]
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        before = _entity_ids(conn)
        mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
            ).scalar_one()
        )
    sync_engine.dispose()
    _purge(sync_url, None, None)  # a stale DF of a crashed run
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield Env(factory, session, sync_url, mark, before)
        await session.rollback()
    await engine.dispose()
    _purge(sync_url, before, mark)


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url, None, None)


# --- helpers for the round trip --------------------------------------------------------------


def _canonical(data: dict[str, Any]) -> dict[str, Any]:
    """Drop ID/CreateTime/ChangeTime, name every entity id by ``<Section>:<Name>`` and drop
    the ``Scope: Global`` default that Znuny's ``*Update`` adds."""
    data = stringify_scalars(data)
    names: dict[str, str] = {}
    for section in _SECTIONS:
        for eid, entry in data.get(section, {}).items():
            names[eid] = f"{section}:{entry['Name']}"
    names[data["Process"]["EntityID"]] = f"Process:{data['Process']['Name']}"

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key in ("ID", "CreateTime", "ChangeTime", "EntityID"):
                    continue
                if key == "Scope" and item == "Global":
                    continue
                out[names.get(key, key)] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(v) for v in value]
        return names.get(value, value) if isinstance(value, str) else value

    result = walk(data)
    for key in ("Activities", "Transitions", "TransitionActions"):
        result["Process"][key] = sorted(result["Process"][key])  # id order is not meaningful
    return result  # type: ignore[no-any-return]


# --- tests -----------------------------------------------------------------------------------


async def test_import_creates_new_ids_and_remaps_layout(env: Env) -> None:
    await env.seed_df()
    result = await env.import_(_sample("AgentTicketProcess"))
    assert result.name == "TestProcess"
    assert result.created == {
        "Process": 1,
        "Activity": 3,
        "ActivityDialog": 2,
        "Transition": 3,
        "TransitionAction": 3,
    }
    assert sum(result.updated.values()) == 0
    original = load_znuny_yaml(_sample("AgentTicketProcess"))
    old_ids = {original["Process"]["EntityID"]}
    for section in _SECTIONS:
        old_ids |= set(original[section])
    new_ids = {i for kind in KINDS for i in await env.new_ids(kind)}
    assert len(new_ids) == 12 and not (new_ids & old_ids)
    assert result.process_entity_id in new_ids

    exported = load_znuny_yaml(
        (await export_process_yaml(env.session, result.process_entity_id))[1]
    )
    process = exported["Process"]
    path = process["Config"]["Path"]
    assert set(process["Layout"]) == set(path) == set(exported["Activities"])
    assert process["Config"]["StartActivity"] in path
    for act, transitions in path.items():
        assert act not in old_ids
        for tid, spec in transitions.items():
            assert tid in exported["Transitions"] and spec["ActivityEntityID"] in path
            assert set(spec["TransitionAction"]) <= set(exported["TransitionActions"])
    start = process["Config"]["StartActivityDialog"]
    assert start in exported["ActivityDialogs"]
    assert start in exported["Activities"][process["Config"]["StartActivity"]]["ActivityDialogs"]
    # positions follow their activity (sample: "Start order" sits at left 407)
    start_name = exported["Activities"][process["Config"]["StartActivity"]]["Name"]
    assert start_name == "Start order" and process["Layout"][
        process["Config"]["StartActivity"]
    ] == {
        "left": "407",
        "top": "202",
    }


async def test_import_old_style_ids_get_new_ids(env: Env) -> None:
    result = await env.import_(_sample("Complex1"), overwrite=True)  # ids like ``A12``
    ids = {i for kind in KINDS for i in await env.new_ids(kind)}
    assert ids and all("-" in i and len(i.split("-", 1)[1]) == 32 for i in ids)
    assert result.created["Activity"] == 4 and result.created["ActivityDialog"] == 3


async def test_import_writes_not_sync_rows(env: Env) -> None:
    await env.seed_df()
    result = await env.import_(_sample("AgentTicketProcess"))
    for kind in KINDS:
        ids = await env.new_ids(kind)
        assert ids
        for eid in ids:
            assert await env.sync_state(kind, eid) == "not_sync", (kind, eid)
    rows = await env.session.execute(
        text("SELECT cache_type FROM tiqora_cache_invalidation WHERE id > :m ORDER BY id"),
        {"m": env.signal_mark},
    )
    types = [r[0] for r in rows.all()]
    assert sorted(types) == sorted(f"ProcessManagement_{k}" for k in KINDS)  # once per type
    assert result.process_entity_id in await env.new_ids("Process")


async def test_import_refuses_missing_dynamic_fields(env: Env) -> None:
    counts = {t: await env.count(t) for t in _TABLES.values()}
    with pytest.raises(ImportRefused) as excinfo:
        await env.import_(_sample("AgentTicketProcess"))  # DF is not seeded
    message = str(excinfo.value)
    assert DF_NAME in message and "Dynamischen Felder" in message
    assert {t: await env.count(t) for t in _TABLES.values()} == counts
    rows = await env.session.execute(
        text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE id > :m"),
        {"m": env.signal_mark},
    )
    assert rows.scalar_one() == 0
    # an invalid (not merely absent) field does not count either
    await env.seed_df()
    await env.session.execute(
        text("UPDATE dynamic_field SET valid_id = 2 WHERE id = :i"), {"i": DF_ID}
    )
    await env.session.commit()
    with pytest.raises(ImportRefused):
        await env.import_(_sample("AgentTicketProcess"))


async def test_import_overwrite_updates_in_place(env: Env) -> None:
    await env.seed_df()
    first = await env.import_(_sample("AgentTicketProcess"), overwrite=True)
    original = load_znuny_yaml(_sample("AgentTicketProcess"))
    assert first.process_entity_id == original["Process"]["EntityID"]  # Znuny-format ids kept
    assert first.created["Process"] == 1 and sum(first.updated.values()) == 0
    process_row = (
        await env.session.execute(
            text("SELECT id FROM pm_process WHERE entity_id = :e"), {"e": first.process_entity_id}
        )
    ).scalar_one()
    counts = {t: await env.count(t) for t in _TABLES.values()}

    edited = load_znuny_yaml(_sample("AgentTicketProcess"))
    edited["Process"]["Name"] = "TestProcess edited"
    edited["Process"]["StateEntityID"] = "S2"
    one_activity = next(iter(edited["Activities"].values()))
    one_activity["Name"] = "Renamed step"
    second = await env.import_(dump_znuny_yaml(edited), overwrite=True)
    assert second.process_entity_id == first.process_entity_id
    assert sum(second.created.values()) == 0
    assert second.updated["Process"] == 1 and second.updated["Activity"] == 3
    assert {t: await env.count(t) for t in _TABLES.values()} == counts
    row = (
        await env.session.execute(
            text("SELECT id, name, state_entity_id FROM pm_process WHERE entity_id = :e"),
            {"e": first.process_entity_id},
        )
    ).one()
    assert (row[0], row[1], row[2]) == (process_row, "TestProcess edited", "S2")
    assert (
        await env.session.execute(
            text("SELECT COUNT(*) FROM pm_activity WHERE name = 'Renamed step'")
        )
    ).scalar_one() == 1
    assert await env.sync_state("Process", first.process_entity_id) == "not_sync"

    # without overwrite the same file is a second, independent copy
    third = await env.import_(_sample("AgentTicketProcess"))
    assert third.process_entity_id != first.process_entity_id
    assert await env.count("pm_process") == counts["pm_process"] + 1


@pytest.mark.parametrize("sample", ["AgentTicketProcess", "CustomerTicketProcess", "Complex1"])
async def test_round_trip_parsed_equal(env: Env, sample: str) -> None:
    await env.seed_df()
    result = await env.import_(_sample(sample))
    filename, content = await export_process_yaml(env.session, result.process_entity_id)
    assert filename == f"Export_ProcessEntityID_{result.process_entity_id}.yml"
    assert content.startswith("---\n")
    assert _canonical(load_znuny_yaml(content)) == _canonical(load_znuny_yaml(_sample(sample)))


async def test_export_unknown_process(env: Env) -> None:
    with pytest.raises(EntityNotFound):
        await export_process_yaml(env.session, "Process-" + "0" * 32)


async def test_broken_yaml_is_422(env: Env) -> None:
    for bad in (
        "a: [unclosed",
        "just a string",
        "- a\n- b\n",
        "",
        "Process: 3\n",
        "a: &x 1\nb: *x\n",
    ):
        with pytest.raises(ImportRefused) as excinfo:
            await env.import_(bad)
        assert "nicht gelesen" in str(excinfo.value), bad
    assert not await env.new_ids("Process")

    @asynccontextmanager
    async def client() -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with env.factory() as session:
                yield session

        user = AuthenticatedUser(
            id=USER, login="wf3@example.org", first_name="W", last_name="V", auth_method="session"
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    async with client() as c:
        resp = await c.post(f"{BASE}/import", json={"content": "a: [unclosed"})
        assert resp.status_code == 422 and "nicht gelesen" in resp.json()["detail"]
        huge = await c.post(f"{BASE}/import", json={"content": "x" * 1_000_001})
        assert huge.status_code == 422


async def test_import_refuses_missing_entity_data(env: Env) -> None:
    await env.seed_df()
    broken = load_znuny_yaml(_sample("AgentTicketProcess"))
    victim = broken["Process"]["Transitions"][0]
    del broken["Transitions"][victim]
    with pytest.raises(ImportRefused) as excinfo:
        await env.import_(dump_znuny_yaml(broken))
    assert victim in str(excinfo.value)

    dangling = load_znuny_yaml(_sample("AgentTicketProcess"))
    path = dangling["Process"]["Config"]["Path"]
    first = next(iter(path.values()))
    next(iter(first.values()))["ActivityEntityID"] = "Activity-" + "f" * 32
    with pytest.raises(ImportRefused) as excinfo2:
        await env.import_(dump_znuny_yaml(dangling))
    assert "keine Zuordnung" in str(excinfo2.value)
    assert not await env.new_ids("Process")


async def test_import_entity_cap(env: Env) -> None:
    data = {
        "Process": {"EntityID": "P1", "Name": "cap", "StateEntityID": "S2", "Config": {}},
        "Transitions": {
            f"Transition-{i:032x}": {"Name": f"t{i}", "Config": {}} for i in range(MAX_ENTITIES)
        },
    }
    with pytest.raises(ImportRefused) as excinfo:
        await env.import_(dump_znuny_yaml(data))
    assert "zu groß" in str(excinfo.value)
    assert not await env.new_ids("Transition")


async def test_import_is_atomic(env: Env) -> None:
    await env.seed_df()
    counts = {t: await env.count(t) for t in (*_TABLES.values(), "pm_entity_sync")}
    doomed = load_znuny_yaml(_sample("AgentTicketProcess"))
    doomed["Process"]["Name"] = "x" * 201  # the process is written last
    with pytest.raises(InvalidReference):
        await env.import_(dump_znuny_yaml(doomed))
    assert {t: await env.count(t) for t in counts} == counts
    rows = await env.session.execute(
        text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE id > :m"),
        {"m": env.signal_mark},
    )
    assert rows.scalar_one() == 0
    # and the same session is usable afterwards
    ok = await env.import_(_sample("AgentTicketProcess"))
    assert ok.process_entity_id in await env.new_ids("Process")


async def test_import_export_routes(env: Env) -> None:
    await env.seed_df()

    @asynccontextmanager
    async def client(user_id: int) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with env.factory() as session:
                yield session

        user = AuthenticatedUser(
            id=user_id,
            login="wf3@example.org",
            first_name="W",
            last_name="V",
            auth_method="session",
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    async with client(NON_ADMIN) as c:
        assert (
            await c.post(f"{BASE}/import", json={"content": _sample("Complex1")})
        ).status_code == 403
    async with client(USER) as c:
        resp = await c.post(
            f"{BASE}/import",
            json={"content": _sample("AgentTicketProcess"), "overwrite_existing": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        pid = body["process_entity_id"]
        assert body["name"] == "TestProcess" and body["created"]["Activity"] == 3
        listed = (await c.get(f"{BASE}/processes")).json()
        mine = next(p for p in listed if p["entity_id"] == pid)
        assert mine["sync_state"] == "not_sync"  # drafts are listed as drafts

        export = await c.get(f"{BASE}/processes/{pid}/export")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/x-yaml")
        assert (
            export.headers["content-disposition"]
            == f'attachment; filename="Export_ProcessEntityID_{pid}.yml"'
        )
        assert load_znuny_yaml(export.text)["Process"]["EntityID"] == pid
        assert (await c.get(f"{BASE}/processes/Process-{'0' * 32}/export")).status_code == 404
    async with client(NON_ADMIN) as c:
        assert (await c.get(f"{BASE}/processes/{pid}/export")).status_code == 403
