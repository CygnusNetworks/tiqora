"""CMDB version archive, job and daily tick (both dialects, band 745500-745999)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    USER_A,
    CmdbWorld,
    make_ci,
    make_class,
    run_jobs_until_idle,
)
from tiqora.cmdb.access import load_access
from tiqora.cmdb.archive import CMDB_ARCHIVE_JOB, archive_old_versions
from tiqora.cmdb.diff import diff_versions
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
    TiqoraCmdbCiVersionArchive,
    TiqoraJob,
)
from tiqora.extensions import get_registry
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import utc_now
from tiqora.worker import cmdb_version_archive as worker_mod

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


async def _ci_with_versions(world: CmdbWorld, n: int = 8, *, backdate_first: int = 7) -> int:
    """A CI with ``n`` versions (names ``v1`` .. ``vN``); the oldest ``backdate_first`` are
    moved to 400 days ago. Returns the CI id."""
    async with world.factory() as s:
        cid = await make_class(s)
        ci = await make_ci(s, cid, "cmdb4-v1")
        for i in range(2, n + 1):
            await CiService(s).update(
                system_access(USER_A),
                ci,
                expected_lock_version=None,
                name=f"cmdb4-v{i}",
                enforce_permissions=False,
            )
        await s.commit()
        ids = (
            (
                await s.execute(
                    select(TiqoraCmdbCiVersion.id)
                    .where(TiqoraCmdbCiVersion.ci_id == ci)
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(ids) == n
    old = utc_now() - timedelta(days=400)
    for vid in ids[:backdate_first]:
        world.sql(
            "UPDATE tiqora_cmdb_ci_version SET created_at = :t WHERE id = :i", t=old, i=int(vid)
        )
    return ci


async def _split(world: CmdbWorld, ci: int) -> tuple[list[int], list[int]]:
    async with world.factory() as s:
        hot = (
            (
                await s.execute(
                    select(TiqoraCmdbCiVersion.id)
                    .where(TiqoraCmdbCiVersion.ci_id == ci)
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )
        arch = (
            (
                await s.execute(
                    select(TiqoraCmdbCiVersionArchive.id)
                    .where(TiqoraCmdbCiVersionArchive.ci_id == ci)
                    .order_by(TiqoraCmdbCiVersionArchive.id)
                )
            )
            .scalars()
            .all()
        )
    return [int(i) for i in hot], [int(i) for i in arch]


async def test_archive_moves_only_old_versions_beyond_keep(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db)
    all_ids = sum(await _split(cmdb_db, ci), [])
    moved = await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365)
    hot, arch = await _split(cmdb_db, ci)
    ordered = sorted(all_ids)
    assert moved == 5
    assert arch == ordered[:5]
    assert hot == ordered[5:]
    async with cmdb_db.factory() as s:
        row = (await s.execute(select(TiqoraCmdbCi).where(TiqoraCmdbCi.id == ci))).scalar_one()
        # the current version is the newest one and stays hot
        assert int(row.current_version_id or 0) == ordered[-1]
        assert ordered[-1] in hot
        # the archive keeps the original id and stamps the move
        stamped = (await s.execute(select(TiqoraCmdbCiVersionArchive.archived_at))).scalars().all()
        assert len(stamped) == 5 and all(t is not None for t in stamped)


async def test_young_versions_stay_hot_even_beyond_keep(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db, 8, backdate_first=2)
    moved = await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365)
    hot, arch = await _split(cmdb_db, ci)
    assert moved == 2
    assert len(arch) == 2 and len(hot) == 6


async def test_archived_versions_still_readable_and_paged(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db)
    await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365)
    async with cmdb_db.factory() as s:
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        hot_only = await svc.list_versions(acc, ci, include_archived=False)
        assert [v.archived for v in hot_only.items] == [False] * 3
        assert hot_only.archived_count == 5
        page = await svc.list_versions(acc, ci, include_archived=True)
        assert len(page.items) == 8
        assert [v.archived for v in page.items] == [False] * 3 + [True] * 5
        ids = [v.id for v in page.items]
        assert ids == sorted(ids, reverse=True)
        first = await svc.list_versions(acc, ci, limit=4, include_archived=True)
        assert first.has_more and len(first.items) == 4
        rest = await svc.list_versions(
            acc, ci, before_id=first.items[-1].id, limit=10, include_archived=True
        )
        assert [v.id for v in first.items + rest.items] == ids
        oldest = await svc.get_version(acc, ci, ids[-1])
        assert oldest.archived and oldest.name == "cmdb4-v1"


async def test_archive_is_idempotent_and_batched(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db)
    seen: list[int] = []

    async def progress(done: int, total: int | None) -> None:
        seen.append(done)

    # batch=2 forces several transactions for the five movable versions
    assert (
        await archive_old_versions(
            cmdb_db.factory, keep=3, min_age_days=365, batch=2, progress=progress
        )
        == 5
    )
    assert seen and seen[-1] >= 1
    assert await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365, batch=2) == 0
    hot, arch = await _split(cmdb_db, ci)
    assert len(hot) == 3 and len(arch) == 5
    # a hot row that is already archived (interrupted copy) is only dropped, not duplicated
    async with cmdb_db.factory() as s:
        v = (
            (
                await s.execute(
                    select(TiqoraCmdbCiVersionArchive).order_by(TiqoraCmdbCiVersionArchive.id)
                )
            )
            .scalars()
            .first()
        )
        assert v is not None
        s.add(
            TiqoraCmdbCiVersion(
                id=v.id,
                ci_id=v.ci_id,
                definition_id=v.definition_id,
                name=v.name,
                deployment_state_id=v.deployment_state_id,
                incident_state_id=v.incident_state_id,
                attributes=v.attributes,
                change_note=v.change_note,
                created_by=v.created_by,
                created_at=v.created_at,
            )
        )
        await s.commit()
    assert await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365) == 1
    async with cmdb_db.factory() as s:
        n = (
            await s.execute(select(func.count()).select_from(TiqoraCmdbCiVersionArchive))
        ).scalar_one()
    assert n == 5


async def test_keep_larger_than_version_count_moves_nothing(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db)
    assert await archive_old_versions(cmdb_db.factory, keep=8, min_age_days=30) == 0
    hot, arch = await _split(cmdb_db, ci)
    assert len(hot) == 8 and arch == []


async def test_tick_does_nothing_when_disabled_but_purges_staged(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "get_session_factory", lambda: cmdb_db.factory)
    old = utc_now() - timedelta(days=3)
    async with cmdb_db.factory() as s:
        for name, created in (("old.txt", old), ("fresh.txt", utc_now())):
            s.add(
                TiqoraCmdbAttachment(
                    ci_id=None,
                    filename=name,
                    content_type="text/plain",
                    size_bytes=1,
                    sha256="0" * 64,
                    content=b"x",
                    created_by=USER_A,
                    created_at=created,
                )
            )
        await s.commit()
    result = await worker_mod.run_cmdb_version_archive_tick()
    assert result == {"purged_staged": 1, "enqueued": 0}
    async with cmdb_db.factory() as s:
        names = (await s.execute(select(TiqoraCmdbAttachment.filename))).scalars().all()
        jobs = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraJob)
                .where(TiqoraJob.kind == "core.cmdb_archive")
            )
        ).scalar_one()
    assert list(names) == ["fresh.txt"]
    assert jobs == 0


async def test_tick_enqueues_job_once_and_job_archives(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_mod, "get_session_factory", lambda: cmdb_db.factory)
    assert get_registry().get(JOB_HANDLERS, "core.cmdb_archive") is CMDB_ARCHIVE_JOB
    ci = await _ci_with_versions(cmdb_db)
    key_col = "`key`" if cmdb_db.dialect == "mysql" else '"key"'
    for key, value in (("version_archive_enabled", "1"), ("version_archive_keep", "5")):
        cmdb_db.sql(
            f"INSERT INTO tiqora_settings ({key_col}, value) VALUES (:k, :v)",
            k=f"cmdb.{key}",
            v=value,
        )
    first = await worker_mod.run_cmdb_version_archive_tick()
    second = await worker_mod.run_cmdb_version_archive_tick()
    assert first["enqueued"] == 1 and second["enqueued"] == 1
    async with cmdb_db.factory() as s:
        jobs = (
            await s.execute(
                select(func.count())
                .select_from(TiqoraJob)
                .where(TiqoraJob.kind == "core.cmdb_archive")
            )
        ).scalar_one()
    assert jobs == 1  # dedupe key "daily"
    await run_jobs_until_idle(cmdb_db)
    hot, arch = await _split(cmdb_db, ci)
    assert len(hot) == 5 and len(arch) == 3
    async with cmdb_db.factory() as s:
        job = (
            await s.execute(select(TiqoraJob).where(TiqoraJob.kind == "core.cmdb_archive"))
        ).scalar_one()
    assert job.status == "succeeded"


async def test_diff_across_archived_version(cmdb_db: CmdbWorld) -> None:
    ci = await _ci_with_versions(cmdb_db)
    hot, _ = await _split(cmdb_db, ci)
    first_id = min(hot)
    await archive_old_versions(cmdb_db.factory, keep=3, min_age_days=365)
    _, arch = await _split(cmdb_db, ci)
    assert first_id in arch
    async with cmdb_db.factory() as s:
        acc = await load_access(s, USER_A)
        current = max(hot)
        rows = await diff_versions(s, acc, ci, first_id, current)
    name = next(r for r in rows if r.path == "name")
    assert (name.kind, name.old, name.new) == ("changed", "cmdb4-v1", "cmdb4-v8")
