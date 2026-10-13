"""Job handlers and run records of the Znuny ITSM importer (kinds ``core.itsm_import`` and
``core.itsm_switch``; plan D6).

Every start (API or CLI) first creates the ``tiqora_itsm_import_run`` row (:func:`start_run`),
then enqueues the job with the run id in its payload. The handler only moves that row through
``running`` -> ``done`` / ``failed`` / ``cancelled``; it never calls ``ctx.checkpoint`` because
every importer step is idempotent (a requeued claim resumes without duplicates). CIs whose run
ended before ``incident_states`` stay marked unfinished in the map (kind ``finish``) and are
finished by the next run, requeued or new.

Import and switch share the dedupe key :data:`IMPORT_DEDUPE_KEY` and the lane ``itsm_import``
(one slot): they never run at the same time, and a second start is refused with
:class:`ImportBusy`.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraItsmImportRun, TiqoraJob, TiqoraSettings
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.pipeline import ImportBlocked, run_import
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.switch import (
    ALREADY_SWITCHED as ALREADY_SWITCHED,
)
from tiqora.itsm.importer.switch import (
    SwitchBlocked,
    check_can_import,
    check_can_switch,
)
from tiqora.itsm.mode import SETTING_ZNUNY_READONLY
from tiqora.jobs.context import JobCancelled, JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import find_active_job, utc_now

IMPORT_JOB_KIND: Final = "core.itsm_import"
SWITCH_JOB_KIND: Final = "core.itsm_switch"
IMPORT_DEDUPE_KEY: Final = "itsm_import"
IMPORT_LANE: Final = "itsm_import"
CSV_NAME: Final = "itsm-import-bericht.csv"
SWITCH_INTERRUPTED: Final = "Die Umstellung wurde unterbrochen; Znuny ist wieder beschreibbar."
IMPORT_INTERRUPTED: Final = "Der Import wurde unterbrochen."
MESSAGE_MAX: Final = 500

RunKind = Literal["dry_run", "import", "switch", "rollback"]
RunStatus = Literal["running", "done", "failed", "cancelled"]


class ImportBusy(Exception):
    """An import or switch job is active (API -> 409, CLI -> exit 1)."""


#: the ITSM data is already kept in Tiqora (or another precondition fails); German message
ImportNotAllowed = SwitchBlocked


class ItsmImportPayload(BaseModel):
    user_id: int
    dry_run: bool
    run_id: int
    page_size: int = 500


class ItsmSwitchPayload(BaseModel):
    user_id: int
    run_id: int
    addon_installed: bool
    edits_stopped: bool = False
    #: "Umstellen ohne CI-Daten": Znuny ITSM release the importer cannot read (itsm1 F19)
    without_ci_data: bool = False


async def _lock_import_start(session: AsyncSession) -> None:
    """Lock the importer-owned ``itsm.znuny_readonly`` row, creating it ("0") when absent."""
    try:
        async with session.begin_nested():
            session.add(TiqoraSettings(key=SETTING_ZNUNY_READONLY, value="0"))
            await session.flush()
    except IntegrityError:
        pass  # another start inserted it first
    await session.execute(
        select(TiqoraSettings.key)
        .where(TiqoraSettings.key == SETTING_ZNUNY_READONLY)
        .with_for_update()
    )


async def bind_run_to_job(session: AsyncSession, run: TiqoraItsmImportRun, job: TiqoraJob) -> None:
    """Attach the enqueued job to its run; a deduped job of another run means a lost race.

    The fresh run row is then dropped (flush only) and :class:`ImportBusy` raised, so no
    ``running`` run is left bound to a job that never processes it.
    """
    payload = json.loads(job.payload)
    if payload.get("run_id") != run.id:
        await session.delete(run)
        await session.flush()
        raise ImportBusy("Ein ITSM-Import oder eine Umstellung läuft bereits.")
    run.job_id = job.id


async def start_run(session: AsyncSession, *, kind: RunKind, user_id: int) -> TiqoraItsmImportRun:
    """Create the ``running`` run row (flush only; the caller enqueues the job and commits).

    The first three kinds are refused with :class:`ImportBusy` while an import or switch job is
    active. Two starts are serialised by a row lock on ``itsm.znuny_readonly`` (inserted when
    absent; the importer owns that key, so the row always exists to lock) and the active-job
    check is a locking read: on MariaDB a plain read would use the REPEATABLE READ snapshot of
    the session's first read and miss the other start's committed job. A caller that enqueues
    the job must pass it to :func:`bind_run_to_job`.
    """
    if kind != "rollback":
        await _lock_import_start(session)
        for job_kind in (IMPORT_JOB_KIND, SWITCH_JOB_KIND):
            if await find_active_job(
                session, kind=job_kind, dedupe_key=IMPORT_DEDUPE_KEY, for_update=True
            ):
                raise ImportBusy("Ein ITSM-Import oder eine Umstellung läuft bereits.")
    run = TiqoraItsmImportRun(
        kind=kind,
        status="running",
        job_id=None,
        started_by=user_id,
        started_at=utc_now(),
        finished_at=None,
        profile=None,
        summary="{}",
        message=None,
    )
    session.add(run)
    await session.flush()
    return run


async def finish_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    status: RunStatus,
    report: ImportReport | None,
    message: str | None,
) -> None:
    """Close the run row in its own transaction (summary: counts + first 200 issues)."""
    values: dict[str, Any] = {
        "status": status,
        "finished_at": utc_now(),
        "message": message[:MESSAGE_MAX] if message is not None else None,
    }
    if report is not None:
        values["summary"] = json.dumps(report.summary())
    async with session_factory() as session:
        await session.execute(
            update(TiqoraItsmImportRun).where(TiqoraItsmImportRun.id == run_id).values(**values)
        )
        await session.commit()


async def _mark_running(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, job_id: int
) -> None:
    """A (re)claimed job takes over its run row."""
    async with session_factory() as session:
        await session.execute(
            update(TiqoraItsmImportRun)
            .where(TiqoraItsmImportRun.id == run_id)
            .values(status="running", job_id=job_id, finished_at=None, message=None)
        )
        await session.commit()


async def set_znuny_readonly(session_factory: async_sessionmaker[AsyncSession], flag: bool) -> None:
    """Write ``itsm.znuny_readonly`` (the only setting the importer writes itself)."""
    async with session_factory() as session:
        row = await session.get(TiqoraSettings, SETTING_ZNUNY_READONLY)
        value = "1" if flag else "0"
        if row is None:
            session.add(TiqoraSettings(key=SETTING_ZNUNY_READONLY, value=value))
        else:
            row.value = value
        await session.commit()


def _switch_failure_message(exc: BaseException) -> str:
    return f"Die Umstellung ist fehlgeschlagen ({type(exc).__name__}): {exc}"[:MESSAGE_MAX]


def _failure_message(exc: BaseException) -> str:
    return f"Der Import ist fehlgeschlagen ({type(exc).__name__}): {exc}"[:MESSAGE_MAX]


async def run_itsm_import_job(ctx: JobContext, payload: Any) -> dict[str, Any]:
    factory = ctx.session_factory
    await _mark_running(factory, payload.run_id, ctx.job_id)
    try:
        async with factory() as session:
            await check_can_import(session)
        report = await run_import(
            factory,
            run_id=payload.run_id,
            user_id=payload.user_id,
            dry_run=payload.dry_run,
            page_size=payload.page_size,
            progress=ctx.progress,
            check_cancelled=ctx.check_cancelled,
        )
        await ctx.add_file(CSV_NAME, "text/csv; charset=utf-8", report.to_csv())
    except JobCancelled:
        await finish_run(factory, payload.run_id, status="cancelled", report=None, message=None)
        raise
    except (ImportBlocked, ImportNotAllowed) as exc:
        await finish_run(factory, payload.run_id, status="failed", report=None, message=str(exc))
        raise JobFailed(str(exc)) from exc
    except Exception as exc:
        await finish_run(
            factory, payload.run_id, status="failed", report=None, message=_failure_message(exc)
        )
        raise
    await finish_run(factory, payload.run_id, status="done", report=report, message=None)
    return {"run_id": payload.run_id, "issues": len(report.issues)}


async def _import_interrupted(ctx: JobContext, payload: Any) -> None:
    await finish_run(
        ctx.session_factory,
        payload.run_id,
        status="failed",
        report=None,
        message=IMPORT_INTERRUPTED,
    )


async def run_itsm_switch_job(ctx: JobContext, payload: Any) -> dict[str, Any] | None:
    """Final import under the read-only flag, then itsm1's switch in one transaction (D7, R6).

    The flag ``itsm.znuny_readonly`` is set (and committed) before the import reads Znuny, and it
    is cleared again on every way out that does not end in the switch (error, cancel, timeout).
    """
    factory = ctx.session_factory
    await _mark_running(factory, payload.run_id, ctx.job_id)
    try:
        async with factory() as session:
            await check_can_switch(
                session,
                addon_installed=payload.addon_installed,
                edits_stopped=payload.edits_stopped,
                without_ci_data=payload.without_ci_data,
                ignore_job_id=ctx.job_id,
            )
    except SwitchBlocked as exc:
        await finish_run(factory, payload.run_id, status="failed", report=None, message=exc.message)
        raise JobFailed(exc.message) from exc

    await set_znuny_readonly(factory, True)
    try:
        report = ImportReport()
        if not payload.without_ci_data:
            report = await run_import(
                factory,
                run_id=payload.run_id,
                user_id=payload.user_id,
                dry_run=False,
                page_size=500,
                progress=ctx.progress,
                check_cancelled=ctx.check_cancelled,
            )
            await ctx.add_file(CSV_NAME, "text/csv; charset=utf-8", report.to_csv())
        await ctx.check_cancelled()
        async with factory() as session:
            await facades.get_ports(session).mode.switch_to_tiqora(
                user_id=payload.user_id, import_run_id=payload.run_id
            )
            await session.commit()
    except BaseException as exc:
        # the flag must never stay set unless the switch succeeded
        await set_znuny_readonly(factory, False)
        if isinstance(exc, JobCancelled):
            await finish_run(factory, payload.run_id, status="cancelled", report=None, message=None)
        elif isinstance(exc, ImportBlocked):
            await finish_run(
                factory, payload.run_id, status="failed", report=None, message=str(exc)
            )
            raise JobFailed(str(exc)) from exc
        else:
            await finish_run(
                factory,
                payload.run_id,
                status="failed",
                report=None,
                message=_switch_failure_message(exc),
            )
        raise
    await finish_run(factory, payload.run_id, status="done", report=report, message=None)
    return {"run_id": payload.run_id, "issues": len(report.issues)}


async def _switch_interrupted(ctx: JobContext, payload: Any) -> None:
    """The read-only flag must never stay set unless the switch succeeded."""
    async with ctx.session_factory() as session:
        switched = await facades.get_ports(session).mode.is_switched()
    if not switched:
        await set_znuny_readonly(ctx.session_factory, False)
    await finish_run(
        ctx.session_factory,
        payload.run_id,
        status="failed",
        report=None,
        message=SWITCH_INTERRUPTED,
    )


ITSM_IMPORT_JOB = JobHandler(
    kind=IMPORT_JOB_KIND,
    payload_model=ItsmImportPayload,
    run=run_itsm_import_job,
    title_key="jobs.kinds.core.itsm_import",
    timeout_seconds=6 * 3600,
    lane=IMPORT_LANE,
    max_attempts=3,
    requeue_if_interrupted=True,
    on_interrupted=_import_interrupted,
)

ITSM_SWITCH_JOB = JobHandler(
    kind=SWITCH_JOB_KIND,
    payload_model=ItsmSwitchPayload,
    run=run_itsm_switch_job,
    title_key="jobs.kinds.core.itsm_switch",
    timeout_seconds=6 * 3600,
    lane=IMPORT_LANE,
    max_attempts=3,
    requeue_if_interrupted=True,
    on_interrupted=_switch_interrupted,
)
