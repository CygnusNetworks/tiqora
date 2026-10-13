"""Importer step ``links``: CI links from ``link_relation`` (band 56200-56229).

Znuny's link types come from SysConfig (seeded as sysconfig rows 56200-56209), the Tiqora rows of
``tiqora_itsm_link_type`` are made by itsm1's sync inside the step.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import (
    T0,
    FakeSource,
    cleanup_cmdb_rows,
    make_ctx,
    sample_source,
)
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraItsmImportMap, TiqoraItsmLink
from tiqora.itsm.importer.ports import ZnunyLink
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.catalog import run_catalog
from tiqora.itsm.importer.steps.cis import run_cis_pass1, run_cis_pass2
from tiqora.itsm.importer.steps.classes import ClassMaps, run_classes
from tiqora.itsm.importer.steps.links import ZNUNY_OBJECT_KINDS, run_links
from tiqora.itsm.models import TiqoraItsmLinkType

pytestmark = pytest.mark.db

SYSCONFIG_BASE = 56200
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
    "ITSM::Core::IncidentLinkTypeDirection": {"DependsOn": "Source"},
}


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
    finally:
        engine.dispose()
    async_engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _World(
            async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False), sync_url
        )
    finally:
        _wipe(sync_url)


def _link(
    src: str, skey: str, tgt: str, tkey: str, type_name: str = "DependsOn", state: str = "Valid"
) -> ZnunyLink:
    return ZnunyLink(src, skey, tgt, tkey, type_name, state, T0 + timedelta(days=21), 7)


async def _import_cis(w: _World, src: FakeSource, *, dry_run: bool = False) -> ClassMaps:
    catalog_ids = await run_catalog(make_ctx(w.factory, src, dry_run=dry_run))
    maps = await run_classes(make_ctx(w.factory, src, dry_run=dry_run), catalog_ids)
    ctx = make_ctx(w.factory, src, dry_run=dry_run)
    await run_cis_pass1(ctx, maps, catalog_ids)
    await run_cis_pass2(ctx, maps, catalog_ids)
    return maps


async def _run_links(
    w: _World, src: FakeSource, maps: ClassMaps, *, dry_run: bool = False
) -> StepContext:
    ctx = make_ctx(w.factory, src, dry_run=dry_run)
    await run_links(ctx, maps)
    return ctx


async def _tiqora_id(w: _World, number: str) -> int:
    async with w.factory() as s:
        return int(
            (
                await s.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number == number))
            ).scalar_one()
        )


async def _links(w: _World) -> list[TiqoraItsmLink]:
    async with w.factory() as s:
        return list(
            (await s.execute(select(TiqoraItsmLink).order_by(TiqoraItsmLink.id))).scalars().all()
        )


async def _ci(w: _World, src: FakeSource, znuny_id: int) -> str:
    number = next(c.number for c in src.ci_rows if c.id == znuny_id)
    return str(await _tiqora_id(w, number))


async def _link_map_count(w: _World) -> int:
    async with w.factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraItsmImportMap)
                    .where(TiqoraItsmImportMap.source_kind == "link")
                )
            ).scalar_one()
        )


def test_object_kind_table() -> None:
    assert ZNUNY_OBJECT_KINDS == {"ITSMConfigItem": "ci", "Ticket": "ticket", "Service": "service"}


async def test_ci_ci_ticket_links_and_skips(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    ctx = await _run_links(world, src, maps)

    rows = await _links(world)
    # 1>2, 2>3, 4>5 DependsOn and 1>Ticket 56071 RelevantTo; the FAQ link is not imported
    assert len(rows) == 4
    by_pair = {
        (r.source_kind, r.source_key, r.target_kind, r.target_key, r.link_type) for r in rows
    }
    c = {n: await _ci(world, src, n) for n in (1, 2, 3, 4, 5)}
    assert ("ci", c[1], "ci", c[2], "DependsOn") in by_pair
    assert ("ci", c[2], "ci", c[3], "DependsOn") in by_pair
    assert ("ci", c[4], "ci", c[5], "DependsOn") in by_pair
    # the ticket keeps its Znuny id, the CI is the Tiqora id as string
    assert ("ci", c[1], "ticket", "56071", "RelevantTo") in by_pair
    link = next(r for r in rows if r.target_kind == "ticket")
    assert link.created_by == 1
    assert link.created_at == T0 + timedelta(days=20)

    steps = ctx.report.steps["links"]
    assert (steps.created, steps.skipped, steps.unchanged) == (4, 1, 0)
    assert [(i.code, i.step) for i in ctx.report.issues] == [("unsupported_object", "links")]
    assert "FAQ" in ctx.report.issues[0].message
    assert await _link_map_count(world) == 4


async def test_link_seen_from_both_cis_is_added_once(world: _World) -> None:
    src = sample_source()
    # CI 3 and CI 4 are on different pages (page size 3): both pages see the link
    src.links.append(_link("ITSMConfigItem", "3", "ITSMConfigItem", "4"))
    maps = await _import_cis(world, src)
    ctx = await _run_links(world, src, maps)
    c3, c4 = await _ci(world, src, 3), await _ci(world, src, 4)
    rows = [r for r in await _links(world) if {r.source_key, r.target_key} == {c3, c4}]
    assert len(rows) == 1
    steps = ctx.report.steps["links"]
    # the second sighting (the other end's page) is not counted again
    assert (steps.created, steps.unchanged) == (5, 0)


async def test_non_valid_state_is_skipped(world: _World) -> None:
    src = sample_source()
    src.links.append(_link("ITSMConfigItem", "6", "ITSMConfigItem", "7", state="Removed"))
    maps = await _import_cis(world, src)
    ctx = await _run_links(world, src, maps)
    c6, c7 = await _ci(world, src, 6), await _ci(world, src, 7)
    assert not [r for r in await _links(world) if {r.source_key, r.target_key} == {c6, c7}]
    assert ctx.report.steps["links"].skipped == 2  # the FAQ link and this one
    assert [i.code for i in ctx.report.issues] == ["unsupported_object"]


async def test_link_to_unmapped_ci_is_reported(world: _World) -> None:
    src = sample_source()
    src.links.append(_link("ITSMConfigItem", "8", "ITSMConfigItem", "999"))
    maps = await _import_cis(world, src)
    ctx = await _run_links(world, src, maps)
    assert len(await _links(world)) == 4
    issues = [i for i in ctx.report.issues if i.code == "unresolved_reference"]
    assert len(issues) == 1
    assert issues[0].object_ref == "ITSMConfigItem:8>ITSMConfigItem:999:DependsOn"
    assert ctx.report.steps["links"].skipped == 2


async def test_unknown_link_type_is_reported_not_fatal(world: _World) -> None:
    src = sample_source()
    src.links.append(_link("ITSMConfigItem", "9", "ITSMConfigItem", "10", type_name="NoSuchType"))
    maps = await _import_cis(world, src)
    ctx = await _run_links(world, src, maps)
    assert len(await _links(world)) == 4
    issues = [i for i in ctx.report.issues if i.code == "unknown_link_type"]
    assert len(issues) == 1
    assert "NoSuchType" in issues[0].message


async def test_propagation_direction_comes_from_the_znuny_setting(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run_links(world, src, maps)
    async with world.factory() as s:
        rows = {r.name: r for r in (await s.execute(select(TiqoraItsmLinkType))).scalars().all()}
    assert rows["DependsOn"].propagation == "source"
    assert rows["DependsOn"].origin == "znuny"
    assert rows["RelevantTo"].propagation == "none"


async def test_rerun_is_idempotent(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run_links(world, src, maps)
    before = [(r.id, r.source_key, r.target_key, r.link_type) for r in await _links(world)]
    second = await _run_links(world, src, maps)
    after = [(r.id, r.source_key, r.target_key, r.link_type) for r in await _links(world)]
    assert after == before
    steps = second.report.steps["links"]
    assert (steps.created, steps.updated, steps.unchanged) == (0, 0, 4)


async def test_link_already_in_tiqora_without_map_entry_counts_unchanged(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run_links(world, src, maps)
    async with world.factory() as s:
        await s.execute(
            delete(TiqoraItsmImportMap).where(TiqoraItsmImportMap.source_kind == "link")
        )
        await s.commit()
    again = await _run_links(world, src, maps)
    assert len(await _links(world)) == 4  # add_link answered False for each, nothing doubled
    steps = again.report.steps["links"]
    assert (steps.created, steps.unchanged) == (0, 4)
    assert await _link_map_count(world) == 4


async def test_dry_run_counts_like_real_run_and_writes_nothing(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src, dry_run=True)
    ctx = await _run_links(world, src, maps, dry_run=True)
    steps = ctx.report.steps["links"]
    assert (steps.created, steps.skipped) == (4, 1)
    assert await _links(world) == []
    assert await _link_map_count(world) == 0
