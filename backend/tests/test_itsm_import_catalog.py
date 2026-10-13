"""Importer step ``catalog`` and the importer fixtures (band 56070-56099, both dialects)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import (
    DEPLOYMENT,
    GC_PRODUCTION,
    FakeSource,
    cleanup_cmdb_rows,
    make_ctx,
    sample_source,
)
from tiqora.cmdb.seed import DEFAULT_CATALOG_ROWS, seed_defaults
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraItsmImportMap
from tiqora.itsm.importer.ports import ZnunyCatalogItem
from tiqora.itsm.importer.steps.catalog import catalog_key, run_catalog

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
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog"))
            # seed_defaults (adoption test) also writes the default link types
            conn.execute(text("DELETE FROM tiqora_itsm_link_type WHERE origin = 'tiqora'"))
            conn.execute(text("DELETE FROM tiqora_itsm_import_map WHERE run_id >= 56070"))
        cleanup_cmdb_rows(sync_url)
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


async def _catalog(w: _World) -> list[TiqoraCmdbCatalog]:
    async with w.factory() as s:
        return list(
            (await s.execute(select(TiqoraCmdbCatalog).order_by(TiqoraCmdbCatalog.id)))
            .scalars()
            .all()
        )


async def _map_count(w: _World) -> int:
    async with w.factory() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraItsmImportMap)
                    .where(TiqoraItsmImportMap.source_kind == "catalog")
                )
            ).scalar_one()
        )


# --- the step ----------------------------------------------------------------------------


async def test_catalog_import_creates_then_is_unchanged(world: _World) -> None:
    src = sample_source()
    ctx = make_ctx(world.factory, src)
    ids = await run_catalog(ctx)
    assert ctx.report.steps["catalog"].created == len(src.catalog) == 10
    assert set(ids) == {str(i.id) for i in src.catalog}
    rows = await _catalog(world)
    assert len(rows) == 10
    assert {r.functionality for r in rows} >= {"productive", "operational", "incident"}
    by_id = {r.id: r for r in rows}
    prod = by_id[ids[str(GC_PRODUCTION)]]
    assert (prod.catalog_class, prod.item_key, prod.name) == (
        DEPLOYMENT,
        "production",
        "Production",
    )
    assert prod.functionality == "productive" and prod.valid is True

    ctx2 = make_ctx(world.factory, src)
    assert await run_catalog(ctx2) == ids
    assert ctx2.report.steps["catalog"].unchanged == len(src.catalog)
    assert ctx2.report.steps["catalog"].created == 0
    assert len(await _catalog(world)) == 10

    src.rename_catalog_item(src.catalog[0].id, "Produktiv")  # fingerprint changes
    ctx3 = make_ctx(world.factory, src)
    ids3 = await run_catalog(ctx3)
    assert ctx3.report.steps["catalog"].updated == 1
    assert ctx3.report.steps["catalog"].unchanged == len(src.catalog) - 1
    # the rename updates the same row (same id, same key) instead of adding one
    assert ids3 == ids
    rows3 = await _catalog(world)
    assert len(rows3) == 10
    renamed = {r.id: r for r in rows3}[ids[str(src.catalog[0].id)]]
    assert (renamed.name, renamed.item_key) == ("Produktiv", "computer")


async def test_catalog_dry_run_writes_nothing(world: _World) -> None:
    src = sample_source()
    ctx = make_ctx(world.factory, src, dry_run=True)
    ids = await run_catalog(ctx)
    assert ctx.report.steps["catalog"].created == len(src.catalog)
    assert all(v < 0 for v in ids.values())  # fake ids
    assert await _catalog(world) == []
    assert await _map_count(world) == 0

    # after a real run a dry run reports unchanged / updated and still changes nothing
    real = await run_catalog(make_ctx(world.factory, src))
    src.rename_catalog_item(GC_PRODUCTION, "Produktion")
    ctx2 = make_ctx(world.factory, src, dry_run=True)
    assert await run_catalog(ctx2) == real
    assert ctx2.report.steps["catalog"].updated == 1
    assert ctx2.report.steps["catalog"].unchanged == len(src.catalog) - 1
    names = {r.item_key: r.name for r in await _catalog(world)}
    assert names["production"] == "Production"


async def test_catalog_import_adopts_seeded_rows(world: _World) -> None:
    async with world.factory() as s:
        await seed_defaults(s)
        await s.commit()
        seeded = {
            (r.catalog_class, r.item_key): r.id
            for r in (await s.execute(select(TiqoraCmdbCatalog))).scalars()
        }
    assert len(seeded) == len(DEFAULT_CATALOG_ROWS)
    src = sample_source()
    ids = await run_catalog(make_ctx(world.factory, src))
    # "Production" is the seeded "production" row, not a second one
    assert ids[str(GC_PRODUCTION)] == seeded[(DEPLOYMENT, "production")]
    rows = await _catalog(world)
    assert len(rows) == len(DEFAULT_CATALOG_ROWS) + 2  # + the two CI class entries
    cleanup_cmdb_rows(world.sync_url)
    # the adopted seed rows are restored, the class entries are gone
    assert len(await _catalog(world)) == len(DEFAULT_CATALOG_ROWS)


async def test_catalog_keys_do_not_collide_and_invalid_stays_invalid(world: _World) -> None:
    items = [
        ZnunyCatalogItem(1, DEPLOYMENT, "Test/QA", True, None, "preproductive"),
        ZnunyCatalogItem(2, DEPLOYMENT, "test qa", True, None, None),
        ZnunyCatalogItem(3, DEPLOYMENT, "---", False, None, None),
    ]
    src = FakeSource(catalog=items)
    ctx = make_ctx(world.factory, src)
    ids = await run_catalog(ctx)
    rows = {r.id: r for r in await _catalog(world)}
    assert rows[ids["1"]].item_key == "test_qa"
    assert rows[ids["2"]].item_key == "test_qa_2"
    assert rows[ids["3"]].item_key == "item_3" and rows[ids["3"]].valid is False
    assert [rows[ids[k]].position for k in ("1", "2", "3")] == [10, 20, 30]
    assert ctx.report.steps["catalog"].created == 3


def test_catalog_key_slug() -> None:
    assert catalog_key("Test/QA", 1) == "test_qa"
    assert catalog_key("Production", 1) == "production"
    assert catalog_key("  Größe  XL ", 1) == "größe_xl"
    assert catalog_key("!!!", 7) == "item_7"
    assert len(catalog_key("x" * 300, 1)) == 100


# --- the fixtures ------------------------------------------------------------------------


async def test_sample_source_shape_and_knobs() -> None:
    src = sample_source()
    assert len(src.ci_rows) == 10
    assert src.ci_rows[0].number == "2026101256001" and src.ci_rows[9].number == "2026101256010"
    assert {len(v) for v in src.version_rows.values()} == {1, 2, 3}
    assert {d.class_id for d in src.definitions} == {22, 25}
    assert [d.version for d in src.definitions if d.class_id == 22] == [1, 2]
    assert await src.ci_ids_page(0, 4) == [1, 2, 3, 4]
    assert await src.ci_ids_page(4, 4) == [5, 6, 7, 8]
    assert src.max_ids_requested_per_call() == 4
    ci_links = await src.links_for_cis([1])
    assert {(link.target_object, link.type_name) for link in ci_links} == {
        ("ITSMConfigItem", "DependsOn"),
        ("Ticket", "RelevantTo"),
    }
    assert [a.data is None for a in await src.attachments([1, 2], with_data=True)] == [False, True]
    assert all(a.data is None for a in await src.attachments([1, 2], with_data=False))
    src.remove_ci(1)
    assert await src.ci_count() == 9 and await src.links_for_cis([1]) == []
    v = src.add_version(2, "renamed", {"Vendor": [{"Content": "X"}]})
    assert (await src.cis([2]))[0].last_version_id == v.id
    assert (await src.versions([2]))[2][-1].name == "renamed"


async def test_fake_source_failure_knobs() -> None:
    seen: list[int] = []
    src = sample_source(fail_once_after_pages=1, on_first_page=lambda: seen.append(1))
    assert len(await src.ci_ids_page(0, 3)) == 3  # page 1
    with pytest.raises(RuntimeError, match="worker died"):
        await src.ci_ids_page(3, 3)  # page 2 dies once
    assert len(await src.ci_ids_page(3, 3)) == 3  # and then works
    assert seen == [1]
    src2 = sample_source(changed_since_count=3)
    assert await src2.count_cis_changed_since(src2.ci_rows[0].created_at) == 3
    assert src2.changed_since_args == [src2.ci_rows[0].created_at]
