"""Importer job handlers and run records (Task 13; band 56300-56329).

The runs use the main test databases (``world``): the FakeSource replaces Znuny, the CMDB rows
are real and wiped afterwards. A ``RuntimeError`` mid-import tests idempotency of a repeated
claim, not the requeue itself (the runner only requeues interrupted jobs; that is covered by the
runner's own tests).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests import _gi_mail_jobs
from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import sample_source
from tests._itsm_import_job_helpers import call_handler, enqueue_import, make_ctx, wipe
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
    TiqoraItsmImportMap,
    TiqoraItsmImportRun,
    TiqoraItsmLink,
    TiqoraJob,
    TiqoraJobFile,
    TiqoraSettings,
)
from tiqora.extensions import get_registry
from tiqora.itsm.importer import facades, jobs
from tiqora.itsm.importer.jobs import (
    IMPORT_DEDUPE_KEY,
    IMPORT_JOB_KIND,
    ITSM_IMPORT_JOB,
    ITSM_SWITCH_JOB,
    SWITCH_JOB_KIND,
    ImportBusy,
    ItsmImportPayload,
    ItsmSwitchPayload,
    bind_run_to_job,
    finish_run,
    start_run,
)
from tiqora.itsm.importer.pipeline import STEP_ORDER
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.switch import ALREADY_SWITCHED
from tiqora.itsm.mode import SETTING_SWITCH, SETTING_ZNUNY_READONLY
from tiqora.jobs.context import JobCancelled, JobFailed
from tiqora.jobs.registry import JOB_HANDLERS, lane_slots
from tiqora.jobs.service import enqueue_job

pytestmark = pytest.mark.db


@dataclass
class _World:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    wipe(sync_url)
    engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _World(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), sync_url
        )
    finally:
        facades.source_override = None
        wipe(sync_url)


async def _run(w: _World, run_id: int) -> TiqoraItsmImportRun:
    async with w.factory() as s:
        return (
            await s.execute(select(TiqoraItsmImportRun).where(TiqoraItsmImportRun.id == run_id))
        ).scalar_one()


async def _counts(w: _World) -> tuple[int, ...]:
    async with w.factory() as s:
        counts: list[int] = []
        for model in (
            TiqoraCmdbCi,
            TiqoraCmdbCiVersion,
            TiqoraItsmLink,
            TiqoraCmdbAttachment,
            TiqoraItsmImportMap,
        ):
            counts.append(
                int((await s.execute(select(func.count()).select_from(model))).scalar_one())
            )
        return tuple(counts)


async def _job_files(w: _World, job_id: int) -> list[TiqoraJobFile]:
    async with w.factory() as s:
        rows = await s.execute(
            select(TiqoraJobFile).where(TiqoraJobFile.job_id == job_id).order_by(TiqoraJobFile.id)
        )
        return list(rows.scalars())


async def _payload(w: _World, job_id: int) -> object:
    async with w.factory() as s:
        raw = (
            await s.execute(select(TiqoraJob.payload).where(TiqoraJob.id == job_id))
        ).scalar_one()
    return ITSM_IMPORT_JOB.payload_model.model_validate_json(raw)


async def _call(w: _World, job_id: int) -> object:
    return await call_handler(
        w.factory, job_id, jobs.run_itsm_import_job, await _payload(w, job_id), IMPORT_JOB_KIND
    )


async def _set_setting(w: _World, key: str, value: str) -> None:
    async with w.factory() as s:
        row = await s.get(TiqoraSettings, key)
        if row is None:
            s.add(TiqoraSettings(key=key, value=value))
        else:
            row.value = value
        await s.commit()


async def _setting(w: _World, key: str) -> str | None:
    async with w.factory() as s:
        row = await s.get(TiqoraSettings, key)
        return row.value if row is not None else None


async def test_import_job_writes_run_summary_and_csv(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    monkeypatch.setattr(_gi_mail_jobs, "GI_MAIL_KIND", IMPORT_JOB_KIND)
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56300)
    done = await _gi_mail_jobs.drain_with_factory(world.factory)
    assert done == [{"id": job_id, "status": "succeeded", "error": None}]
    run = await _run(world, run_id)
    assert run.status == "done" and run.job_id == job_id and run.kind == "import"
    assert run.finished_at is not None and run.message is None
    summary = json.loads(run.summary)
    assert summary["steps"]["cis_pass2"]["created"] > 0
    assert set(STEP_ORDER) <= set(summary["steps"])
    assert summary["issue_count"] >= 1  # the link to an unsupported FAQ object
    files = await _job_files(world, job_id)
    assert [f.name for f in files] == ["itsm-import-bericht.csv"]
    assert files[0].content_type == "text/csv; charset=utf-8"
    assert (await _counts(world))[0] == 10


async def test_dry_run_records_run_and_writes_no_cis(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    run_id, job_id = await enqueue_import(world.factory, dry_run=True, user_id=56301)
    await _call(world, job_id)
    run = await _run(world, run_id)
    assert run.kind == "dry_run" and run.status == "done"
    assert json.loads(run.summary)["steps"]["cis_pass2"]["created"] > 0
    assert (await _counts(world))[0] == 0


async def test_requeued_job_resumes_without_duplicates(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    _, clean_job = await enqueue_import(world.factory, dry_run=False, user_id=56302)
    await _call(world, clean_job)
    clean = await _counts(world)
    assert clean[0] == 10 and clean[1] > 10
    wipe(world.sync_url)

    monkeypatch.setattr(facades, "source_override", sample_source(fail_once_after_pages=1))
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56303)
    with pytest.raises(RuntimeError, match="worker died"):
        await _call(world, job_id)  # first claim dies after page 1 was committed
    failed = await _run(world, run_id)
    assert failed.status == "failed" and "worker died" in (failed.message or "")
    assert 0 < (await _counts(world))[0] < 10
    await _call(world, job_id)  # the same payload / run id, claimed again
    assert await _counts(world) == clean  # no duplicate CIs, versions, links, attachments
    done = await _run(world, run_id)
    assert done.status == "done" and done.message is None


async def test_second_start_is_refused_while_active(world: _World) -> None:
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56304)
    async with world.factory() as s:
        again = await enqueue_job(
            s,
            kind=IMPORT_JOB_KIND,
            payload=await _payload(world, job_id),  # type: ignore[arg-type]
            created_by=56304,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        assert again.id == job_id  # the dedupe key joins the active job
        for kind in ("dry_run", "import", "switch"):
            with pytest.raises(ImportBusy):
                await start_run(s, kind=kind, user_id=56304)  # type: ignore[arg-type]
        await s.rollback()
    # a running switch job blocks as well
    async with world.factory() as s:
        await s.execute(update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="succeeded"))
        await enqueue_job(
            s,
            kind=SWITCH_JOB_KIND,
            payload=ItsmSwitchPayload(user_id=56304, run_id=run_id, addon_installed=False),
            created_by=56304,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        await s.commit()
    async with world.factory() as s:
        with pytest.raises(ImportBusy):
            await start_run(s, kind="import", user_id=56304)
        await s.rollback()
    # once nothing is active a new run can start
    async with world.factory() as s:
        await s.execute(
            update(TiqoraJob).where(TiqoraJob.created_by == 56304).values(status="failed")
        )
        run = await start_run(s, kind="import", user_id=56304)
        assert run.id > run_id
        await s.commit()


@pytest.mark.parametrize("read_first", [True, False])
async def test_concurrent_starts_are_serialised(world: _World, read_first: bool) -> None:
    """The second start waits for the first and is refused, even when its session read
    something first (MariaDB REPEATABLE READ snapshot) and no settings row exists yet."""
    async with world.factory() as a, world.factory() as b:
        if read_first:  # what the CLI/API do: a precondition read before start_run
            await b.execute(select(func.count()).select_from(TiqoraSettings))
        run = await start_run(a, kind="import", user_id=56312)
        job = await enqueue_job(
            a,
            kind=IMPORT_JOB_KIND,
            payload=ItsmImportPayload(user_id=56312, dry_run=False, run_id=run.id),
            created_by=56312,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        await bind_run_to_job(a, run, job)
        second = asyncio.ensure_future(start_run(b, kind="import", user_id=56313))
        await asyncio.sleep(0.5)
        assert not second.done()  # waits on A's lock
        await a.commit()
        with pytest.raises(ImportBusy):
            await second
        await b.rollback()
    async with world.factory() as s:
        runs = (await s.execute(select(TiqoraItsmImportRun.id))).scalars().all()
        jobs_n = (
            await s.execute(
                select(func.count()).select_from(TiqoraJob).where(TiqoraJob.kind == IMPORT_JOB_KIND)
            )
        ).scalar_one()
    assert runs == [run.id] and jobs_n == 1


async def test_deduped_job_of_another_run_is_refused(world: _World) -> None:
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56314)
    async with world.factory() as s:
        orphan = TiqoraItsmImportRun(
            kind="import",
            status="running",
            job_id=None,
            started_by=56314,
            started_at=datetime(2026, 1, 1),
            finished_at=None,
            profile=None,
            summary="{}",
            message=None,
        )
        s.add(orphan)
        await s.flush()
        job = await enqueue_job(
            s,
            kind=IMPORT_JOB_KIND,
            payload=ItsmImportPayload(user_id=56314, dry_run=False, run_id=orphan.id),
            created_by=56314,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        assert job.id == job_id  # joined the first job
        with pytest.raises(ImportBusy):
            await bind_run_to_job(s, orphan, job)
        await s.commit()
    async with world.factory() as s:
        ids = (await s.execute(select(TiqoraItsmImportRun.id))).scalars().all()
    assert ids == [run_id]


async def test_failed_run_records_message(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source(profile=None))
    monkeypatch.setattr(_gi_mail_jobs, "GI_MAIL_KIND", IMPORT_JOB_KIND)
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56305)
    done = await _gi_mail_jobs.drain_with_factory(world.factory)
    assert [d["status"] for d in done] == ["failed"]
    run = await _run(world, run_id)
    assert run.status == "failed"
    assert run.message == "In der Znuny-Datenbank wurde kein ITSM gefunden."
    assert done[0]["error"] == run.message
    assert (await _counts(world))[0] == 0
    assert job_id == run.job_id


async def test_cancelled_job_marks_run_cancelled(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56306)
    async with world.factory() as s:
        await s.execute(
            update(TiqoraJob).where(TiqoraJob.id == job_id).values(cancel_requested=True)
        )
        await s.commit()
    with pytest.raises(JobCancelled):
        await _call(world, job_id)
    run = await _run(world, run_id)
    assert run.status == "cancelled" and run.finished_at is not None


async def test_import_refused_after_switch(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    await _set_setting(world, SETTING_SWITCH, json.dumps({"switched_at": "2026-10-13T08:00:00"}))
    run_id, job_id = await enqueue_import(world.factory, dry_run=False, user_id=56307)
    with pytest.raises(JobFailed, match="bereits in Tiqora geführt"):
        await _call(world, job_id)
    run = await _run(world, run_id)
    assert run.status == "failed" and run.message == ALREADY_SWITCHED
    assert (await _counts(world))[0] == 0


async def test_finish_run_stores_summary_and_truncates_message(world: _World) -> None:
    async with world.factory() as s:
        run = await start_run(s, kind="import", user_id=56308)
        await s.commit()
        run_id = run.id
    report = ImportReport()
    report.count("catalog", "created", 2)
    report.issue("links", "link_target_unsupported", "FAQ:5", "x")
    await finish_run(world.factory, run_id, status="done", report=report, message="m" * 900)
    row = await _run(world, run_id)
    assert row.status == "done" and isinstance(row.finished_at, datetime)
    assert row.message == "m" * 500
    summary = json.loads(row.summary)
    assert summary["steps"]["catalog"]["created"] == 2
    assert summary["issues"][0]["code"] == "link_target_unsupported"


async def test_interrupted_switch_clears_readonly_flag(world: _World) -> None:
    async with world.factory() as s:
        run = await start_run(s, kind="switch", user_id=56310)
        await s.commit()
        run_id = run.id
    await _set_setting(world, SETTING_ZNUNY_READONLY, "1")
    payload = ItsmSwitchPayload(user_id=56310, run_id=run_id, addon_installed=False)
    assert ITSM_SWITCH_JOB.on_interrupted is not None
    await ITSM_SWITCH_JOB.on_interrupted(
        make_ctx(world.factory, 0, SWITCH_JOB_KIND, 56310), payload
    )
    assert await _setting(world, SETTING_ZNUNY_READONLY) == "0"
    run_row = await _run(world, run_id)
    assert run_row.status == "failed" and "unterbrochen" in (run_row.message or "")


async def test_interrupted_switch_keeps_flag_after_a_successful_switch(world: _World) -> None:
    async with world.factory() as s:
        run = await start_run(s, kind="switch", user_id=56311)
        await s.commit()
        run_id = run.id
    await _set_setting(world, SETTING_ZNUNY_READONLY, "1")
    await _set_setting(world, SETTING_SWITCH, json.dumps({"switched_at": "2026-10-13T08:00:00"}))
    payload = ItsmSwitchPayload(user_id=56311, run_id=run_id, addon_installed=False)
    assert ITSM_SWITCH_JOB.on_interrupted is not None
    await ITSM_SWITCH_JOB.on_interrupted(
        make_ctx(world.factory, 0, SWITCH_JOB_KIND, 56311), payload
    )
    assert await _setting(world, SETTING_ZNUNY_READONLY) == "1"


def test_job_kinds_are_registered_in_their_own_lane() -> None:
    registry = get_registry()
    for handler, kind in (
        (ITSM_IMPORT_JOB, "core.itsm_import"),
        (ITSM_SWITCH_JOB, "core.itsm_switch"),
    ):
        assert registry.get(JOB_HANDLERS, kind) is handler
        assert handler.title_key == f"jobs.kinds.{kind}"
        assert handler.lane == "itsm_import" and lane_slots(handler.lane, 4) == 1
        assert handler.max_attempts == 3 and handler.requeue_if_interrupted
