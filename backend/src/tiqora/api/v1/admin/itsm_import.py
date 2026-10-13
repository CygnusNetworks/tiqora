"""Znuny ITSM import, switch and rollback — ``/api/v1/admin/itsm/import`` (admin only).

Import and switch are background jobs (``tiqora_job``); the endpoints only create the run row,
enqueue the job and return 202. The admin page polls ``GET /status`` and ``GET /api/v1/jobs/{id}``.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraItsmImportRun
from tiqora.itsm.importer.jobs import (
    IMPORT_DEDUPE_KEY,
    IMPORT_JOB_KIND,
    SWITCH_JOB_KIND,
    ImportBusy,
    ItsmImportPayload,
    ItsmSwitchPayload,
    RunKind,
    bind_run_to_job,
    stale_run_statuses,
    start_run,
)
from tiqora.itsm.importer.schemas import (
    ItsmImportRollbackIn,
    ItsmImportRunOut,
    ItsmImportStartIn,
    ItsmImportStatusOut,
    ItsmImportSwitchIn,
    run_out,
    status_out,
)
from tiqora.itsm.importer.switch import (
    SwitchBlocked,
    check_can_switch,
    import_status,
    release_readonly,
    rollback,
)
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import enqueue_job

router = APIRouter(prefix="/itsm/import", tags=["admin:itsm-import"])

SWITCH_CONFIRM: Final = "UMSTELLEN"
ROLLBACK_CONFIRM: Final = "ZURÜCK"
RUN_NOT_FOUND: Final = "Der Lauf wurde nicht gefunden."
DEFAULT_PAGE_SIZE: Final = 500


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


async def _user_names(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    """``started_by`` -> "First Last" (IN-list over the Znuny ``users`` table)."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Users.id, Users.first_name, Users.last_name).where(Users.id.in_(sorted(ids)))
    )
    return {int(r[0]): f"{r[1]} {r[2]}".strip() for r in rows.all()}


async def _status(session: AsyncSession) -> ItsmImportStatusOut:
    st = await import_status(session)
    ids = {st.last_run.started_by} if st.last_run is not None else set()
    stale = await stale_run_statuses(session, [st.last_run] if st.last_run is not None else [])
    return status_out(st, await _user_names(session, ids), stale)


async def _start_import_run(session: AsyncSession, kind: RunKind, user_id: int) -> JobAccepted:
    """Create the run row and enqueue its job in one transaction."""
    try:
        run = await start_run(session, kind=kind, user_id=user_id)
        job = await enqueue_job(
            session,
            kind=IMPORT_JOB_KIND,
            payload=ItsmImportPayload(
                user_id=user_id,
                dry_run=kind == "dry_run",
                run_id=run.id,
                page_size=DEFAULT_PAGE_SIZE,
            ),
            created_by=user_id,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        await bind_run_to_job(session, run, job)
    except ImportBusy as exc:
        await session.rollback()
        raise _conflict(str(exc)) from exc
    job_id, job_status = job.id, job.status
    await session.commit()
    return JobAccepted(job_id=job_id, status="running" if job_status == "running" else "queued")


@router.get("/status", response_model=ItsmImportStatusOut)
async def get_import_status(_admin: AdminUser, session: DbSession) -> ItsmImportStatusOut:
    return await _status(session)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def start_import(
    body: ItsmImportStartIn, admin: AdminUser, session: DbSession
) -> JobAccepted:
    """Queue a dry run or the import; 409 when blocked (after the switch, busy, no Znuny ITSM)."""
    st = await import_status(session)
    if not st.can_import:
        # the status read opened a transaction; end it before start_run takes its row lock
        await session.rollback()
        raise _conflict(st.blocked_reason or "Der Import ist derzeit nicht möglich.")
    await session.rollback()
    return await _start_import_run(session, "dry_run" if body.dry_run else "import", admin.id)


@router.get("/runs", response_model=list[ItsmImportRunOut])
async def list_runs(
    _admin: AdminUser, session: DbSession, limit: int = Query(20, ge=1, le=100)
) -> list[ItsmImportRunOut]:
    rows = (
        (
            await session.execute(
                select(TiqoraItsmImportRun)
                .order_by(TiqoraItsmImportRun.started_at.desc(), TiqoraItsmImportRun.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    names = await _user_names(session, {r.started_by for r in rows})
    stale = await stale_run_statuses(session, list(rows))
    return [run_out(r, names, stale) for r in rows]


@router.get("/runs/{run_id}", response_model=ItsmImportRunOut)
async def get_run(run_id: int, _admin: AdminUser, session: DbSession) -> ItsmImportRunOut:
    run = await session.get(TiqoraItsmImportRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RUN_NOT_FOUND)
    stale = await stale_run_statuses(session, [run])
    return run_out(run, await _user_names(session, {run.started_by}), stale)


@router.post("/switch", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def start_switch(
    body: ItsmImportSwitchIn, admin: AdminUser, session: DbSession
) -> JobAccepted:
    """Queue the switch to Tiqora (final import under the read-only flag, then itsm1's switch)."""
    if body.confirm != SWITCH_CONFIRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zur Bestätigung muss „{SWITCH_CONFIRM}“ eingegeben werden.",
        )
    try:
        await check_can_switch(
            session,
            addon_installed=body.addon_installed,
            edits_stopped=body.edits_stopped,
            without_ci_data=body.without_ci_data,
        )
    except SwitchBlocked as exc:
        await session.rollback()
        raise _conflict(exc.message) from exc
    await session.rollback()
    try:
        run = await start_run(session, kind="switch", user_id=admin.id)
        job = await enqueue_job(
            session,
            kind=SWITCH_JOB_KIND,
            payload=ItsmSwitchPayload(
                user_id=admin.id,
                run_id=run.id,
                addon_installed=body.addon_installed,
                edits_stopped=body.edits_stopped,
                without_ci_data=body.without_ci_data,
            ),
            created_by=admin.id,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        await bind_run_to_job(session, run, job)
    except ImportBusy as exc:
        await session.rollback()
        raise _conflict(str(exc)) from exc
    job_id, job_status = job.id, job.status
    await session.commit()
    return JobAccepted(job_id=job_id, status="running" if job_status == "running" else "queued")


@router.post("/rollback", response_model=ItsmImportStatusOut)
async def rollback_switch(
    body: ItsmImportRollbackIn, admin: AdminUser, session: DbSession
) -> ItsmImportStatusOut:
    """Back to Znuny (Mode A): two setting writes and a run row, so no job."""
    if body.confirm != ROLLBACK_CONFIRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zur Bestätigung muss „{ROLLBACK_CONFIRM}“ eingegeben werden.",
        )
    try:
        await rollback(session, user_id=admin.id)
    except SwitchBlocked as exc:
        await session.rollback()
        raise _conflict(exc.message) from exc
    await session.commit()
    return await _status(session)


@router.post("/release-lock", response_model=ItsmImportStatusOut)
async def release_lock(_admin: AdminUser, session: DbSession) -> ItsmImportStatusOut:
    """ "Sperre aufheben": clear a read-only flag that stayed set without a switch (F10)."""
    try:
        await release_readonly(session)
    except SwitchBlocked as exc:
        await session.rollback()
        raise _conflict(exc.message) from exc
    await session.commit()
    return await _status(session)
