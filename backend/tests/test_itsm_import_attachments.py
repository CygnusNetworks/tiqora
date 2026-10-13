"""Importer step ``attachments`` and its ``virtual_fs`` reader (band 56230-56259).

The reader runs on the core tables of the Znuny test databases (``virtual_fs*`` are core tables),
seeded the way Znuny's ``ConfigItemAttachmentAdd`` writes them. The step runs on ``FakeSource``
and the real CMDB services.
"""

from __future__ import annotations

import base64
import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, func, select, text
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
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraCmdbAttachment, TiqoraCmdbCi, TiqoraItsmImportMap
from tiqora.itsm.importer import source_extra
from tiqora.itsm.importer.ports import ZnunyAttachment
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.attachments import KIND_ATTACHMENT, map_key, run_attachments
from tiqora.itsm.importer.steps.catalog import run_catalog
from tiqora.itsm.importer.steps.cis import run_cis_pass1, run_cis_pass2
from tiqora.itsm.importer.steps.classes import ClassMaps, run_classes

pytestmark = pytest.mark.db

CI_A, CI_B, CI_C = 56231, 56232, 56233  # Znuny CI ids of the reader tests
DB_BACKEND = "Kernel::System::VirtualFS::DB"
FS_BACKEND = "Kernel::System::VirtualFS::FS"
BLOB = b"\x00\x01binary\x00data\xff"


@dataclass
class _World:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str
    dialect: str
    statements: list[str]


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
            conn.execute(
                text(
                    "DELETE FROM virtual_fs_preferences WHERE virtual_fs_id IN"
                    " (SELECT id FROM virtual_fs WHERE filename LIKE 'ConfigItem/5623%')"
                )
            )
            conn.execute(text("DELETE FROM virtual_fs WHERE filename LIKE 'ConfigItem/5623%'"))
            conn.execute(text("DELETE FROM virtual_fs_db WHERE filename LIKE 'ConfigItem/5623%'"))
    finally:
        engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    _wipe(sync_url)
    async_engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    statements: list[str] = []

    @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    try:
        yield _World(
            async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False),
            sync_url,
            async_engine.dialect.name,
            statements,
        )
    finally:
        _wipe(sync_url)


def _seed_file(
    w: _World,
    ci_id: int,
    name: str,
    *,
    backend: str = DB_BACKEND,
    data: bytes | None = BLOB,
    content_type: str | None = "application/octet-stream",
    size: int | None = None,
    bare_key: bool = False,
    extra_prefs: dict[str, str] | None = None,
) -> None:
    """Rows as ``ConfigItemAttachmentAdd`` + ``VirtualFS::Write`` leave them.

    ``backend_key`` of the DB backend is ``Compress=0;Crypt=0;FileID=<id>;Mode=binary;`` (as the
    Znuny 6.5 / 7.3 peers write it); ``bare_key`` stores just the number instead.
    """
    filename = f"ConfigItem/{ci_id}/{name}"
    engine = create_engine(w.sync_url)
    try:
        with engine.begin() as conn:
            key = "fs-key"
            if backend == DB_BACKEND:
                payload = (
                    base64.encodebytes(data or b"").decode() if w.dialect == "postgresql" else data
                )
                conn.execute(
                    text(
                        "INSERT INTO virtual_fs_db (filename, content, create_time)"
                        " VALUES (:f, :c, :t)"
                    ),
                    {"f": filename, "c": payload, "t": T0},
                )
                file_id = conn.execute(
                    text("SELECT MAX(id) FROM virtual_fs_db WHERE filename = :f"),
                    {"f": filename},
                ).scalar_one()
                key = (
                    str(file_id)
                    if bare_key
                    else f"Compress=0;Crypt=0;FileID={file_id};Mode=binary;"
                )
            conn.execute(
                text(
                    "INSERT INTO virtual_fs (filename, backend, backend_key, create_time)"
                    " VALUES (:f, :b, :k, :t)"
                ),
                {"f": filename, "b": backend, "k": key, "t": T0 + timedelta(hours=1)},
            )
            vfs_id = conn.execute(
                text("SELECT MAX(id) FROM virtual_fs WHERE filename = :f"), {"f": filename}
            ).scalar_one()
            prefs = {
                "FilesizeRaw": str(len(data or b"") if size is None else size),
            }
            if content_type is not None:
                prefs["ContentType"] = content_type
            prefs.update(extra_prefs or {})
            for k, v in prefs.items():
                conn.execute(
                    text(
                        "INSERT INTO virtual_fs_preferences"
                        " (virtual_fs_id, preferences_key, preferences_value) VALUES (:i, :k, :v)"
                    ),
                    {"i": vfs_id, "k": k, "v": v},
                )
    finally:
        engine.dispose()


# --- reader ----------------------------------------------------------------------------------


async def test_reader_returns_db_and_fs_backend_files(world: _World) -> None:
    _seed_file(world, CI_A, "dump.bin", data=BLOB, content_type="application/x-dump")
    _seed_file(world, CI_A, "big.iso", backend=FS_BACKEND, data=None, size=4096)
    _seed_file(world, CI_B, "note.txt", data=b"hello", content_type="text/plain")
    # another CI and an unrelated virtual_fs file are not returned
    _seed_file(world, CI_C, "other.txt", data=b"x")
    async with world.factory() as s:
        got = await source_extra.read_ci_attachments(s, [CI_B, CI_A], with_data=True)
    assert [(a.ci_id, a.filename) for a in got] == [
        (CI_A, "dump.bin"),
        (CI_A, "big.iso"),
        (CI_B, "note.txt"),
    ]
    dump, big, note = got
    assert dump.data == BLOB  # NUL and 0xff survive both dialects
    assert (dump.content_type, dump.size) == ("application/x-dump", len(BLOB))
    assert dump.created_at == T0 + timedelta(hours=1)
    assert (big.data, big.size) == (None, 4096)  # FS backend: size known, no bytes
    assert (note.data, note.content_type) == (b"hello", "text/plain")


async def test_reader_reads_a_bare_number_backend_key(world: _World) -> None:
    """Older rows (and the plan's first fixtures) carry just the ``virtual_fs_db`` id."""
    _seed_file(world, CI_A, "bare.bin", bare_key=True)
    async with world.factory() as s:
        got = await source_extra.read_ci_attachments(s, [CI_A], with_data=True)
    assert [(a.filename, a.data) for a in got] == [("bare.bin", BLOB)]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Compress=0;Crypt=0;FileID=3;Mode=binary;", 3),  # what Znuny 6.5 / 7.3 write
        ("FileID=12;", 12),
        (" 7 ", 7),
        ("42", 42),
        ("Compress=0;Crypt=0;Mode=binary;", None),
        ("FileID=x;", None),
        ("fs-key", None),
        ("", None),
    ],
)
def test_db_file_id_parses_znuny_backend_keys(key: str, expected: int | None) -> None:
    assert source_extra._db_file_id(key) == expected


async def test_reader_carries_the_uploader_and_content_id_preferences(world: _World) -> None:
    """GI ``ConfigItemGet`` shows ``UserID`` and ``ContentID`` of the VirtualFS preferences."""
    _seed_file(world, CI_A, "a.bin", extra_prefs={"UserID": "7", "ContentID": "cid-1"})
    _seed_file(world, CI_B, "b.bin")
    async with world.factory() as s:
        got = await source_extra.read_ci_attachments(s, [CI_A, CI_B], with_data=False)
    assert [(a.filename, a.user_id, a.content_id) for a in got] == [
        ("a.bin", 7, "cid-1"),
        ("b.bin", None, ""),
    ]


async def test_reader_without_data_never_selects_content(world: _World) -> None:
    _seed_file(world, CI_A, "dump.bin")
    _seed_file(world, CI_B, "note.txt", data=b"hello")
    world.statements.clear()
    async with world.factory() as s:
        got = await source_extra.read_ci_attachments(s, [CI_A, CI_B], with_data=False)
    assert [(a.filename, a.data, a.size) for a in got] == [
        ("dump.bin", None, len(BLOB)),
        ("note.txt", None, 5),
    ]
    assert world.statements  # the recorder sees the reads...
    assert not [q for q in world.statements if "virtual_fs_db" in q]  # ...but none of the content
    world.statements.clear()
    async with world.factory() as s:
        await source_extra.read_ci_attachments(s, [CI_A], with_data=True)
    assert [q for q in world.statements if "virtual_fs_db" in q]


async def test_reader_uses_bounded_statements(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for ci in (CI_A, CI_B, CI_C):
        _seed_file(world, ci, "f.txt", data=b"1")
    monkeypatch.setattr(source_extra, "_PREFIX_CHUNK", 1)
    monkeypatch.setattr(
        source_extra,
        "chunks",
        lambda items, size=2: [items[i : i + size] for i in range(0, len(items), size)],
    )
    world.statements.clear()
    async with world.factory() as s:
        got = await source_extra.read_ci_attachments(s, [CI_A, CI_B, CI_C], with_data=True)
    assert len(got) == 3
    listing = [q for q in world.statements if "FROM virtual_fs WHERE" in q]
    assert len(listing) == 3  # one statement per CI prefix chunk
    assert all(q.count("LIKE") == 1 for q in listing)
    assert len([q for q in world.statements if "FROM virtual_fs_db" in q]) == 2  # 3 ids, size 2


async def test_reader_skips_content_of_oversized_file(world: _World) -> None:
    _seed_file(world, CI_A, "huge.bin", data=b"tiny", size=source_extra.MAX_ATTACHMENT_BYTES + 1)
    async with world.factory() as s:
        (att,) = await source_extra.read_ci_attachments(s, [CI_A], with_data=True)
    assert att.data is None
    assert att.size == source_extra.MAX_ATTACHMENT_BYTES + 1


async def test_reader_defaults_for_missing_preferences(world: _World) -> None:
    _seed_file(world, CI_A, "plain", data=b"abc", content_type=None)
    async with world.factory() as s:
        (att,) = await source_extra.read_ci_attachments(s, [CI_A], with_data=True)
    assert (att.content_type, att.size, att.data) == ("", 3, b"abc")


# --- step ------------------------------------------------------------------------------------


async def _import_cis(w: _World, src: FakeSource, *, dry_run: bool = False) -> ClassMaps:
    catalog_ids = await run_catalog(make_ctx(w.factory, src, dry_run=dry_run))
    maps = await run_classes(make_ctx(w.factory, src, dry_run=dry_run), catalog_ids)
    ctx = make_ctx(w.factory, src, dry_run=dry_run)
    await run_cis_pass1(ctx, maps, catalog_ids)
    await run_cis_pass2(ctx, maps, catalog_ids)
    return maps


async def _run(
    w: _World, src: FakeSource, maps: ClassMaps, *, dry_run: bool = False
) -> StepContext:
    ctx = make_ctx(w.factory, src, dry_run=dry_run)
    await run_attachments(ctx, maps)
    return ctx


async def _stored(w: _World) -> list[TiqoraCmdbAttachment]:
    async with w.factory() as s:
        return list(
            (await s.execute(select(TiqoraCmdbAttachment).order_by(TiqoraCmdbAttachment.id)))
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
                    .where(TiqoraItsmImportMap.source_kind == KIND_ATTACHMENT)
                )
            ).scalar_one()
        )


async def test_db_attachment_imported_once_and_fs_attachment_reported(world: _World) -> None:
    src = sample_source()  # CI 1: report.txt (DB, with NUL), CI 2: dump.bin (FS)
    maps = await _import_cis(world, src)
    ctx = await _run(world, src, maps)

    rows = await _stored(world)
    assert len(rows) == 1
    att = rows[0]
    assert (att.filename, att.content_type, att.content) == (
        "report.txt",
        "text/plain",
        b"hello\x00world!",
    )
    assert att.created_at == T0
    assert att.created_by == 56070  # attachments carry no author in Znuny: the import user
    async with world.factory() as s:
        ci = (
            await s.execute(
                select(TiqoraCmdbCi).where(TiqoraCmdbCi.number == src.ci_rows[0].number)
            )
        ).scalar_one()
    assert att.ci_id == ci.id

    steps = ctx.report.steps["attachments"]
    assert (steps.created, steps.unchanged, steps.skipped) == (1, 0, 1)
    assert [(i.step, i.code, i.object_ref) for i in ctx.report.issues] == [
        ("attachments", "attachment_in_filesystem", "CI 2, dump.bin")
    ]
    assert await _map_count(world) == 1


async def test_rerun_is_unchanged_and_loads_no_content(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run(world, src, maps)
    calls: list[bool] = []
    original = src.attachments

    async def spy(ci_ids, *, with_data):  # type: ignore[no-untyped-def]
        calls.append(with_data)
        return await original(ci_ids, with_data=with_data)

    src.attachments = spy  # type: ignore[method-assign]
    again = await _run(world, src, maps)
    assert len(await _stored(world)) == 1
    steps = again.report.steps["attachments"]
    assert (steps.created, steps.updated, steps.unchanged) == (0, 0, 1)
    # the FS file is not mapped, so its CI is asked for data again; the DB file's CI is not
    assert calls.count(True) == 1


async def test_changed_attachment_is_updated(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run(world, src, maps)
    src.attachment_rows[0] = ZnunyAttachment(
        1, "report.txt", "text/plain", 3, b"new", T0 + timedelta(days=2)
    )
    again = await _run(world, src, maps)
    steps = again.report.steps["attachments"]
    assert (steps.created, steps.updated) == (0, 1)
    assert sorted(a.content for a in await _stored(world)) == [b"hello\x00world!", b"new"]
    assert await _map_count(world) == 1


async def test_attachment_of_unmapped_ci_is_skipped(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    # a CI the CI passes did not import (e.g. its class had no definition): no map entry
    src.ci_rows.append(dataclasses.replace(src.ci_rows[0], id=11, number="2026101256011"))
    src.attachment_rows.append(ZnunyAttachment(11, "orphan.txt", "text/plain", 1, b"x", T0))
    ctx = await _run(world, src, maps)
    steps = ctx.report.steps["attachments"]
    assert (steps.created, steps.skipped) == (1, 2)  # the FS file and the unmapped CI's file
    assert [i.code for i in ctx.report.issues] == ["attachment_in_filesystem"]
    assert [a.filename for a in await _stored(world)] == ["report.txt"]


async def test_oversized_attachment_is_reported_not_read(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    big = source_extra.MAX_ATTACHMENT_BYTES + 1
    src.attachment_rows.append(ZnunyAttachment(3, "huge.iso", "application/x-iso", big, None, T0))
    wanted_data: list[list[int]] = []
    original = src.attachments

    async def spy(ci_ids, *, with_data):  # type: ignore[no-untyped-def]
        if with_data:
            wanted_data.append(list(ci_ids))
        return await original(ci_ids, with_data=with_data)

    src.attachments = spy  # type: ignore[method-assign]
    ctx = await _run(world, src, maps)
    too_large = [i for i in ctx.report.issues if i.code == "attachment_too_large"]
    assert [(i.object_ref, i.step) for i in too_large] == [("CI 3, huge.iso", "attachments")]
    assert "51 MB" in too_large[0].message and "50 MB" in too_large[0].message
    assert [3] not in wanted_data  # CI 3 has nothing else to import: no content read
    assert len(await _stored(world)) == 1


async def test_duplicate_content_after_lost_map_row_is_not_doubled(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src)
    await _run(world, src, maps)
    async with world.factory() as s:
        await s.execute(
            TiqoraItsmImportMap.__table__.delete().where(
                TiqoraItsmImportMap.source_kind == KIND_ATTACHMENT
            )
        )
        await s.commit()
    await _run(world, src, maps)
    assert len(await _stored(world)) == 1  # cmdb's name + SHA-256 idempotency
    assert await _map_count(world) == 1


async def test_dry_run_counts_like_real_run_and_writes_nothing(world: _World) -> None:
    src = sample_source()
    maps = await _import_cis(world, src, dry_run=True)
    ctx = await _run(world, src, maps, dry_run=True)
    steps = ctx.report.steps["attachments"]
    assert (steps.created, steps.skipped) == (1, 1)
    assert [i.code for i in ctx.report.issues] == ["attachment_in_filesystem"]
    assert await _stored(world) == []
    assert await _map_count(world) == 0


def test_map_key_is_short_and_stable_for_long_names() -> None:
    long = ZnunyAttachment(7, "ä" * 400, "text/plain", 1, None, T0)
    assert map_key(long) == map_key(long)
    assert len(map_key(long)) < 255
    assert map_key(long) != map_key(ZnunyAttachment(7, "b", "text/plain", 1, None, T0))
