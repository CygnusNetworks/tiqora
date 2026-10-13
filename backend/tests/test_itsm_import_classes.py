"""Importer step ``classes``: classes and every definition version (band 56100-56119)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import (
    CLASS_CATALOG,
    DEFINITION_COMPUTER_V1,
    DEFINITION_NETWORK_V1,
    GROUP_COMPUTER,
    GROUP_NETWORK,
    FakeSource,
    cleanup_cmdb_rows,
    make_ctx,
    sample_source,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
    TiqoraItsmImportMap,
)
from tiqora.itsm.importer.ports import ZnunyCatalogItem
from tiqora.itsm.importer.steps.catalog import run_catalog
from tiqora.itsm.importer.steps.classes import (
    FALLBACK_GROUP_ID,
    class_key,
    run_classes,
)

pytestmark = pytest.mark.db


@dataclass
class _World:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


def _wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        # the class rows point at their current definition and the importer's map points at the
        # class rows: detach, then let the fixture helper delete what the map names
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


async def _run(w: _World, src: FakeSource, *, dry_run: bool = False):
    catalog_ctx = make_ctx(w.factory, src, dry_run=dry_run)
    catalog_ids = await run_catalog(catalog_ctx)
    ctx = make_ctx(w.factory, src, dry_run=dry_run)
    return ctx, await run_classes(ctx, catalog_ids)


async def _definition_versions(w: _World, class_id: int) -> list[TiqoraCmdbClassDefinition]:
    async with w.factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraCmdbClassDefinition)
                    .where(TiqoraCmdbClassDefinition.class_id == class_id)
                    .order_by(TiqoraCmdbClassDefinition.version)
                )
            )
            .scalars()
            .all()
        )


async def _classes(w: _World) -> list[TiqoraCmdbClass]:
    async with w.factory() as s:
        return list(
            (await s.execute(select(TiqoraCmdbClass).order_by(TiqoraCmdbClass.id))).scalars().all()
        )


async def _map_rows(w: _World, kind: str) -> int:
    async with w.factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraItsmImportMap)
                    .where(TiqoraItsmImportMap.source_kind == kind)
                )
            ).scalar_one()
        )


async def test_definition_versions_imported_in_order(world: _World) -> None:
    src = sample_source()
    ctx, maps = await _run(world, src)
    computer = maps.class_ids[src.class_id("Computer")]
    versions = await _definition_versions(world, computer)
    assert [v.version for v in versions] == [1, 2]
    assert [v.id for v in versions] == sorted(v.id for v in versions)
    assert maps.definitions[src.definition_id("Computer", 2)].version == 2
    assert maps.definitions[src.definition_id("Computer", 1)].version == 1
    # the class's current definition is the newer version
    cls = next(c for c in await _classes(world) if c.id == computer)
    assert cls.current_definition_id == versions[-1].id
    assert maps.definitions[src.definition_id("Computer", 2)].definition_id == versions[-1].id
    # the older version is the older Znuny definition: v2 only adds CPU
    keys_v1 = [
        a["key"] for a in maps.definitions[src.definition_id("Computer", 1)].schema["attributes"]
    ]
    keys_v2 = [
        a["key"] for a in maps.definitions[src.definition_id("Computer", 2)].schema["attributes"]
    ]
    assert keys_v1 == ["Vendor", "NIC"] and keys_v2 == ["Vendor", "NIC", "CPU"]
    target = maps.definitions[src.definition_id("Computer", 2)]
    assert target.tiqora_class_id == computer
    assert [a["Key"] for a in target.znuny_definition] == ["Vendor", "NIC", "CPU"]
    # the second class has its single version
    network = maps.class_ids[src.class_id("Network")]
    assert [v.version for v in await _definition_versions(world, network)] == [1]
    assert ctx.report.steps["classes"].created == 2
    assert ctx.report.steps["definitions"].created == 3
    assert ctx.report.issues == []


async def test_rerun_adds_nothing_and_new_znuny_version_adds_one(world: _World) -> None:
    src = sample_source()
    _, first = await _run(world, src)
    ctx2, second = await _run(world, src)
    assert second.class_ids == first.class_ids
    assert {k: v.definition_id for k, v in second.definitions.items()} == {
        k: v.definition_id for k, v in first.definitions.items()
    }
    assert ctx2.report.steps["classes"].unchanged == 2
    assert ctx2.report.steps["classes"].created == 0
    assert ctx2.report.steps["definitions"].unchanged == 3
    assert ctx2.report.steps["definitions"].created == 0
    computer = first.class_ids[src.class_id("Computer")]
    assert len(await _definition_versions(world, computer)) == 2
    assert await _map_rows(world, "definition") == 3

    # a third Znuny version appears: exactly one new Tiqora version, becoming the current one
    extra = src.add_definition(
        "Computer",
        DEFINITION_COMPUTER_V1 + "- Key: Serial\n  Name: Serial\n  Input:\n    Type: Text\n",
    )
    ctx3, third = await _run(world, src)
    versions = await _definition_versions(world, computer)
    assert [v.version for v in versions] == [1, 2, 3]
    assert ctx3.report.steps["definitions"].created == 1
    assert ctx3.report.steps["definitions"].unchanged == 3
    assert third.definitions[extra.id].version == 3
    cls = next(c for c in await _classes(world) if c.id == computer)
    assert cls.current_definition_id == versions[-1].id


async def test_unparsable_definition_keeps_class_and_reports(world: _World) -> None:
    src = sample_source()
    broken = src.add_definition("Network", "[ { Key => ")
    ctx, maps = await _run(world, src)
    assert broken.id not in maps.definitions
    network = maps.class_ids[src.class_id("Network")]
    assert network > 0
    assert [v.version for v in await _definition_versions(world, network)] == [1]
    issues = [i for i in ctx.report.issues if i.code == "definition_unreadable"]
    assert len(issues) == 1
    assert issues[0].object_ref == "class:Network:v2"
    assert issues[0].step == "classes"
    assert ctx.report.steps["definitions"].problems == 1

    # a class whose only definition is unreadable still exists, without any definition
    only_broken = FakeSource(
        catalog=[
            ZnunyCatalogItem(7, CLASS_CATALOG, "Broken", True, None, None, GROUP_NETWORK),
        ]
    )
    only_broken.add_definition("Broken", "[ { Key => ")
    ctx2, maps2 = await _run(world, only_broken)
    assert maps2.class_ids[7] > 0 and maps2.definitions == {}
    assert await _definition_versions(world, maps2.class_ids[7]) == []
    assert [i.code for i in ctx2.report.issues] == ["definition_unreadable"]


async def test_class_group_comes_from_the_permission_preference(world: _World) -> None:
    src = sample_source()
    ctx, maps = await _run(world, src)
    by_id = {c.id: c for c in await _classes(world)}
    assert by_id[maps.class_ids[src.class_id("Computer")]].group_id == GROUP_COMPUTER
    assert by_id[maps.class_ids[src.class_id("Network")]].group_id == GROUP_NETWORK
    assert not [i for i in ctx.report.issues if i.code == "class_without_group"]

    # no preference: the fallback group plus the issue; the rest of the class is unaffected
    nogroup = FakeSource(
        catalog=[ZnunyCatalogItem(9, CLASS_CATALOG, "Loose Asset", True, None, None)]
    )
    nogroup.add_definition("Loose Asset", DEFINITION_NETWORK_V1)
    ctx2, maps2 = await _run(world, nogroup)
    row = next(c for c in await _classes(world) if c.id == maps2.class_ids[9])
    assert (row.class_key, row.group_id, row.name) == (
        "loose_asset",
        FALLBACK_GROUP_ID,
        "Loose Asset",
    )
    assert row.number_prefix == "LOOSE_ASSET-"
    [issue] = [i for i in ctx2.report.issues if i.code == "class_without_group"]
    assert issue.object_ref == "class:Loose Asset" and "Loose Asset" in issue.message
    assert len(maps2.definitions) == 1


async def test_changed_group_updates_the_class_not_a_second_one(world: _World) -> None:
    src = sample_source()
    _, first = await _run(world, src)
    computer = src.class_id("Computer")
    src.catalog = [
        ZnunyCatalogItem(
            i.id, i.catalog_class, "Computer (neu)", i.valid, i.comment, i.functionality, 56110
        )
        if i.id == computer
        else i
        for i in src.catalog
    ]
    ctx, second = await _run(world, src)
    assert second.class_ids == first.class_ids
    assert ctx.report.steps["classes"].updated == 1
    rows = {c.id: c for c in await _classes(world)}
    row = rows[first.class_ids[computer]]
    # renamed and regrouped, but the key stays (it is what CIs and references use)
    assert (row.name, row.class_key, row.group_id) == ("Computer (neu)", "computer", 56110)
    assert len(rows) == 2


async def test_invalid_attribute_keys_are_reported_per_class(world: _World) -> None:
    raw = (
        "---\n"
        "- Key: Vendor\n  Name: Vendor\n  Input:\n    Type: Text\n"
        "- Key: Bad Key\n  Name: Bad\n  Input:\n    Type: Text\n"
        "- Key: Größe\n  Name: Size\n  Input:\n    Type: Text\n"
        "- Key: Model\n  Name: Model\n  Frobnicate: 1\n  Input:\n    Type: Text\n"
    )
    src = FakeSource(
        catalog=[ZnunyCatalogItem(11, CLASS_CATALOG, "Printer", True, None, None, GROUP_NETWORK)]
    )
    src.add_definition("Printer", raw)
    ctx, maps = await _run(world, src)
    [target] = maps.definitions.values()
    assert [a["key"] for a in target.schema["attributes"]] == ["Vendor", "Model"]
    assert [a["Key"] for a in target.znuny_definition] == ["Vendor", "Model"]
    invalid = sorted(i.object_ref for i in ctx.report.issues if i.code == "definition_key_invalid")
    assert invalid == ["class:Printer:v1:Bad Key", "class:Printer:v1:Größe"]
    unknown = [i for i in ctx.report.issues if i.code == "definition_attribute_key_unknown"]
    assert len(unknown) == 1 and "Frobnicate" in unknown[0].message
    assert [v.version for v in await _definition_versions(world, maps.class_ids[11])] == [1]


async def test_optional_input_is_not_forced_mandatory(world: _World) -> None:
    # CMDB-PM-I1: only inputs with Required=1 default to CountMin 1
    src = sample_source()
    _, maps = await _run(world, src)
    attrs = {
        a["key"]: a for a in maps.definitions[src.definition_id("Computer", 1)].schema["attributes"]
    }
    assert attrs["Vendor"]["count_min"] == 0 and attrs["Vendor"]["required"] is False
    # an explicit CountMin is kept as written, even next to Required
    assert attrs["NIC"]["required"] is True and attrs["NIC"]["count_min"] == 0


async def test_classes_dry_run_writes_nothing(world: _World) -> None:
    src = sample_source()
    ctx, maps = await _run(world, src, dry_run=True)
    assert all(v < 0 for v in maps.class_ids.values())
    assert all(t.definition_id < 0 for t in maps.definitions.values())
    assert sorted(t.version for t in maps.definitions.values()) == [1, 1, 2]
    assert ctx.report.steps["classes"].created == 2
    assert ctx.report.steps["definitions"].created == 3
    assert await _classes(world) == []
    assert await _map_rows(world, "class") == 0 and await _map_rows(world, "definition") == 0

    # after a real import a dry run is all-unchanged and still changes nothing
    _, real = await _run(world, src)
    ctx2, again = await _run(world, src, dry_run=True)
    assert again.class_ids == real.class_ids
    assert ctx2.report.steps["classes"].unchanged == 2
    assert ctx2.report.steps["definitions"].unchanged == 3
    assert len(await _classes(world)) == 2


def test_class_key_slug() -> None:
    assert class_key("Computer", 1) == "computer"
    assert class_key("Test/QA Rack", 1) == "test_qa_rack"
    assert class_key("Hardware", 1) == "hardware"
    assert class_key("3D Printer", 4) == "class_4"  # must start with a letter
    assert class_key("Größe", 5) == "gr_e"
    assert class_key("!!!", 7) == "class_7"
    assert class_key("x" * 80, 1) == "x" * 50
