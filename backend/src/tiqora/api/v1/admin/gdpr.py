"""Admin GDPR erasure API — preview, apply, list, rollback, purge, download.

All destructive routes require :data:`AdminUser` and (for apply) ``confirm=true``.
Engine always passes ``force_parallel=True`` so admin can act during parallel
operation; the risk is recorded on the job and in ``tiqora_gdpr_audit``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParams, Page, window
from tiqora.api.v1.admin.schemas import (
    ErasureSelectorIn,
    GdprCustomerRecordPreviewOut,
    GdprCustomerRecordPreviewRequest,
    GdprDeleteSummaryRowOut,
    GdprErasureJobCreate,
    GdprErasureJobDetailOut,
    GdprErasureJobOut,
    GdprErasurePreviewOut,
    GdprErasurePreviewRequest,
    GdprFieldPreviewOut,
    GdprPurgeOut,
    GdprResolvedCustomerOut,
    GdprRollbackOut,
    GdprSampleRowOut,
    GdprSelectorCountOut,
    GdprSelectorCountRequest,
)
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraGdprJob
from tiqora.gdpr.erasure import (
    ErasureError,
    ErasureNotFoundError,
    ErasureSelector,
    build_customer_record_preview,
    build_erasure_preview,
    load_job_backups_export,
    purge_job_backup,
    resolve_selector,
    rollback_job,
    run_erasure,
)
from tiqora.gdpr.gate import GdprRefusedError

router = APIRouter(prefix="/gdpr", tags=["admin:gdpr"])

GdprJobStatus = Literal["applied", "rolled_back", "purged"]
GdprMode = Literal["anonymize", "delete"]


class GdprJobListParams(ListParams):
    status_filter: GdprJobStatus | None = None
    mode: GdprMode | None = None
    q: str | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None


def gdpr_job_list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 25,
    status_filter: Annotated[GdprJobStatus | None, Query(alias="status")] = None,
    mode: Annotated[GdprMode | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
) -> GdprJobListParams:
    return GdprJobListParams(
        page=page,
        page_size=page_size,
        valid="all",
        status_filter=status_filter,
        mode=mode,
        q=q,
        from_dt=from_dt,
        to_dt=to_dt,
    )


GdprJobListParamsDep = Annotated[GdprJobListParams, Depends(gdpr_job_list_params)]


def _selector_from_in(body: ErasureSelectorIn | None) -> ErasureSelector:
    if body is None:
        return ErasureSelector()
    return ErasureSelector(
        logins=list(body.logins or []),
        customer_ids=list(body.customer_ids or []),
        login_regex=body.login_regex,
        customer_id_regex=body.customer_id_regex,
        changed_before=body.changed_before,
        changed_after=body.changed_after,
        activity=body.activity,
        valid_id=body.valid_id,
    )


def _job_detail(job: TiqoraGdprJob) -> GdprErasureJobDetailOut:
    counts: dict[str, int] = {}
    logins: list[str] = []
    selector: dict[str, Any] = {}
    try:
        raw_counts = json.loads(job.counts or "{}")
        if isinstance(raw_counts, dict):
            counts = {str(k): int(v) for k, v in raw_counts.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        counts = {}
    try:
        raw_logins = json.loads(job.resolved_logins or "[]")
        if isinstance(raw_logins, list):
            logins = [str(x) for x in raw_logins]
    except (json.JSONDecodeError, TypeError):
        logins = []
    try:
        raw_sel = json.loads(job.selector or "{}")
        if isinstance(raw_sel, dict):
            selector = raw_sel
    except (json.JSONDecodeError, TypeError):
        selector = {}
    base = GdprErasureJobOut.model_validate(job)
    return GdprErasureJobDetailOut(
        **base.model_dump(),
        counts_parsed=counts,
        resolved_logins_parsed=logins,
        selector_parsed=selector,
    )


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        return factory  # type: ignore[no-any-return]
    return get_session_factory()


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.post("/preview", response_model=GdprErasurePreviewOut)
async def preview_erasure(
    body: GdprErasurePreviewRequest,
    admin: AdminUser,
    session: DbSession,
) -> GdprErasurePreviewOut:
    _ = admin
    selector = _selector_from_in(body.selector)
    try:
        preview = await build_erasure_preview(
            session, selector, mode=body.mode, delete_tickets=body.delete_tickets
        )
    except ErasureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return GdprErasurePreviewOut(
        mode=preview.mode,
        customers=[GdprResolvedCustomerOut(**c.__dict__) for c in preview.customers],
        counts=preview.counts,
        sample=[GdprSampleRowOut(**s.__dict__) for s in preview.sample],
        columns_changed=preview.columns_changed,
        tables_deleted=preview.tables_deleted,
    )


@router.post("/selector-count", response_model=GdprSelectorCountOut)
async def selector_count(
    body: GdprSelectorCountRequest,
    admin: AdminUser,
    session: DbSession,
) -> GdprSelectorCountOut:
    """Fast match count for the live selector counter (same selector shape as
    ``/preview``, but resolves ids only — no customer/sample/count breakdown)."""
    _ = admin
    selector = _selector_from_in(body.selector)
    try:
        ids = await resolve_selector(session, selector)
    except ErasureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return GdprSelectorCountOut(count=len(ids))


@router.post("/record-preview", response_model=GdprCustomerRecordPreviewOut)
async def customer_record_preview(
    body: GdprCustomerRecordPreviewRequest,
    admin: AdminUser,
    session: DbSession,
) -> GdprCustomerRecordPreviewOut:
    """Per-customer before/after preview. Read-only: no commit is ever issued,
    and the session is rolled back explicitly in case the engine's read helpers
    ever pick up a pending flush."""
    _ = admin
    try:
        preview = await build_customer_record_preview(
            session,
            body.login,
            body.mode,
            seed=body.seed,
            delete_tickets=body.delete_tickets,
        )
    except ErasureNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ErasureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    finally:
        await session.rollback()
    return GdprCustomerRecordPreviewOut(
        login=preview.login,
        mode=preview.mode,
        fields=[GdprFieldPreviewOut(**f.__dict__) for f in preview.fields],
        delete_summary=[GdprDeleteSummaryRowOut(**d.__dict__) for d in preview.delete_summary],
    )


@router.post(
    "/jobs",
    response_model=GdprErasureJobDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_erasure_job(
    body: GdprErasureJobCreate,
    admin: AdminUser,
    request: Request,
) -> GdprErasureJobDetailOut:
    if body.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confirm must be true to run a destructive GDPR erasure",
        )
    factory = _session_factory(request)
    settings = _settings(request)
    selector = _selector_from_in(body.selector)
    actor = f"admin:{admin.login}" if getattr(admin, "login", None) else f"admin:{admin.id}"
    try:
        result = await run_erasure(
            factory,
            settings,
            customer_user_ids=list(body.customer_user_ids),
            mode=body.mode,
            seed=body.seed,
            actor=actor,
            force_parallel=True,
            selector=selector,
            delete_tickets=body.delete_tickets,
        )
    except GdprRefusedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ErasureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    async with factory() as session:
        job = await session.get(TiqoraGdprJob, result.job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="job created but not readable",
            )
        return _job_detail(job)


@router.get("/jobs", response_model=Page[GdprErasureJobOut])
async def list_erasure_jobs(
    admin: AdminUser,
    session: DbSession,
    params: GdprJobListParamsDep,
) -> Page[GdprErasureJobOut]:
    _ = admin
    stmt = select(TiqoraGdprJob)
    if params.status_filter is not None:
        stmt = stmt.where(TiqoraGdprJob.status == params.status_filter)
    if params.mode is not None:
        stmt = stmt.where(TiqoraGdprJob.mode == params.mode)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(TiqoraGdprJob.resolved_logins.ilike(like))
    if params.from_dt is not None:
        from_val = params.from_dt.replace(tzinfo=None) if params.from_dt.tzinfo else params.from_dt
        stmt = stmt.where(TiqoraGdprJob.created >= from_val)
    if params.to_dt is not None:
        to_val = params.to_dt.replace(tzinfo=None) if params.to_dt.tzinfo else params.to_dt
        stmt = stmt.where(TiqoraGdprJob.created <= to_val)
    stmt = stmt.order_by(TiqoraGdprJob.created.desc(), TiqoraGdprJob.id.desc())
    rows, total = await window(session, stmt, params)
    return Page(
        items=[GdprErasureJobOut.model_validate(r) for r in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/jobs/{job_id}", response_model=GdprErasureJobDetailOut)
async def get_erasure_job(
    job_id: int,
    admin: AdminUser,
    session: DbSession,
) -> GdprErasureJobDetailOut:
    _ = admin
    job = await session.get(TiqoraGdprJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GDPR job not found")
    return _job_detail(job)


@router.post("/jobs/{job_id}/rollback", response_model=GdprRollbackOut)
async def rollback_erasure_job(
    job_id: int,
    admin: AdminUser,
    request: Request,
) -> GdprRollbackOut:
    factory = _session_factory(request)
    settings = _settings(request)
    actor = f"admin:{admin.login}" if getattr(admin, "login", None) else f"admin:{admin.id}"
    try:
        result = await rollback_job(
            factory,
            settings,
            job_id,
            actor=actor,
            force_parallel=True,
        )
    except ErasureNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ErasureError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GdprRefusedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return GdprRollbackOut(restored_rows=int(result.get("restored_rows", 0)))


@router.post("/jobs/{job_id}/purge-backup", response_model=GdprPurgeOut)
async def purge_erasure_job_backup(
    job_id: int,
    admin: AdminUser,
    request: Request,
) -> GdprPurgeOut:
    factory = _session_factory(request)
    actor = f"admin:{admin.login}" if getattr(admin, "login", None) else f"admin:{admin.id}"
    try:
        result = await purge_job_backup(factory, job_id, actor=actor)
    except ErasureNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return GdprPurgeOut(deleted_backups=int(result.get("deleted_backups", 0)))


@router.get("/jobs/{job_id}/backup/download")
async def download_erasure_backup(
    job_id: int,
    admin: AdminUser,
    session: DbSession,
) -> StreamingResponse:
    _ = admin
    job = await session.get(TiqoraGdprJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GDPR job not found")
    rows = await load_job_backups_export(session, job_id)
    payload = json.dumps(
        {"job_id": job_id, "mode": job.mode, "status": job.status, "backups": rows},
        indent=2,
        sort_keys=True,
    )

    async def _gen() -> AsyncIterator[bytes]:
        yield payload.encode("utf-8")

    return StreamingResponse(
        _gen(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="gdpr-job-{job_id}-backup.json"'},
    )
