"""Background job polling, cancel and file download.

API keys reach ``/api/v1/jobs`` with the ``tickets`` OR the ``stats`` scope
(:mod:`tiqora.domain.api_key_scopes`). A key whose ``tickets`` scope does not cover
the request (a stats-only key) may only touch report jobs
(:data:`STATS_JOB_KINDS`): every other kind answers 403, and ``?all=true`` is refused.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.api_key_scopes import area_allows
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.schemas import JobOut, job_out
from tiqora.jobs.service import (
    JobNotCancellable,
    can_download_job_file,
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
#: the job kinds a stats-only API key may reach (report runs, exports, the report backfill)
STATS_JOB_KINDS: Final = frozenset(
    {"core.report_run", "core.report_export", "core.report_backfill"}
)
_STATS_ONLY = "Dieser API-Schlüssel darf nur Berichtsaufgaben abrufen (Bereich „stats“)."


def _stats_only(user: AuthenticatedUser, request: Request) -> bool:
    """True for an API key whose ``tickets`` scope does not cover this request."""
    if user.api_key_scopes is None:
        return False
    return not area_allows(user.api_key_scopes, "tickets", method=request.method)


async def _visible_job(
    session: DbSession, user: CurrentUser, job_id: int, request: Request
) -> TiqoraJob:
    job = await get_job(session, job_id)
    if job is not None:
        is_admin = await PermissionEngine(session).is_admin(user.id)
        if can_view_job(job, user_id=user.id, is_admin=is_admin):
            if _stats_only(user, request) and job.kind not in STATS_JOB_KINDS:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_STATS_ONLY)
            return job
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


@router.get("", response_model=list[JobOut])
async def list_my_jobs(
    request: Request,
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
    stats_only = _stats_only(user, request)
    if all and stats_only:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_STATS_ONLY)
    jobs = await list_jobs(
        session,
        created_by=None if all else user.id,
        limit=limit,
        kinds=sorted(STATS_JOB_KINDS) if stats_only else None,
    )
    files = await list_job_files(session, [j.id for j in jobs])
    registry = get_registry()
    return [job_out(j, files.get(j.id, []), registry, viewer_id=user.id) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
async def read_job(job_id: int, request: Request, user: CurrentUser, session: DbSession) -> JobOut:
    job = await _visible_job(session, user, job_id, request)
    files = await list_job_files(session, [job.id])
    return job_out(job, files.get(job.id, []), get_registry(), viewer_id=user.id)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: int, request: Request, user: CurrentUser, session: DbSession
) -> JobOut:
    job = await _visible_job(session, user, job_id, request)
    try:
        await request_cancel(session, job)
    except JobNotCancellable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Die Aufgabe ist bereits beendet."},
        ) from None
    await session.commit()
    files = await list_job_files(session, [job.id])
    return job_out(job, files.get(job.id, []), get_registry(), viewer_id=user.id)


@router.get("/{job_id}/files/{file_id}")
async def download_job_file(
    job_id: int, file_id: int, request: Request, user: CurrentUser, session: DbSession
) -> Response:
    job = await _visible_job(session, user, job_id, request)
    # Files hold data selected with the creator's permissions: admins only see metadata.
    if not can_download_job_file(job, user_id=user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    row = await get_job_file(session, job.id, file_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return Response(
        content=bytes(row.data),
        media_type=row.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.name, safe='')}"},
    )
