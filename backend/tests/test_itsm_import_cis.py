"""Importer steps ``cis_pass1`` / ``cis_pass2``: CIs and all their versions (band 56120-56199)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import (
    CI_NUMBER_BASE,
    DEFINITION_COMPUTER_V2,
    GC_NETWORK,
    T0,
    FakeSource,
    cleanup_cmdb_rows,
    make_ctx,
    sample_source,
)
from tiqora.cmdb import importer_api
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbCiVersion, TiqoraItsmImportMap
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.catalog import run_catalog
from tiqora.itsm.importer.steps.cis import MapResolver, run_cis_pass1, run_cis_pass2
from tiqora.itsm.importer.steps.classes import ClassMaps, run_classes

pytestmark = pytest.mark.db


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
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog"))
            conn.execute(text("DELETE FROM tiqora_itsm_import_map WHERE run_id >= 56070"))
    finally:
        engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    _wipe(sync_url)
    engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _World(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), sync_url
        )
    finally:
        _wipe(sync_url)


@dataclass(frozen=True)
class _Counts:
    cis: int
    versions: int
    mapped: int


ZERO = _Counts(0, 0, 0)


async def _counts(w: _World) -> _Counts:
    async with w.factory() as s:
        cis = (await s.execute(select(func.count()).select_from(TiqoraCmdbCi))).scalar_one()
        versions = (
            await s.execute(select(func.count()).select_from(TiqoraCmdbCiVersion))
        ).scalar_one()
        mapped = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraItsmImportMap)
                .where(TiqoraItsmImportMap.source_kind.in_(("ci", "version")))
            )
        ).scalar_one()
    return _Counts(int(cis), int(versions), int(mapped))


async def _prepare(
    w: _World, src: FakeSource, *, dry_run: bool = False
) -> tuple[ClassMaps, dict[str, int]]:
    catalog_ids = await run_catalog(make_ctx(w.factory, src, dry_run=dry_run))
    maps = await run_classes(make_ctx(w.factory, src, dry_run=dry_run), catalog_ids)
    return maps, catalog_ids


async def _full_ci_import(
    w: _World, src: FakeSource, *, dry_run: bool = False, page_size: int = 3
) -> StepContext:
    """Catalog, classes, pass 1 and pass 2 (the CI passes share one context and report)."""
    maps, catalog_ids = await _prepare(w, src, dry_run=dry_run)
    ctx = make_ctx(w.factory, src, dry_run=dry_run, page_size=page_size)
    await run_cis_pass1(ctx, maps, catalog_ids)
    await run_cis_pass2(ctx, maps, catalog_ids)
    return ctx


def _ci_id(src: FakeSource, name: str) -> int:
    return next(c.id for c in src.ci_rows if src.version_rows[c.id][-1].name == name)


def _number(n: int) -> str:
    return str(CI_NUMBER_BASE + n)


async def _tiqora_ci_id(w: _World, number: str) -> int:
    async with w.factory() as s:
        return int(
            (
                await s.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == number))
            ).scalar_one()
        )


async def _ci_row(w: _World, number: str) -> TiqoraCmdbCi:
    async with w.factory() as s:
        return (
            await s.execute(select(TiqoraCmdbCi).where(TiqoraCmdbCi.number == number))
        ).scalar_one()


async def _latest_attributes(w: _World, number: str) -> dict[str, Any]:
    async with w.factory() as s:
        ci = (
            await s.execute(select(TiqoraCmdbCi).where(TiqoraCmdbCi.number == number))
        ).scalar_one()
        raw = (
            await s.execute(
                select(TiqoraCmdbCiVersion.attributes).where(
                    TiqoraCmdbCiVersion.id == ci.current_version_id
                )
            )
        ).scalar_one()
    return dict(json.loads(raw))


async def _latest_version_id(w: _World, ci_id: int) -> int | None:
    async with w.factory() as s:
        return await importer_api.latest_version_id(s, ci_id)


async def _versions_of(w: _World, number: str) -> list[TiqoraCmdbCiVersion]:
    ci_id = await _tiqora_ci_id(w, number)
    async with w.factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraCmdbCiVersion)
                    .where(TiqoraCmdbCiVersion.ci_id == ci_id)
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )


async def _add_version_in_tiqora(w: _World, ci_id: int, attributes: dict[str, Any]) -> int:
    """A version made in Tiqora itself (not by the importer): same effect as an agent's edit."""
    current = await _versions_of_id(w, ci_id)
    last = current[-1]
    async with w.factory() as s:
        vid = await importer_api.add_imported_version(
            s,
            ci_id,
            importer_api.ImportedVersion(
                definition_id=int(last.definition_id),
                name=str(last.name),
                deployment_state_id=int(last.deployment_state_id),
                incident_state_id=int(last.incident_state_id),
                attributes=attributes,
                created_by=7,
                created_at=T0 + timedelta(days=300),
            ),
        )
        await s.commit()
    assert vid is not None
    return vid


async def _versions_of_id(w: _World, ci_id: int) -> list[TiqoraCmdbCiVersion]:
    async with w.factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraCmdbCiVersion)
                    .where(TiqoraCmdbCiVersion.ci_id == ci_id)
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )


def _recorder(calls: list[str], label: str) -> Callable[..., Any]:
    async def record(*_args: Any) -> None:
        calls.append(label)

    return record


# --- review focus tests ---------------------------------------------------------------------


async def test_rerun_is_idempotent_and_appends_new_versions(world: _World) -> None:
    src = sample_source()
    first = await _full_ci_import(world, src)
    before = await _counts(world)
    assert before.cis == 10
    assert before.versions == sum(len(v) for v in src.version_rows.values())
    assert first.report.steps["cis_pass1"].created == 10
    assert first.report.steps["cis_pass2"].created == 10
    assert first.report.issues == []

    second = await _full_ci_import(world, src)
    assert await _counts(world) == before
    assert second.report.steps["cis_pass1"].unchanged == 10
    assert second.report.steps["cis_pass2"].unchanged == 10

    src.add_version(
        ci_id=_ci_id(src, "itim56-ci-03"), name="itim56-ci-03", xml={"Vendor": [{"Content": "Neu"}]}
    )
    third = await _full_ci_import(world, src)
    after = await _counts(world)
    assert after.versions == before.versions + 1
    assert after.cis == before.cis
    assert third.report.steps["cis_pass2"].updated == 1
    assert third.report.steps["cis_pass2"].unchanged == 9
    latest = await _latest_attributes(world, _number(3))
    assert latest["Vendor"] == [{"v": "Neu"}]


async def test_forward_ci_reference_resolved_in_second_pass(world: _World) -> None:
    src = sample_source()
    # CI 01 (first page) points at CI 10 (last page); a third Computer definition adds the field
    ref = src.add_definition(
        "Computer",
        DEFINITION_COMPUTER_V2 + "- Key: DependsOn\n  Name: Depends on\n  Input:\n    Type: CI\n"
        "    CIClassName: Network\n  CountMin: 0\n  CountMax: 1\n",
    )
    first = src.version_rows[1][0]
    src.version_rows[1][0] = dataclasses.replace(
        first,
        definition_id=ref.id,
        xml_data={"DependsOn": [{"Content": "10"}]},
    )
    ctx = await _full_ci_import(world, src)
    tiqora_id_of_ci10 = await _tiqora_ci_id(world, _number(10))
    attrs = await _latest_attributes(world, _number(1))
    assert attrs["DependsOn"][0]["v"] == tiqora_id_of_ci10
    assert [i for i in ctx.report.issues if i.code == "unresolved_reference"] == []


async def test_paging_commits_per_batch_and_checks_cancel(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = sample_source()
    maps, catalog_ids = await _prepare(world, src)
    calls: list[str] = []
    ctx = make_ctx(world.factory, src, page_size=3)
    ctx.check_cancelled = _recorder(calls, "cancel?")
    ctx.progress = _recorder(calls, "progress")
    commits = {"n": 0}
    real_commit = AsyncSession.commit

    async def counting_commit(self: AsyncSession) -> None:
        commits["n"] += 1
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", counting_commit)
    await run_cis_pass1(ctx, maps, catalog_ids)
    assert commits["n"] == 4  # 10 CIs / 3 per page
    assert calls.count("cancel?") == 4
    assert calls.count("progress") == 4
    assert src.max_ids_requested_per_call() <= 3  # never loads more than a page


async def test_ci_changed_in_tiqora_is_not_overwritten(world: _World) -> None:
    src = sample_source()
    await _full_ci_import(world, src)
    ci = await _tiqora_ci_id(world, _number(4))
    own = await _add_version_in_tiqora(world, ci, {"Vendor": [{"v": "in Tiqora geändert"}]})
    src.add_version(
        ci_id=_ci_id(src, "itim56-ci-04"),
        name="itim56-ci-04",
        xml={"Vendor": [{"Content": "aus Znuny"}]},
    )
    before = await _counts(world)
    ctx = await _full_ci_import(world, src)
    assert await _latest_version_id(world, ci) == own
    assert await _counts(world) == before
    assert [(i.code, i.object_ref) for i in ctx.report.issues if i.code == "changed_in_tiqora"] == [
        ("changed_in_tiqora", _number(4))
    ]
    assert ctx.report.steps["cis_pass2"].skipped == 1


async def test_preserves_number_created_and_created_by(world: _World) -> None:
    src = sample_source()
    await _full_ci_import(world, src)
    for n in (1, 5, 9):
        znuny = next(c for c in src.ci_rows if c.id == n)
        row = await _ci_row(world, znuny.number)
        assert row.number == znuny.number == _number(n)
        assert row.created_at == znuny.created_at
        assert row.created_by == znuny.created_by
        # pass 2 brings the real name and the changed data of the newest version
        assert row.name == f"itim56-ci-{n:02d}"
        assert row.changed_at == znuny.changed_at
        versions = await _versions_of(world, znuny.number)
        assert [v.created_at for v in versions] == [v.created_at for v in src.version_rows[n]]
        assert row.version_count == len(src.version_rows[n])
        assert row.current_version_id == versions[-1].id


async def test_ci_without_usable_definition_is_skipped_with_issue(world: _World) -> None:
    src = sample_source()
    # Network: its only definition cannot be parsed -> pass 1 skips its three CIs
    src.definitions = [
        dataclasses.replace(d, raw="[ { Key => ") if d.class_id == GC_NETWORK else d
        for d in src.definitions
    ]
    # CI 01: its version names a definition id that does not exist -> pass 2 skips the CI
    first = src.version_rows[1][0]
    src.version_rows[1][0] = dataclasses.replace(first, definition_id=9999)
    ctx = await _full_ci_import(world, src)
    no_def = sorted(i.object_ref for i in ctx.report.issues if i.code == "no_definition")
    assert no_def == sorted([_number(1), _number(8), _number(9), _number(10)])
    assert ctx.report.steps["cis_pass1"].skipped == 3
    assert ctx.report.steps["cis_pass1"].created == 7
    assert ctx.report.steps["cis_pass2"].skipped == 1
    assert ctx.report.steps["cis_pass2"].created == 6
    counts = await _counts(world)
    assert counts.cis == 7
    assert (await _ci_row(world, _number(1))).version_count == 0
    assert (await _ci_row(world, _number(2))).version_count == len(src.version_rows[2])


async def test_dry_run_counts_like_real_run_and_writes_nothing(world: _World) -> None:
    src = sample_source()
    dry = await _full_ci_import(world, src, dry_run=True)
    assert await _counts(world) == ZERO
    real = await _full_ci_import(world, src)
    for step in ("cis_pass1", "cis_pass2"):
        assert dry.report.steps[step].created == real.report.steps[step].created == 10
    assert [(i.code, i.object_ref) for i in dry.report.issues] == [
        (i.code, i.object_ref) for i in real.report.issues
    ]
    assert dry.touched_ci_ids == set()
    assert len(real.touched_ci_ids) == 10


# --- more behaviour -------------------------------------------------------------------------


async def test_unknown_state_and_attribute_are_reported_not_fatal(world: _World) -> None:
    src = sample_source()
    only = src.version_rows[2][0]
    src.version_rows[2][0] = dataclasses.replace(
        only,
        deployment_state_id=None,
        xml_data={**dict(only.xml_data), "Ghost": [{"Content": "x"}]},
    )
    ctx = await _full_ci_import(world, src)
    codes = [(i.code, i.object_ref) for i in ctx.report.issues]
    assert ("unknown_attribute", f"{_number(2)}:Ghost") in codes
    assert (await _ci_row(world, _number(2))).version_count == len(src.version_rows[2])


async def test_resolver_maps_both_kinds_and_strips() -> None:
    r = MapResolver({"7": 70}, {"3": 30})
    assert r.ci("7") == 70 and r.ci(" 7 ") == 70 and r.ci("8") is None
    assert r.catalog("3") == 30 and r.catalog("4") is None
