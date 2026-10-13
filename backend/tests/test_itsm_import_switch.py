"""Switch A->B, rollback, drift and status of the ITSM importer (Task 14; band 56330-56369).

The runs use the main test databases (``world``): the FakeSource replaces Znuny, the CMDB rows
are real and wiped afterwards. The stored ``itsm.mode`` row ("znuny_readthrough") stands in for a
site that still keeps its ITSM data in Znuny.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import sample_source
from tests._itsm_import_job_helpers import call_handler, wipe
from tiqora.api.v1.cmdb.deps import cmdb_http_error
from tiqora.cmdb.gate import CmdbUnavailable, require_tiqora_cmdb, reset_mode_cache
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraItsmImportRun, TiqoraJob, TiqoraSettings
from tiqora.itsm.importer import facades, jobs
from tiqora.itsm.importer.jobs import (
    IMPORT_DEDUPE_KEY,
    IMPORT_JOB_KIND,
    SWITCH_JOB_KIND,
    ItsmImportPayload,
    ItsmSwitchPayload,
    bind_run_to_job,
    start_run,
)
from tiqora.itsm.importer.switch import (
    ALREADY_SWITCHED,
    MODE_KEY,
    READONLY_KEY,
    SWITCH_KEY,
    SwitchBlocked,
    check_can_import,
    check_can_switch,
    import_status,
    rollback,
)
from tiqora.jobs.context import JobFailed
from tiqora.jobs.service import enqueue_job, utc_now

pytestmark = pytest.mark.db


@dataclass
class _World:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    wipe(sync_url)
    reset_mode_cache()
    engine = create_async_engine(to_async_url(sync_url), poolclass=NullPool)
    try:
        yield _World(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), sync_url
        )
    finally:
        facades.source_override = None
        facades.znuny_present_override = None
        reset_mode_cache()
        wipe(sync_url)


def _sync_setting(sync_url: str, key: str) -> str | None:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            from sqlalchemy import text

            quoted = "`key`" if conn.dialect.name == "mysql" else "key"
            row = conn.execute(
                text(f"SELECT value FROM tiqora_settings WHERE {quoted} = :k"), {"k": key}
            ).first()
            return None if row is None else str(row[0])
    finally:
        engine.dispose()


async def _put(w: _World, key: str, value: str) -> None:
    async with w.factory() as s:
        row = await s.get(TiqoraSettings, key)
        if row is None:
            s.add(TiqoraSettings(key=key, value=value))
        else:
            row.value = value
        await s.commit()


async def _get(w: _World, key: str) -> str | None:
    async with w.factory() as s:
        row = await s.get(TiqoraSettings, key)
        return row.value if row is not None else None


async def _mode_a(w: _World, monkeypatch: pytest.MonkeyPatch, **knobs: object) -> object:
    """A site that still keeps its ITSM data in Znuny, with a fake Znuny source."""
    source = sample_source(**knobs)
    monkeypatch.setattr(facades, "source_override", source)
    monkeypatch.setattr(facades, "znuny_present_override", True)
    await _put(w, MODE_KEY, "znuny_readthrough")
    reset_mode_cache()
    return source


async def _done_import(w: _World, user_id: int) -> int:
    async with w.factory() as s:
        now = utc_now()
        run = TiqoraItsmImportRun(
            kind="import",
            status="done",
            job_id=None,
            started_by=user_id,
            started_at=now,
            finished_at=now,
            profile=None,
            summary="{}",
            message=None,
        )
        s.add(run)
        await s.commit()
        return run.id


async def _run_switch(
    w: _World,
    *,
    user_id: int,
    addon_installed: bool,
    edits_stopped: bool = False,
    without_ci_data: bool = False,
) -> int:
    """Create the switch run + job the way the API does and run the handler directly."""
    async with w.factory() as s:
        run = await start_run(s, kind="switch", user_id=user_id)
        payload = ItsmSwitchPayload(
            user_id=user_id,
            run_id=run.id,
            addon_installed=addon_installed,
            edits_stopped=edits_stopped,
            without_ci_data=without_ci_data,
        )
        job = await enqueue_job(
            s,
            kind=SWITCH_JOB_KIND,
            payload=payload,
            created_by=user_id,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        await bind_run_to_job(s, run, job)
        run_id, job_id = run.id, job.id
        await s.commit()
    try:
        await call_handler(w.factory, job_id, jobs.run_itsm_switch_job, payload, SWITCH_JOB_KIND)
    finally:  # the runner would close the job; a direct handler call does not
        async with w.factory() as s:
            await s.execute(update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="failed"))
            await s.commit()
    return run_id


async def _run_row(w: _World, run_id: int) -> TiqoraItsmImportRun:
    async with w.factory() as s:
        return (
            await s.execute(select(TiqoraItsmImportRun).where(TiqoraItsmImportRun.id == run_id))
        ).scalar_one()


async def _ci_count(w: _World) -> int:
    async with w.factory() as s:
        return int((await s.execute(select(func.count()).select_from(TiqoraCmdbCi))).scalar_one())


async def test_switch_sets_readonly_before_final_import(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []
    await _mode_a(
        world,
        monkeypatch,
        on_first_page=lambda: seen.append(_sync_setting(world.sync_url, READONLY_KEY)),
    )
    await _done_import(world, 56330)
    run_id = await _run_switch(world, user_id=56330, addon_installed=True)
    assert seen[-1] == "1"  # the flag was set before the import read data
    assert await _get(world, MODE_KEY) == "tiqora"
    switch = json.loads(await _get(world, SWITCH_KEY) or "{}")
    assert switch["by"] == 56330 and switch["run_id"] == run_id
    assert await _get(world, READONLY_KEY) == "1"  # stays set after a successful switch
    run = await _run_row(world, run_id)
    assert run.kind == "switch" and run.status == "done" and run.message is None
    assert await _ci_count(world) == 10  # the final import ran


async def test_switch_failure_clears_readonly_and_keeps_mode(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch, fail_once_after_pages=0)
    await _done_import(world, 56331)
    with pytest.raises(RuntimeError, match="worker died"):
        await _run_switch(world, user_id=56331, addon_installed=True)
    assert await _get(world, READONLY_KEY) == "0"
    assert await _get(world, MODE_KEY) == "znuny_readthrough"
    assert await _get(world, SWITCH_KEY) is None
    async with world.factory() as s:
        run = (
            await s.execute(
                select(TiqoraItsmImportRun)
                .where(TiqoraItsmImportRun.started_by == 56331)
                .order_by(TiqoraItsmImportRun.id.desc())
                .limit(1)
            )
        ).scalar_one()
    assert run.kind == "switch" and run.status == "failed"
    assert "worker died" in (run.message or "")


async def test_switch_requires_successful_import(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch)
    async with world.factory() as s:
        with pytest.raises(SwitchBlocked, match="erfolgreichen Import"):
            await check_can_switch(s, addon_installed=True, edits_stopped=False)
    with pytest.raises(JobFailed, match="erfolgreichen Import"):
        await _run_switch(world, user_id=56332, addon_installed=True)
    assert await _get(world, READONLY_KEY) in (None, "0")  # never set: the check failed first
    assert await _get(world, MODE_KEY) == "znuny_readthrough"
    assert await _ci_count(world) == 0


async def test_switch_without_ci_data_needs_no_import(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch, profile=None)
    run_id = await _run_switch(world, user_id=56333, addon_installed=True, without_ci_data=True)
    assert (await _run_row(world, run_id)).status == "done"
    assert await _get(world, MODE_KEY) == "tiqora"
    assert await _ci_count(world) == 0


async def test_switch_without_addon_requires_edits_stopped(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch)
    await _done_import(world, 56334)
    async with world.factory() as s:
        with pytest.raises(SwitchBlocked, match="bearbeitet"):
            await check_can_switch(s, addon_installed=False, edits_stopped=False)
        await check_can_switch(s, addon_installed=False, edits_stopped=True)
        await check_can_switch(s, addon_installed=True, edits_stopped=False)
    with pytest.raises(JobFailed, match="bearbeitet"):
        await _run_switch(world, user_id=56334, addon_installed=False, edits_stopped=False)
    assert await _get(world, MODE_KEY) == "znuny_readthrough"
    assert await _get(world, READONLY_KEY) in (None, "0")


async def test_switch_refused_without_znuny_itsm_or_in_wrong_mode(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch)
    await _done_import(world, 56335)
    monkeypatch.setattr(facades, "znuny_present_override", False)
    async with world.factory() as s:
        with pytest.raises(SwitchBlocked, match="kein Znuny ITSM"):
            await check_can_switch(s, addon_installed=True, edits_stopped=True)
    monkeypatch.setattr(facades, "znuny_present_override", True)
    await _put(world, MODE_KEY, "off")
    async with world.factory() as s:
        with pytest.raises(SwitchBlocked, match="Znuny-Durchgriff"):
            await check_can_switch(s, addon_installed=True, edits_stopped=True)


async def test_import_refused_after_switch(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    await _mode_a(world, monkeypatch)
    await _done_import(world, 56336)
    async with world.factory() as s:
        await check_can_import(s)  # allowed before the switch
    await _run_switch(world, user_id=56336, addon_installed=True)
    async with world.factory() as s:
        with pytest.raises(SwitchBlocked) as info:
            await check_can_import(s)
        assert (
            info.value.message
            == ALREADY_SWITCHED
            == "Die ITSM-Daten werden bereits in Tiqora geführt."
        )
        with pytest.raises(SwitchBlocked, match="bereits in Tiqora"):
            await check_can_switch(s, addon_installed=True, edits_stopped=True)
    # and the import job itself refuses (run row failed)
    async with world.factory() as s:
        run = await start_run(s, kind="import", user_id=56336)
        payload = ItsmImportPayload(user_id=56336, dry_run=False, run_id=run.id, page_size=3)
        job = await enqueue_job(
            s, kind=IMPORT_JOB_KIND, payload=payload, created_by=56336, dedupe_key=IMPORT_DEDUPE_KEY
        )
        await bind_run_to_job(s, run, job)
        run_id, job_id = run.id, job.id
        await s.commit()
    with pytest.raises(JobFailed, match="bereits in Tiqora"):
        await call_handler(
            world.factory, job_id, jobs.run_itsm_import_job, payload, IMPORT_JOB_KIND
        )
    assert (await _run_row(world, run_id)).message == ALREADY_SWITCHED


async def test_rollback_restores_mode_a(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    await _mode_a(world, monkeypatch)
    await _done_import(world, 56337)
    await _run_switch(world, user_id=56337, addon_installed=True)
    assert await _get(world, READONLY_KEY) == "1"
    async with world.factory() as s:
        await rollback(s, user_id=56337)
        await s.commit()
    assert await _get(world, SWITCH_KEY) is None
    assert await _get(world, MODE_KEY) == "znuny_readthrough"
    assert await _get(world, READONLY_KEY) == "0"
    async with world.factory() as s:
        run = (
            await s.execute(
                select(TiqoraItsmImportRun).where(TiqoraItsmImportRun.kind == "rollback")
            )
        ).scalar_one()
        assert run.status == "done" and run.started_by == 56337
        meta = json.loads((await s.get(TiqoraSettings, "itsm.mode_meta")).value)  # type: ignore[union-attr]
        assert meta["import_run_id"] == run.id and meta["source"] == "importer"
        # a second rollback has nothing to roll back
        with pytest.raises(SwitchBlocked, match="nicht auf Tiqora umgestellt"):
            await rollback(s, user_id=56337)
        await check_can_import(s)  # imports are possible again


async def test_staged_cis_stay_invisible_in_mode_a(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L7: the importer stages rows before the switch; cmdb answers 409 until then."""
    await _mode_a(world, monkeypatch)
    await _done_import(world, 56338)
    async with world.factory() as s:
        run = await start_run(s, kind="import", user_id=56338)
        payload = ItsmImportPayload(user_id=56338, dry_run=False, run_id=run.id, page_size=3)
        job = await enqueue_job(
            s, kind=IMPORT_JOB_KIND, payload=payload, created_by=56338, dedupe_key=IMPORT_DEDUPE_KEY
        )
        await bind_run_to_job(s, run, job)
        job_id = job.id
        await s.commit()
    await call_handler(world.factory, job_id, jobs.run_itsm_import_job, payload, IMPORT_JOB_KIND)
    assert await _ci_count(world) == 10  # staged
    reset_mode_cache()
    async with world.factory() as s:
        with pytest.raises(CmdbUnavailable) as info:
            await require_tiqora_cmdb(s)
    err = cmdb_http_error(info.value)
    assert err.status_code == 409 and err.detail["code"] == "cmdb_in_znuny"  # type: ignore[index]


async def test_status_reports_drift_after_switch(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = await _mode_a(world, monkeypatch, changed_since_count=3)
    await _put(
        world,
        SWITCH_KEY,
        json.dumps({"switched_at": "2026-10-12T08:00:00", "by": 56339, "run_id": 1}),
    )
    await _put(world, MODE_KEY, "tiqora")
    reset_mode_cache()
    async with world.factory() as s:
        status = await import_status(s)
    assert status.znuny_changes_since_switch == 3
    assert src.changed_since_args == [datetime(2026, 10, 12, 8, 0)]  # type: ignore[attr-defined]
    assert status.can_import is False and status.can_switch is False
    assert status.blocked_reason == ALREADY_SWITCHED
    assert status.switched is not None and status.switched.by == 56339
    assert status.mode == "tiqora"
    assert status.znuny_counts is not None and status.znuny_counts["cis"] == 10


async def test_status_before_switch_and_with_stuck_flag(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = await _mode_a(world, monkeypatch)
    async with world.factory() as s:
        status = await import_status(s)
    assert status.can_import is True and status.blocked_reason is None
    assert status.can_switch is False  # no successful import yet
    assert status.znuny_changes_since_switch is None and status.switched is None
    assert src.changed_since_args == []  # type: ignore[attr-defined]
    await _done_import(world, 56340)
    await _put(world, READONLY_KEY, "1")  # a flag without a switch and without a job
    async with world.factory() as s:
        stuck = await import_status(s)
    assert stuck.readonly_flag is True and stuck.can_import is False and stuck.can_switch is False
    assert "Sperre" in (stuck.blocked_reason or "")
    await _put(world, READONLY_KEY, "0")
    async with world.factory() as s:
        ready = await import_status(s)
    assert ready.can_switch is True and ready.last_run is not None
    assert ready.last_run.kind == "import"


async def test_status_without_znuny_itsm_reads_nothing(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(world, monkeypatch)
    monkeypatch.setattr(facades, "znuny_present_override", False)
    async with world.factory() as s:
        status = await import_status(s)
    assert status.znuny_itsm_present is False and status.can_import is False
    assert status.blocked_reason == "In der Datenbank wurde kein Znuny ITSM gefunden."
    assert status.znuny_counts is None and status.profile is None
