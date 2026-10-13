"""Import pipeline (Task 12; band 56260-56299): step order, delegated steps, change counts, deleted
CIs, incident states and a run on the real adapter.

The FakeSource runs use the main test databases (``world``, rows are committed and wiped); the
real-adapter runs use the ITSM databases of ``_itsm_db`` inside one outer transaction that every
step session joins with a savepoint, so nothing is left behind.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_db import to_async_url as itsm_async_url
from tests._itsm_fixtures import seed_ci, seed_ci_link
from tests._itsm_import_fixtures import (
    GC_INCIDENT,
    FakeSource,
    cleanup_cmdb_rows,
    sample_source,
)
from tests._itsm_mode_helpers import profile_packages
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi, TiqoraItsmImportMap
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.facades import ItsmModeFacade
from tiqora.itsm.importer.pipeline import STEP_ORDER, ImportBlocked, run_import
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.models import TiqoraItsmLinkType, TiqoraItsmPriorityMatrix, TiqoraItsmServiceMeta
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.itsm.znuny_adapter.reader import CipRow, ServiceItsmRow

pytestmark = pytest.mark.db

SYSCONFIG_BASE = 56260
SYSCONFIG: dict[str, Any] = {
    "LinkObject::Type###DependsOn": {"SourceName": "Depends on", "TargetName": "Required for"},
    "LinkObject::Type###RelevantTo": {"SourceName": "Relevant to", "TargetName": "Relevant to"},
    "LinkObject::PossibleLink###0300": {
        "Object1": "ITSMConfigItem",
        "Object2": "ITSMConfigItem",
        "Type": "DependsOn",
    },
    "LinkObject::PossibleLink###0301": {
        "Object1": "Ticket",
        "Object2": "ITSMConfigItem",
        "Type": "RelevantTo",
    },
    "ITSM::Core::IncidentLinkTypeDirection": {"DependsOn": "Both"},
}
SERVICES = (56270, 56271, 56272)
RUN_IDS = iter(range(56260, 56299))

CIP = [CipRow("3 normal", "3 normal", 3), CipRow("4 high", "4 high", 2)]
SERVICE_ROWS = [
    ServiceItsmRow(56270, 5, "Hardware", "3 normal"),
    ServiceItsmRow(56271, None, None, None),
    ServiceItsmRow(56272, 6, "Software", None),
]


async def _noop(*_args: Any) -> None:
    return None


@dataclass
class _World:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


def _wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tiqora_cmdb_class SET current_definition_id = NULL"
                    " WHERE id IN (SELECT target_id FROM tiqora_itsm_import_map"
                    " WHERE source_kind = 'class' AND run_id BETWEEN 56070 AND 56299)"
                )
            )
        cleanup_cmdb_rows(sync_url)
        with engine.begin() as conn:
            conn.execute(delete(TiqoraItsmLinkType.__table__))
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog"))
            conn.execute(text("DELETE FROM tiqora_itsm_import_map WHERE run_id >= 56070"))
            conn.execute(delete(TiqoraItsmPriorityMatrix.__table__))
            conn.execute(
                delete(TiqoraItsmServiceMeta.__table__).where(
                    TiqoraItsmServiceMeta.service_id.in_(SERVICES)
                )
            )
            conn.execute(text("DELETE FROM service WHERE id BETWEEN 56270 AND 56272"))
            for table in ("sysconfig_modified", "sysconfig_default"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE id BETWEEN :lo AND :hi"),
                    {"lo": SYSCONFIG_BASE, "hi": SYSCONFIG_BASE + 9},
                )
    finally:
        engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    _wipe(sync_url)
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            set_sysconfig_rows(conn, SYSCONFIG_BASE, SYSCONFIG)
            for sid in SERVICES:
                conn.execute(
                    text(
                        "INSERT INTO service (id, name, valid_id, create_time, create_by,"
                        " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"i": sid, "n": f"itim56-service-{sid}", "t": "2026-01-01 08:00:00"},
                )
    finally:
        engine.dispose()
    async_engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _World(
            async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False), sync_url
        )
    finally:
        _wipe(sync_url)


def _source(**knobs: Any) -> FakeSource:
    src = sample_source(**knobs)
    src.cip = list(CIP)
    src.services = list(SERVICE_ROWS)
    return src


async def _run(
    w: _World,
    src: FakeSource,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dry_run: bool = False,
    check_cancelled: Any = _noop,
) -> ImportReport:
    monkeypatch.setattr(facades, "source_override", src)
    return await run_import(
        w.factory,
        run_id=next(RUN_IDS),
        user_id=56261,
        dry_run=dry_run,
        page_size=4,
        progress=_noop,
        check_cancelled=check_cancelled,
    )


def _order(report: ImportReport) -> list[str]:
    # the classes step also counts under "definitions" (right after "classes"); see pipeline.py
    assert list(report.steps)[1:3] == ["classes", "definitions"]
    return [s for s in report.steps if s != "definitions"]


async def _scalar(w: _World, stmt: Any) -> Any:
    async with w.factory() as s:
        return (await s.execute(stmt)).scalar_one()


async def _ci_count(w: _World) -> int:
    return int(await _scalar(w, select(func.count()).select_from(TiqoraCmdbCi)))


# --- the pipeline on a fake source ----------------------------------------------------------


async def test_full_pipeline_with_fake_source(world: _World, monkeypatch) -> None:
    report = await _run(world, _source(), monkeypatch)

    assert _order(report) == list(STEP_ORDER)
    assert report.steps["changes"].skipped == 8  # 3 changes + 5 work orders, counted only
    codes = {i.code for i in report.issues}
    assert codes >= {"unsupported_object", "attachment_in_filesystem", "changes_not_imported"}
    assert {i.step for i in report.issues if i.code == "changes_not_imported"} == {"changes"}
    assert report.steps["cis_pass2"].created == 10  # per CI: 10 CIs, 17 versions
    assert report.steps["links"].created == 4
    assert await _ci_count(world) == 10

    # delegated steps: matrix cells and the service meta landed in Tiqora's tables
    assert report.steps["priority_matrix"].created == 2
    async with world.factory() as s:
        cells = {
            (c.criticality, c.impact, c.priority_id)
            for c in (await s.execute(select(TiqoraItsmPriorityMatrix))).scalars()
        }
        assert cells == {("3 normal", "3 normal", 3), ("4 high", "4 high", 2)}
        meta = {m.service_id: m for m in (await s.execute(select(TiqoraItsmServiceMeta))).scalars()}
    assert (meta[56270].service_type, meta[56270].criticality) == ("Hardware", "3 normal")
    assert (meta[56272].service_type, meta[56272].criticality) == ("Software", None)
    assert 56271 not in meta  # nothing ITSM about it
    sm = report.steps["service_meta"]
    assert (sm.created, sm.skipped, sm.problems) == (2, 1, 0)


async def test_second_run_changes_nothing(world: _World, monkeypatch) -> None:
    src = _source()
    await _run(world, src, monkeypatch)
    again = await _run(world, src, monkeypatch)

    assert _order(again) == list(STEP_ORDER)
    assert again.steps["cis_pass2"].unchanged == 10 and again.steps["cis_pass2"].created == 0
    assert again.steps["priority_matrix"].unchanged == 2
    assert again.steps["priority_matrix"].created == again.steps["priority_matrix"].updated == 0
    assert again.steps["service_meta"].unchanged == 2
    assert again.steps["incident_states"].updated == 0  # nothing new was touched
    assert await _ci_count(world) == 10


async def test_dry_run_writes_nothing(world: _World, monkeypatch) -> None:
    report = await _run(world, _source(), monkeypatch, dry_run=True)

    assert _order(report) == list(STEP_ORDER)
    assert report.steps["cis_pass2"].created == 10  # counted like a real run
    assert report.steps["priority_matrix"].skipped == 2  # dry run only counts the source rows
    assert report.steps["service_meta"].skipped == 3
    assert await _ci_count(world) == 0
    assert await _scalar(world, select(func.count()).select_from(TiqoraItsmPriorityMatrix)) == 0
    assert await _scalar(world, select(func.count()).select_from(TiqoraItsmServiceMeta)) == 0
    assert await _scalar(world, select(func.count()).select_from(TiqoraItsmImportMap)) == 0


def _no_profile() -> FakeSource:
    return FakeSource(profile=None)


async def test_blocked_without_supported_itsm(world: _World, monkeypatch) -> None:
    with pytest.raises(ImportBlocked, match="kein ITSM"):
        await _run(world, _no_profile(), monkeypatch)
    assert await _ci_count(world) == 0


async def test_itsm_core_only_imports_catalog_matrix_and_services(
    world: _World, monkeypatch
) -> None:
    report = await _run(world, _source(cmdb_installed=False), monkeypatch)

    assert _order(report) == list(STEP_ORDER)
    assert report.steps["catalog"].created > 0
    for step in ("classes", "cis_pass1", "cis_pass2", "links", "attachments"):
        counts = report.steps[step]
        assert (counts.created, counts.updated, counts.unchanged, counts.skipped) == (0, 0, 0, 0)
    assert report.steps["priority_matrix"].created == 2
    assert report.steps["service_meta"].created == 2
    assert await _ci_count(world) == 0


async def test_cancel_between_steps_stops_the_run(world: _World, monkeypatch) -> None:
    calls = 0

    async def cancel_after_catalog() -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        await _run(world, _source(), monkeypatch, check_cancelled=cancel_after_catalog)
    assert await _scalar(world, select(func.count()).select_from(TiqoraItsmPriorityMatrix)) == 0


async def test_invalid_matrix_is_reported_not_fatal(world: _World, monkeypatch) -> None:
    src = _source()
    src.cip = [CipRow("3 normal", "3 normal", 987654)]
    report = await _run(world, src, monkeypatch)

    assert [i.code for i in report.issues if i.step == "priority_matrix"] == [
        "priority_matrix_invalid"
    ]
    assert report.steps["priority_matrix"].problems == 1
    assert await _scalar(world, select(func.count()).select_from(TiqoraItsmPriorityMatrix)) == 0
    assert report.steps["service_meta"].created == 2  # the run went on


async def test_unknown_service_is_reported(world: _World, monkeypatch) -> None:
    src = _source()
    src.services = [ServiceItsmRow(56279, 5, "Hardware", "3 normal"), SERVICE_ROWS[0]]
    report = await _run(world, src, monkeypatch)

    issues = [i for i in report.issues if i.step == "service_meta"]
    assert [(i.code, i.object_ref) for i in issues] == [("service_unknown", "service:56279")]
    assert report.steps["service_meta"].created == 1
    assert report.steps["service_meta"].problems == 1


async def test_existing_service_meta_keeps_tiqora_fields(world: _World, monkeypatch) -> None:
    async with world.factory() as s:
        s.add(
            TiqoraItsmServiceMeta(
                service_id=56270,
                owner_user_id=1,
                description="Rack 4",
                portal_visible=False,
                min_minutes_between_incidents=30,
                change_time=datetime(2026, 1, 1),
                change_by=1,
            )
        )
        await s.commit()
    await _run(world, _source(), monkeypatch)

    async with world.factory() as s:
        row = await s.get(TiqoraItsmServiceMeta, 56270)
        assert row is not None
        assert (row.service_type, row.criticality) == ("Hardware", "3 normal")
        assert (row.owner_user_id, row.description) == (1, "Rack 4")
        assert (row.portal_visible, row.min_minutes_between_incidents) == (False, 30)


async def test_deleted_in_znuny_reported_not_deleted(world: _World, monkeypatch) -> None:
    src = _source()
    await _run(world, src, monkeypatch)
    victim = src.ci_rows[2]
    src.remove_ci(victim.id)
    again = await _run(world, src, monkeypatch)

    issues = [i for i in again.issues if i.code == "deleted_in_znuny"]
    assert [(i.step, i.object_ref) for i in issues] == [("deleted_in_znuny", victim.number)]
    assert str(victim.id) in issues[0].message
    assert again.steps["deleted_in_znuny"].problems == 1
    assert again.steps["deleted_in_znuny"].unchanged == 9
    assert await _ci_count(world) == 10  # still there
    async with world.factory() as s:
        kept = (
            await s.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == victim.number))
        ).scalar_one_or_none()
    assert kept is not None


async def test_incident_states_recomputed_for_touched_cis(world: _World, monkeypatch) -> None:
    src = _source()
    last = src.version_rows[5][-1]
    src.version_rows[5][-1] = dataclasses.replace(last, incident_state_id=GC_INCIDENT)
    src.ci_rows = [
        dataclasses.replace(c, incident_state_id=GC_INCIDENT) if c.id == 5 else c
        for c in src.ci_rows
    ]
    report = await _run(world, src, monkeypatch)

    # CI 4 DependsOn CI 5 (propagates both ways): the incident on 5 makes 4 a Warning. The own
    # state of CI 4 stays Operational, the derived one (cur_incident_state_id) is what changes.
    numbers = {i: next(c.number for c in src.ci_rows if c.id == i) for i in (4, 5, 8)}
    async with world.factory() as s:
        rows = (
            await s.execute(
                select(TiqoraCmdbCi.number, TiqoraCmdbCi.incident_state_id, TiqoraCmdbCatalog.name)
                .join(TiqoraCmdbCatalog, TiqoraCmdbCatalog.id == TiqoraCmdbCi.cur_incident_state_id)
                .where(TiqoraCmdbCi.number.in_(numbers.values()))
            )
        ).all()
        own = {
            n: name
            for n, name in (
                await s.execute(
                    select(TiqoraCmdbCi.number, TiqoraCmdbCatalog.name)
                    .join(TiqoraCmdbCatalog, TiqoraCmdbCatalog.id == TiqoraCmdbCi.incident_state_id)
                    .where(TiqoraCmdbCi.number.in_(numbers.values()))
                )
            ).all()
        }
    derived = {n: name for n, _own_id, name in rows}
    assert derived == {
        numbers[4]: "Warning",
        numbers[5]: "Incident",
        numbers[8]: "Operational",
    }
    assert own[numbers[4]] == "Operational"
    inc = report.steps["incident_states"]
    assert inc.updated >= 1 and inc.updated + inc.unchanged == 10


async def test_znuny_present_override_is_the_only_mode_hook(monkeypatch) -> None:
    monkeypatch.setattr(facades, "znuny_present_override", False)
    assert await ItsmModeFacade(None).znuny_itsm_present() is False  # type: ignore[arg-type]
    monkeypatch.setattr(facades, "znuny_present_override", True)
    assert await ItsmModeFacade(None).znuny_itsm_present() is True  # type: ignore[arg-type]


# --- the real adapter on Znuny 6.x ITSM tables ----------------------------------------------


@pytest.fixture(params=["mysql", "postgresql"])
async def itsm_world(request: pytest.FixtureRequest) -> AsyncIterator[async_sessionmaker[Any]]:
    """Session factory whose sessions share one outer transaction (rolled back at the end)."""
    url = request.getfixturevalue("itsm_db_url")(request.param, "itsm-6")
    engine = create_async_engine(itsm_async_url(url), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                yield async_sessionmaker(
                    conn,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


async def test_real_adapter_on_znuny_6x_tables(itsm_world, monkeypatch) -> None:
    monkeypatch.setattr(facades, "source_override", None)  # the real ZnunyAdapterSource
    clear_detect_cache()
    async with itsm_world() as s:
        for name, version in profile_packages("itsm-6").items():  # what detection reads
            await s.execute(
                text(
                    "INSERT INTO package_repository (name, version, vendor, install_status,"
                    " content, create_time, create_by, change_time, change_by)"
                    " VALUES (:n, :v, 'Znuny GmbH', 'installed', '',"
                    " '2000-01-01 00:00:00', 1, '2000-01-01 00:00:00', 1)"
                ),
                {"n": name, "v": version},
            )
        a = await seed_ci(
            s,
            "itsm-6",
            number="5626001",
            class_name="Computer",
            name="itim56 real a",
            versions=[{"name": "itim56 real a1"}, {"name": "itim56 real a2"}],
        )
        b = await seed_ci(s, "itsm-6", number="5626002", class_name="Network", name="itim56 b")
        await seed_ci(s, "itsm-6", number="5626003", class_name="Computer", name="itim56 c")
        await seed_ci_link(s, source_key=a, target_key=b)
        await s.commit()  # releases the savepoint only: the outer transaction keeps everything
    run_id = next(RUN_IDS)

    report = await run_import(
        itsm_world,
        run_id=run_id,
        user_id=1,
        dry_run=False,
        page_size=2,
        progress=_noop,
        check_cancelled=_noop,
    )

    assert _order(report) == list(STEP_ORDER)
    assert report.steps["cis_pass1"].created == 3
    assert report.steps["cis_pass2"].created == 3  # counted per CI; its 4 versions are below
    assert report.steps["links"].created == 1
    assert report.steps["changes"].skipped == 0  # no Change Management data in the seed
    async with itsm_world() as s:
        numbers = set(
            (
                await s.execute(
                    select(TiqoraCmdbCi.number).where(
                        TiqoraCmdbCi.number.in_(["5626001", "5626002", "5626003"])
                    )
                )
            ).scalars()
        )
        assert numbers == {"5626001", "5626002", "5626003"}
        versions = await s.execute(
            select(func.count())
            .select_from(TiqoraItsmImportMap)
            .where(
                TiqoraItsmImportMap.source_kind == "version", TiqoraItsmImportMap.run_id == run_id
            )
        )
        assert versions.scalar_one() == 4  # 2 + 1 + 1 versions
