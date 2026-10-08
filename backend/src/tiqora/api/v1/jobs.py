"""Background job polling, cancel and file download."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import get_registry
from tiqora.jobs.schemas import JobOut, job_out
from tiqora.jobs.service import (
    JobNotCancellable,
    can_view_job,
    get_job,
    get_job_file,
    list_job_files,
    list_jobs,
    request_cancel,
)
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/jobs", tags=["jobs"])

_NOT_FOUND = "Aufgabe nicht gefunden."


async def _visible_job(session: DbSession, user: CurrentUser, job_id: int) -> TiqoraJob:
    job = await get_job(session, job_id)
    if job is not None:
        is_admin = await PermissionEngine(session).is_admin(user.id)
        if can_view_job(job, user_id=user.id, is_admin=is_admin):
            return job
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@router.get("", response_model=list[JobOut])
async def list_my_jobs(
    user: CurrentUser,
    session: DbSession,
    all: bool = Query(False, description="All users' jobs (admins only)."),  # noqa: A002
    limit: int = Query(50, ge=1, le=200),
) -> list[JobOut]:
    if all and not await PermissionEngine(session).is_admin(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Nur Administratoren sehen die Aufgaben aller Benutzer.",
        )
    jobs = await list_jobs(session, created_by=None if all else user.id, limit=limit)
    files = await list_job_files(session, [j.id for j in jobs])
    registry = get_registry()
    return [job_out(j, files.get(j.id, []), registry) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
async def read_job(job_id: int, user: CurrentUser, session: DbSession) -> JobOut:
    job = await _visible_job(session, user, job_id)
    files = await list_job_files(session, [job.id])
    return job_out(job, files.get(job.id, []), get_registry())


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, user: CurrentUser, session: DbSession) -> JobOut:
    job = await _visible_job(session, user, job_id)
    try:
        await request_cancel(session, job)
    except JobNotCancellable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Die Aufgabe ist bereits beendet."},
        ) from None
    await session.commit()
    files = await list_job_files(session, [job.id])
    return job_out(job, files.get(job.id, []), get_registry())


@router.get("/{job_id}/files/{file_id}")
async def download_job_file(
    job_id: int, file_id: int, user: CurrentUser, session: DbSession
) -> Response:
    job = await _visible_job(session, user, job_id)
    row = await get_job_file(session, job.id, file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return Response(
        content=bytes(row.data),
        media_type=row.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.name, safe='')}"},
    )
