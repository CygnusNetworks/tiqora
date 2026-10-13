"""Importer framework: fingerprint, report, dry-run writer, map store (band 56050-56069)."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tiqora.db.tiqora.models import TiqoraItsmImportMap
from tiqora.itsm.importer.issue_codes import ISSUE_CODES
from tiqora.itsm.importer.mapstore import MapStore, fingerprint
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.writer import DryRunWriter

T0 = datetime(2024, 1, 1)
BAND_LO, BAND_HI = 56050, 56069


@dataclass
class _Rec:
    id: int
    name: str
    blob: bytes
    when: datetime


# --- fingerprint ----------------------------------------------------------------------


def test_fingerprint_stable_across_dict_order() -> None:
    assert fingerprint({"a": 1, "b": [1, 2]}) == fingerprint({"b": [1, 2], "a": 1})


def test_fingerprint_differs_on_any_field() -> None:
    base = _Rec(1, "x", b"abc", T0)
    fps = {
        fingerprint(base),
        fingerprint(_Rec(2, "x", b"abc", T0)),
        fingerprint(_Rec(1, "y", b"abc", T0)),
        fingerprint(_Rec(1, "x", b"abd", T0)),
        fingerprint(_Rec(1, "x", b"abc", datetime(2024, 1, 2))),
    }
    assert len(fps) == 5
    assert fingerprint(base) == fingerprint(_Rec(1, "x", b"abc", T0))
    assert len(fingerprint(base)) == 64


# --- report ---------------------------------------------------------------------------


def test_report_summary_cuts_issues_keeps_counts() -> None:
    r = ImportReport()
    r.count("cis", "created", 3)
    r.count("cis", "created")
    r.count("links", "skipped")
    for i in range(250):
        r.issue("cis", "bad_value", f"ci:{i}", "m")
    s = r.summary()
    assert s["steps"]["cis"] == {
        "created": 4,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "problems": 0,
    }
    assert s["steps"]["links"]["skipped"] == 1
    assert len(s["issues"]) == 200
    assert s["issues"][0] == {
        "step": "cis",
        "code": "bad_value",
        "object_ref": "ci:0",
        "message": "m",
    }
    assert s["issue_count"] == 250
    assert len(r.summary(max_issues=5)["issues"]) == 5


def test_report_start_step_and_unknown_counter() -> None:
    r = ImportReport()
    r.start_step("catalog")
    assert "catalog" in r.summary()["steps"]
    with pytest.raises(ValueError):
        r.count("catalog", "bogus")


def test_report_csv_bom_header_and_escaping() -> None:
    r = ImportReport()
    r.issue("cis", "bad_value", "ci:1", 'a;b\nc "q"')
    r.issue("cis", "bad_value", "ci:2", '=HYPERLINK("x")')
    r.issue("cis", "bad_value", "ci:3", "+49 @x\t")
    raw = r.to_csv()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == ["Schritt", "Code", "Objekt", "Meldung"]
    assert rows[1] == ["cis", "bad_value", "ci:1", 'a;b\nc "q"']
    assert rows[2][3] == '\'=HYPERLINK("x")'
    assert rows[3][3] == "'+49 @x\t"


def test_issue_codes_have_explanations() -> None:
    for code in (
        "unsupported_object",
        "attachment_in_filesystem",
        "attachment_too_large",
        "unresolved_reference",
        "unknown_attribute",
        "bad_value",
        "definition_unreadable",
        "definition_converted_with_issues",
        "no_definition",
        "class_without_group",
        "changed_in_tiqora",
        "deleted_in_znuny",
        "changes_not_imported",
        "definition_key_invalid",
        "definition_attribute_key_unknown",
    ):
        assert ISSUE_CODES[code].strip()


# --- dry-run writer -------------------------------------------------------------------


class _RealWriter(DryRunWriter):
    async def latest_version_id(self, ci_id: int) -> int | None:
        return 777 if ci_id == 5 else None


async def test_dry_run_fake_ids_negative_and_unique() -> None:
    w = DryRunWriter()
    cat = await w.upsert_catalog_item(
        catalog_class="c", item_key="k", name="n", functionality=None, valid=True
    )
    cls = await w.upsert_class(class_key="srv", name="S", group_id=1, number_prefix="1", user_id=1)
    defn = await w.add_definition(
        class_id=cls, attributes=[{"Key": "A"}], comment=None, created_by=1, created_at=T0
    )
    ci1 = await w.create_imported_ci(
        class_id=cls, number="1", name="n", created_by=1, created_at=T0
    )
    ci2 = await w.create_imported_ci(
        class_id=cls, number="2", name="n", created_by=1, created_at=T0
    )
    ids = [cat, cls, defn, ci1, ci2]
    assert all(i < 0 for i in ids)
    assert len(set(ids)) == len(ids)
    # same upsert/definition again -> same fake id
    assert (
        await w.upsert_class(class_key="srv", name="S", group_id=1, number_prefix="1", user_id=1)
        == cls
    )
    assert (
        await w.add_definition(
            class_id=cls, attributes=[{"Key": "A"}], comment=None, created_by=1, created_at=T0
        )
        == defn
    )


async def test_dry_run_unchanged_version_is_none() -> None:
    w = DryRunWriter()
    kw = dict(
        definition_id=-1,
        name="n",
        deployment_state_id=1,
        incident_state_id=1,
        created_by=1,
        created_at=T0,
    )
    v1 = await w.add_imported_version(-10, attributes={"A": [{"v": 1}]}, **kw)  # type: ignore[arg-type]
    assert v1 is not None and v1 < 0
    assert await w.add_imported_version(-10, attributes={"A": [{"v": 1}]}, **kw) is None  # type: ignore[arg-type]
    v2 = await w.add_imported_version(-10, attributes={"A": [{"v": 2}]}, **kw)  # type: ignore[arg-type]
    assert v2 is not None and v2 != v1
    assert await w.latest_version_id(-10) == v2
    # another CI keeps its own state
    assert await w.add_imported_version(-11, attributes={"A": [{"v": 2}]}, **kw) is not None  # type: ignore[arg-type]


async def test_dry_run_latest_version_falls_back_to_real_for_real_ids() -> None:
    w = DryRunWriter(_RealWriter())
    assert await w.latest_version_id(5) == 777
    assert await w.latest_version_id(6) is None
    assert await w.latest_version_id(-3) is None


async def test_dry_run_link_and_noops() -> None:
    w = DryRunWriter()
    kw = dict(
        source_kind="ci",
        source_key="1",
        target_kind="ticket",
        target_key="2",
        link_type="relevant",
        created_by=1,
        created_at=T0,
    )
    assert await w.add_link(**kw) is True  # type: ignore[arg-type]
    assert await w.add_link(**kw) is False  # type: ignore[arg-type]
    assert await w.import_history(-1, [("a", "b", 1, T0)]) == 1
    assert await w.finish_import([-1]) == 0


# --- map store (DB) -------------------------------------------------------------------


@dataclass
class _Db:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


def _wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraItsmImportMap.__table__.create(engine, checkfirst=True)
        with engine.begin() as conn:
            conn.execute(
                delete(TiqoraItsmImportMap).where(
                    TiqoraItsmImportMap.run_id.between(BAND_LO, BAND_HI)
                )
            )
    finally:
        engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def mapdb(request: pytest.FixtureRequest) -> Iterator[_Db]:
    sync_url: str = request.getfixturevalue(request.param)
    _wipe(sync_url)
    engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _Db(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), sync_url)
    finally:
        _wipe(sync_url)


@pytest.fixture
async def session(mapdb: _Db) -> AsyncIterator[AsyncSession]:
    async with mapdb.factory() as s:
        yield s


async def _count(s: AsyncSession) -> int:
    return int(
        (
            await s.execute(
                select(func.count())
                .select_from(TiqoraItsmImportMap)
                .where(TiqoraItsmImportMap.run_id.between(BAND_LO, BAND_HI))
            )
        ).scalar_one()
    )


@pytest.mark.db
async def test_mapstore_put_get_many_chunks(mapdb: _Db) -> None:
    ids = [str(56_000_000 + i) for i in range(1200)]
    async with mapdb.factory() as s:
        store = MapStore(s, run_id=56050, read_only=False)
        for n, sid in enumerate(ids):
            await store.put("ci", sid, n + 1, f"fp{n}")
        await s.commit()
    async with mapdb.factory() as s:
        store = MapStore(s, run_id=56051, read_only=False)
        got = await store.get_many("ci", [*ids, "missing"])
        assert len(got) == 1200
        assert got[ids[0]].target_id == 1 and got[ids[0]].fingerprint == "fp0"
        assert got[ids[1199]].target_id == 1200
        assert "missing" not in got
        assert (await store.get("ci", ids[7])).target_id == 8  # type: ignore[union-attr]
        assert await store.get("version", ids[7]) is None
        assert set(await store.all_source_ids("ci")) >= set(ids)
        assert await store.get_many("ci", []) == {}


@pytest.mark.db
async def test_mapstore_update_keeps_one_row(mapdb: _Db) -> None:
    async with mapdb.factory() as s:
        store = MapStore(s, run_id=56052, read_only=False)
        await store.put("ci", "56052", 10, "a")
        await store.put("ci", "56052", 11, "b")
        await s.commit()
    async with mapdb.factory() as s:
        assert await _count(s) == 1
        entry = await MapStore(s, run_id=56053, read_only=False).get("ci", "56052")
        assert entry is not None and (entry.target_id, entry.fingerprint) == (11, "b")
        # a later run updates the row in place and takes over run_id
        await MapStore(s, run_id=56053, read_only=False).put("ci", "56052", 12, "c")
        await s.commit()
    async with mapdb.factory() as s:
        assert await _count(s) == 1
        row = (
            await s.execute(
                select(TiqoraItsmImportMap).where(TiqoraItsmImportMap.source_id == "56052")
            )
        ).scalar_one()
        assert (row.target_id, row.fingerprint, row.run_id) == (12, "c", 56053)


@pytest.mark.db
async def test_mapstore_read_only_overlay(mapdb: _Db) -> None:
    async with mapdb.factory() as s:
        store = MapStore(s, run_id=56054, read_only=True)
        await store.put("ci", "56055", -1, "fp")
        assert (await store.get("ci", "56055")).target_id == -1  # type: ignore[union-attr]
        assert (await store.get_many("ci", ["56055"]))["56055"].target_id == -1
        assert "56055" in await store.all_source_ids("ci")
        await s.commit()
    async with mapdb.factory() as s:
        assert await _count(s) == 0
        assert await MapStore(s, run_id=56054, read_only=False).get("ci", "56055") is None
