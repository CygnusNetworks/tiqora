"""Revision list, diff and restore-as-draft (plan D14, Task 16).

Seed band 896400-896799: the non-admin user id is 896401. Entities use fixed literal ids
(``Process-wf3…`` etc.); the fixture removes every ``pm_*`` row, sync row, revision and cache
signal that appeared during a test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraCacheInvalidation,
    TiqoraProcessRevision,
    TiqoraSettings,
)
from tiqora.deployed.store import clear_revision_cache, record_revision
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.designer.revisions import (
    DUMP_KINDS,
    diff_revisions,
    dump_entry_to_config,
    process_references,
    restore_process_from_revision,
)
from tiqora.process.designer.writer import EntityNotFound, ProcessWriter
from tiqora.process.dump import build_process_dump, dump_checksum

BASE = "/api/v1/admin/process-designer"
NON_ADMIN = 896401
USER = 1
HEX = "0123456789abcdef" * 2
PID = "Process-wf3" + "a" * 29
OTHER_PID = "Process-wf3" + "b" * 29
ACT1, ACT2 = "Activity-wf3" + "1" * 28, "Activity-wf3" + "2" * 28
DLG = "ActivityDialog-wf3" + "3" * 22
TRN = "Transition-wf3" + "4" * 26
ACN = "TransitionAction-wf3" + "5" * 20
EXTRA_ACT = "Activity-wf3" + "6" * 28
_TABLES = {
    "Process": "pm_process",
    "Activity": "pm_activity",
    "ActivityDialog": "pm_activity_dialog",
    "Transition": "pm_transition",
    "TransitionAction": "pm_transition_action",
}
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _purge(
    sync_url: str, before: dict[str, set[str]] | None, marks: tuple[int, int] | None
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for kind, table in _TABLES.items():
            known = before[kind] if before is not None else set()
            for eid in set(conn.execute(text(f"SELECT entity_id FROM {table}")).scalars()):
                if eid not in known and (before is not None or "wf3" in eid):
                    conn.execute(text(f"DELETE FROM {table} WHERE entity_id = :e"), {"e": eid})
                    conn.execute(
                        text("DELETE FROM pm_entity_sync WHERE entity_id = :e"), {"e": eid}
                    )
        if marks is not None:
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :m"), {"m": marks[0]}
            )
            conn.execute(text("DELETE FROM tiqora_process_revision WHERE id > :m"), {"m": marks[1]})
    engine.dispose()


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    session: AsyncSession
    before: dict[str, set[str]]

    def writer(self) -> ProcessWriter:
        return ProcessWriter(self.session, user_id=USER)

    async def seed(self, *, dialog_text: str = "Bestellung") -> None:
        w = self.writer()
        await w.import_entity(
            "ActivityDialog",
            DLG,
            name="Dialog",
            config={
                "Interface": ["AgentInterface"],
                "DescriptionShort": dialog_text,
                "Fields": {"Queue": {"Display": "1"}},
                "FieldOrder": ["Queue"],
            },
        )
        for aid, name in ((ACT1, "Start"), (ACT2, "End")):
            await w.import_entity("Activity", aid, name=name, config={"ActivityDialog": {"1": DLG}})
        await w.import_entity(
            "Transition",
            TRN,
            name="Go",
            config={"Condition": {"1": {"Type": "and"}}, "ConditionLinking": "and"},
        )
        await w.import_entity(
            "TransitionAction",
            ACN,
            name="Move",
            config={
                "Module": "Kernel::System::ProcessManagement::TransitionAction::X",
                "Config": {},
            },
        )
        await w.import_entity(
            "Process",
            PID,
            name="Wf3 process",
            config={
                "Description": "d",
                "StartActivity": ACT1,
                "StartActivityDialog": DLG,
                "Path": {
                    ACT1: {TRN: {"ActivityEntityID": ACT2, "TransitionAction": [ACN]}},
                    ACT2: {},
                },
            },
            layout={ACT1: {"left": "10", "top": "20"}, ACT2: {"left": "30", "top": "40"}},
            state_entity_id="S2",
        )
        await self.session.commit()

    async def deploy(self) -> int:
        info = await record_revision(
            self.session,
            "process",
            dump=await build_process_dump(self.session),
            source="tiqora",
            deployed_by=USER,
        )
        await self.session.commit()
        return info.id

    async def sync_state(self, kind: str, eid: str) -> str | None:
        row = (
            await self.session.execute(
                text("SELECT sync_state FROM pm_entity_sync WHERE entity_type=:t AND entity_id=:e"),
                {"t": kind, "e": eid},
            )
        ).first()
        return None if row is None else str(row[0])

    async def exists(self, kind: str, eid: str) -> bool:
        row = await self.session.execute(
            text(f"SELECT 1 FROM {_TABLES[kind]} WHERE entity_id = :e"), {"e": eid}
        )
        return row.first() is not None

    @asynccontextmanager
    async def client(self, user_id: int = USER) -> AsyncIterator[AsyncClient]:
        async def _db() -> Any:
            async with self.factory() as session:
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


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        sync_engine,
        tables=[  # type: ignore[list-item]
            TiqoraCacheInvalidation.__table__,
            TiqoraSettings.__table__,
            TiqoraProcessRevision.__table__,
        ],
        checkfirst=True,
    )
    with sync_engine.connect() as conn:
        before = {
            kind: set(conn.execute(text(f"SELECT entity_id FROM {table}")).scalars())
            for kind, table in _TABLES.items()
        }
        marks = (
            int(
                conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                ).scalar_one()
            ),
            int(
                conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_process_revision")
                ).scalar_one()
            ),
        )
    sync_engine.dispose()
    _purge(sync_url, None, None)  # leftovers of a crashed run
    clear_revision_cache()
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield Env(factory, session, before)
        await session.rollback()
    await engine.dispose()
    _purge(sync_url, before, marks)
    clear_revision_cache()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        _purge(sync_url, None, None)


def _dump(**overrides: Any) -> dict[str, Any]:
    """A small hand-written dump: one process P1 (A1 -> dialog D1, transition T1, action X1)
    and an unrelated process P2 (A2 -> D2)."""
    dump: dict[str, Any] = {
        "Process": {
            "P1": {
                "ID": 1,
                "Name": "One",
                "CreateTime": "2026-01-01 00:00:00",
                "ChangeTime": "2026-01-01 00:00:00",
                "StartActivity": "A1",
                "StartActivityDialog": "D1",
                "Path": {"A1": {"T1": {"ActivityEntityID": "A1", "TransitionAction": ["X1"]}}},
            },
            "P2": {"ID": 2, "Name": "Two", "StartActivity": "A2", "Path": {"A2": {}}},
        },
        "Activity": {
            "A1": {"ID": 1, "Name": "a1", "ActivityDialog": {"1": "D1"}},
            "A2": {"ID": 2, "Name": "a2", "ActivityDialog": {"1": "D2"}},
        },
        "ActivityDialog": {
            "D1": {"ID": 1, "Name": "d1", "DescriptionShort": "x"},
            "D2": {"ID": 2, "Name": "d2", "DescriptionShort": "y"},
        },
        "Transition": {"T1": {"ID": 1, "Name": "t1", "ConditionLinking": "and"}},
        "TransitionAction": {"X1": {"ID": 1, "Name": "x1", "Module": "M"}},
        "State": {"S1": "Active"},
        "ProcessPreferences": {},
    }
    dump.update(overrides)
    return dump


def test_diff_added_removed_changed_ignores_times() -> None:
    old = _dump()
    new = deepcopy(old)
    # time and id noise only: no change
    new["Process"]["P1"]["ChangeTime"] = "2027-02-02 02:02:02"
    new["Activity"]["A1"]["ID"] = 99
    assert diff_revisions(old, new, process_entity_id=None).changes == []

    new["ActivityDialog"]["D1"]["DescriptionShort"] = "changed"
    new["ActivityDialog"]["D1"]["Fields"] = {}
    del new["Transition"]["T1"]
    new["TransitionAction"]["X2"] = {"ID": 5, "Name": "x2", "Module": "M2"}
    result = diff_revisions(old, new, process_entity_id=None)
    by_id = {c.entity_id: c for c in result.changes}
    assert set(by_id) == {"D1", "T1", "X2"}
    assert (by_id["D1"].change, by_id["D1"].changed_keys) == (
        "changed",
        ["DescriptionShort", "Fields"],
    )
    assert (by_id["T1"].change, by_id["T1"].name) == ("removed", "t1")
    assert (by_id["X2"].change, by_id["X2"].entity_type) == ("added", "TransitionAction")
    assert result.process_entity_id is None
    assert "changed" in result.yaml_after and "changed" not in result.yaml_before
    assert "ChangeTime" not in result.yaml_before and "CreateTime" not in result.yaml_after


def test_diff_filtered_to_one_process_includes_referenced_entities() -> None:
    old = _dump()
    new = deepcopy(old)
    new["ActivityDialog"]["D1"]["DescriptionShort"] = "p1 only"
    new["ActivityDialog"]["D2"]["DescriptionShort"] = "p2 only"
    new["TransitionAction"]["X1"]["Module"] = "M-new"
    removed_in_new = deepcopy(old)
    del removed_in_new["Transition"]["T1"]  # referenced by P1's Path in the OLD dump only
    result = diff_revisions(old, new, process_entity_id="P1")
    assert {c.entity_id for c in result.changes} == {"D1", "X1"}
    assert "p2 only" not in result.yaml_after and "p1 only" in result.yaml_after
    assert "Two" not in result.yaml_before
    both = diff_revisions(old, removed_in_new, process_entity_id="P1")
    assert [(c.entity_id, c.change) for c in both.changes] == [("T1", "removed")]
    assert diff_revisions(old, new, process_entity_id="P-missing").changes == []
    refs = process_references(old, "P1")
    assert refs["Activity"] == {"A1"} and refs["ActivityDialog"] == {"D1"}
    assert refs["Transition"] == {"T1"} and refs["TransitionAction"] == {"X1"}


def test_dump_entry_to_config_inverts_every_kind() -> None:
    dump = _dump()
    assert (
        dump_entry_to_config("Process", dump["Process"]["P1"])["Path"]
        == (dump["Process"]["P1"]["Path"])
    )
    assert dump_entry_to_config("Activity", dump["Activity"]["A1"]) == {
        "ActivityDialog": {"1": "D1"}
    }
    assert dump_entry_to_config("ActivityDialog", dump["ActivityDialog"]["D1"]) == {
        "DescriptionShort": "x"
    }
    assert dump_entry_to_config("Transition", dump["Transition"]["T1"])["ConditionLinking"] == "and"
    assert dump_entry_to_config("TransitionAction", dump["TransitionAction"]["X1"]) == {
        "Module": "M",
        "Config": {},
    }
    assert set(DUMP_KINDS) == {
        "Process",
        "Activity",
        "ActivityDialog",
        "Transition",
        "TransitionAction",
    }
    with pytest.raises(ValueError):
        dump_entry_to_config("Nope", {})


@pytest.mark.db
async def test_restore_writes_drafts_with_same_ids_and_never_deletes(env: Env) -> None:
    await env.seed(dialog_text="Bestellung")
    revision_id = await env.deploy()
    # drift after the deployment: new text, a new activity that the restore must not delete,
    # and a new canvas position for an existing and a new activity
    w = env.writer()
    await w.import_entity(
        "ActivityDialog",
        DLG,
        name="Dialog",
        config={"Interface": ["AgentInterface"], "DescriptionShort": "drifted"},
    )
    await w.import_entity("Activity", EXTRA_ACT, name="Extra", config={"ActivityDialog": {}})
    await env.session.execute(
        text("UPDATE pm_process SET layout = :l WHERE entity_id = :e"),
        {
            "l": "---\n"
            + f"{ACT1}:\n  left: '77'\n  top: '88'\n{EXTRA_ACT}:\n  left: '1'\n  top: '2'\n",
            "e": PID,
        },
    )
    await env.session.execute(text("DELETE FROM pm_entity_sync"))  # all clean before the restore
    await env.session.commit()

    async with env.client() as c:
        resp = await c.post(
            f"{BASE}/revisions/{revision_id}/restore", json={"process_entity_id": PID}
        )
        assert resp.status_code == 200, resp.text
        draft = resp.json()
        again = await c.post(
            f"{BASE}/revisions/{revision_id}/restore", json={"process_entity_id": PID}
        )
        assert again.status_code == 200  # restoring twice is idempotent
    assert draft["process"]["entity_id"] == PID and draft["process"]["name"] == "Wf3 process"
    assert draft["layout"][ACT1]["left"] == "77" and EXTRA_ACT not in draft["layout"]
    row = (
        await env.session.execute(
            text("SELECT config FROM pm_activity_dialog WHERE entity_id = :e"), {"e": DLG}
        )
    ).scalar_one()
    assert "Bestellung" in str(row) and "drifted" not in str(row)
    assert await env.exists("Activity", EXTRA_ACT)  # never deletes
    assert await env.sync_state("ActivityDialog", DLG) == "not_sync"  # changed -> draft
    assert await env.sync_state("Process", PID) == "not_sync"  # layout lost the extra activity
    # rows that already equal the revision are not written ("no change, no write")
    for kind, eid in (("Activity", ACT1), ("Transition", TRN)):
        assert await env.sync_state(kind, eid) is None
    assert await env.sync_state("Activity", EXTRA_ACT) is None  # untouched
    layout = (
        await env.session.execute(
            text("SELECT layout FROM pm_process WHERE entity_id = :e"), {"e": PID}
        )
    ).scalar_one()
    assert "'77'" in str(layout) and EXTRA_ACT not in str(layout)  # kept, extra id not in revision


@pytest.mark.db
async def test_restore_reproduces_revision_checksum(env: Env) -> None:
    await env.seed()
    revision_id = await env.deploy()
    w = env.writer()
    await w.import_entity(
        "Transition", TRN, name="Go renamed", config={"Condition": {}, "ConditionLinking": "or"}
    )
    await w.import_entity(
        "ActivityDialog",
        DLG,
        name="Dialog",
        config={"Interface": ["CustomerInterface"], "Fields": {}},
    )
    await env.session.commit()
    await restore_process_from_revision(
        env.session, env.writer(), revision_id=revision_id, process_entity_id=PID
    )
    await env.session.commit()

    def reduce(dump: dict[str, Any]) -> dict[str, Any]:
        refs = process_references(dump, PID)
        out: dict[str, Any] = {}
        for kind in DUMP_KINDS:
            out[kind] = {
                eid: {
                    k: v
                    for k, v in dump[kind][eid].items()
                    if k not in ("ID", "CreateTime", "ChangeTime")
                }
                for eid in sorted(refs[kind])
            }
        return out

    from tiqora.deployed.store import load_revision

    loaded = await load_revision(env.session, "process", revision_id)
    assert loaded is not None
    now = await build_process_dump(env.session)
    assert dump_checksum(reduce(now)) == dump_checksum(reduce(dict(loaded.dump)))


@pytest.mark.db
async def test_restore_unknown_process_in_revision_is_404(env: Env) -> None:
    await env.seed()
    revision_id = await env.deploy()
    with pytest.raises(EntityNotFound):
        await restore_process_from_revision(
            env.session, env.writer(), revision_id=revision_id, process_entity_id=OTHER_PID
        )
    async with env.client() as c:
        unknown = await c.post(
            f"{BASE}/revisions/{revision_id}/restore", json={"process_entity_id": OTHER_PID}
        )
        assert unknown.status_code == 404
        missing = await c.post(
            f"{BASE}/revisions/{revision_id + 1000}/restore", json={"process_entity_id": PID}
        )
        assert missing.status_code == 404
    assert not await env.exists("Process", OTHER_PID)
    async with env.client(NON_ADMIN) as c:
        forbidden = await c.post(
            f"{BASE}/revisions/{revision_id}/restore", json={"process_entity_id": PID}
        )
        assert forbidden.status_code in (401, 403)


@pytest.mark.db
async def test_revisions_list_newest_first_limit(env: Env) -> None:
    await env.seed()
    first = await env.deploy()
    await env.writer().import_entity(
        "ActivityDialog",
        DLG,
        name="Dialog",
        config={"Interface": ["AgentInterface"], "DescriptionShort": "v2"},
    )
    await env.session.commit()
    second = await env.deploy()
    third = await env.deploy()
    async with env.client() as c:
        listed = await c.get(f"{BASE}/revisions", params={"kind": "process", "limit": 2})
        assert listed.status_code == 200
        ids = [r["id"] for r in listed.json()]
        assert ids == [third, second]
        assert set(listed.json()[0]) == {
            "id",
            "source",
            "entity_count",
            "deployed_at",
            "deployed_by",
        }
        assert (await c.get(f"{BASE}/revisions", params={"limit": 0})).status_code == 422
        # default `against` is the previous revision
        diff = await c.get(f"{BASE}/revisions/{second}/diff", params={"process": PID})
        assert diff.status_code == 200
        body = diff.json()
        assert (body["from_revision"], body["to_revision"]) == (first, second)
        assert [(c_["entity_id"], c_["change"]) for c_ in body["changes"]] == [(DLG, "changed")]
        same = (await c.get(f"{BASE}/revisions/{third}/diff", params={"against": second})).json()
        assert same["changes"] == []
        empty = (await c.get(f"{BASE}/revisions/{first}/diff", params={"process": PID})).json()
        assert empty["from_revision"] == 0 and {x["change"] for x in empty["changes"]} == {"added"}
        assert (await c.get(f"{BASE}/revisions/{third + 1000}/diff")).status_code == 404
