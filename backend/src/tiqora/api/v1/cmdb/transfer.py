"""Agent API: CSV/XLSX import (upload -> background job) and mapping templates."""

from __future__ import annotations

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ValidationError

from tiqora.api.uploads import read_upload_within_limit
from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.access import readable_class_ids, require_class_access
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.schemas import (
    CiSearchCriteria,
    CmdbBulkIn,
    CmdbExportIn,
    CmdbImportRequestIn,
    ImportTemplateIn,
    ImportTemplateOut,
    MappingRowIn,
)
from tiqora.cmdb.search import ci_search_statement
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.cmdb.transfer.bulk_job import CMDB_BULK_JOB, CmdbBulkPayload, invalid_state_ids
from tiqora.cmdb.transfer.export_job import CMDB_EXPORT_JOB, CmdbExportPayload
from tiqora.cmdb.transfer.import_job import (
    CMDB_IMPORT_JOB,
    INPUT_PREFIX,
    CmdbImportPayload,
    MappingProblem,
    check_mapping,
    default_mapping,
    resolve_columns,
)
from tiqora.cmdb.transfer.mapping import ImportOptions, TargetError, parse_target
from tiqora.cmdb.transfer.readers import ReadError, read_rows
from tiqora.cmdb.transfer.templates import (
    create_template,
    delete_template,
    get_template,
    list_templates,
    template_out,
    update_template,
)
from tiqora.db.tiqora.models import TiqoraJobFile
from tiqora.filters import FilterError
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import (
    count_active_jobs,
    enqueue_job,
    lock_user_job_quota,
    utc_now,
)

router = APIRouter()

MAX_ACTIVE_IMPORTS_PER_USER = 3
MAX_ACTIVE_EXPORTS_PER_USER = 3
MAX_ACTIVE_BULKS_PER_USER = 3
_MIB = 1024 * 1024


def _mapping_error(problems: list[MappingProblem]) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {
            "code": "invalid_mapping",
            "rows": [{"row": p.row, "message": p.message} for p in problems],
        },
    )


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": code, "message": message})


# --- mapping templates -------------------------------------------------------------------------


@router.get("/import-templates", response_model=list[ImportTemplateOut])
async def list_import_templates(ctx: Ctx, class_id: int | None = None) -> list[ImportTemplateOut]:
    rows = await list_templates(ctx.session, ctx.user.id, class_id)
    readable = set(await readable_class_ids(ctx.session, ctx.access))
    return [template_out(r, ctx.user.id) for r in rows if int(r.class_id) in readable]


@router.post(
    "/import-templates", response_model=ImportTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_import_template(body: ImportTemplateIn, ctx: Ctx) -> ImportTemplateOut:
    try:
        await require_class_access(ctx.session, ctx.access, body.class_id, "rw")
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    _check_template_targets(body.mapping)
    row = await create_template(ctx.session, ctx.user.id, body)
    out = template_out(row, ctx.user.id)
    await ctx.session.commit()
    return out


@router.get("/import-templates/{template_id}", response_model=ImportTemplateOut)
async def read_import_template(template_id: int, ctx: Ctx) -> ImportTemplateOut:
    try:
        row = await get_template(ctx.session, ctx.user.id, template_id)
        await require_class_access(ctx.session, ctx.access, int(row.class_id), "ro")
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return template_out(row, ctx.user.id)


@router.put("/import-templates/{template_id}", response_model=ImportTemplateOut)
async def update_import_template(
    template_id: int, body: ImportTemplateIn, ctx: Ctx
) -> ImportTemplateOut:
    try:
        await require_class_access(ctx.session, ctx.access, body.class_id, "rw")
        _check_template_targets(body.mapping)
        row = await update_template(ctx.session, ctx.user.id, template_id, body)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    out = template_out(row, ctx.user.id)
    await ctx.session.commit()
    return out


@router.delete("/import-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import_template(template_id: int, ctx: Ctx) -> Response:
    try:
        await delete_template(ctx.session, ctx.user.id, template_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _check_template_targets(mapping: list[MappingRowIn]) -> None:
    """A template is saved before any file is known, so only the targets can be checked."""
    problems: list[MappingProblem] = []
    for n, row in enumerate(mapping, start=1):
        try:
            parse_target(row.target)
        except TargetError as exc:
            problems.append(MappingProblem(n, str(exc)))
    if problems:
        raise _mapping_error(problems)


# --- import job -------------------------------------------------------------------------------


@router.post("/import-jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_import(
    ctx: Ctx,
    file: Annotated[UploadFile, File()],
    request: Annotated[str, Form()],
) -> JobAccepted:
    """Queue the import of ``file``; poll ``GET /jobs/{id}`` for progress and the report.

    The mapping is checked now (422 ``invalid_mapping``); permissions are checked again when
    the worker runs the job.
    """
    try:
        req = CmdbImportRequestIn.model_validate_json(request)
    except ValidationError as exc:
        raise _bad_request("invalid_request", "Die Angaben zum Import sind ungültig.") from exc
    try:
        ZoneInfo(req.time_zone)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise _bad_request("invalid_request", "Die Zeitzone ist unbekannt.") from exc

    settings = await load_cmdb_settings(ctx.session)
    data = await read_upload_within_limit(file, settings.import_max_mb * _MIB)

    class_id, file_format, options, mapping = (
        req.class_id,
        req.file_format,
        req.options,
        req.mapping,
    )
    try:
        if req.template_id is not None:
            tpl = template_out(
                await get_template(ctx.session, ctx.user.id, req.template_id), ctx.user.id
            )
            class_id = class_id or tpl.class_id
            file_format = file_format or tpl.file_format
            options = options or tpl.options
            mapping = mapping or tpl.mapping
        if class_id is None:
            raise _bad_request("invalid_request", "Es fehlt die Klasse für den Import.")
        await require_class_access(ctx.session, ctx.access, class_id, "rw")
        definition = await ClassService(ctx.session).current_definition(class_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc

    filename = (file.filename or "import").replace("\\", "/").rsplit("/", 1)[-1][:200]
    if file_format is None:
        file_format = "xlsx" if filename.lower().endswith(".xlsx") else "csv"
    options = options or ImportOptions.model_validate({})
    try:
        header, _rows = read_rows(data, file_format=file_format, options=options)
    except ReadError as exc:
        raise _bad_request("unreadable_file", str(exc)) from exc
    mapping = mapping if mapping is not None else default_mapping(header)
    columns, problems = resolve_columns(header, mapping)
    problems += check_mapping(columns, definition.attributes, options)
    if problems:
        raise _mapping_error(problems)

    payload = CmdbImportPayload(
        user_id=ctx.user.id,
        class_id=class_id,
        file_format=file_format,
        mapping=mapping,
        options=options,
        dry_run=req.dry_run,
        filename=filename,
        time_zone=req.time_zone,
    )
    session = ctx.session
    await session.rollback()  # fresh transaction whose first statement locks the user's row
    await lock_user_job_quota(session, ctx.user.id)
    if (
        await count_active_jobs(session, kind=CMDB_IMPORT_JOB.kind, created_by=ctx.user.id)
        >= MAX_ACTIVE_IMPORTS_PER_USER
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_imports",
                "message": (
                    f"Es laufen bereits {MAX_ACTIVE_IMPORTS_PER_USER} Importe. "
                    "Bitte warten Sie, bis einer fertig ist."
                ),
            },
        )
    job = await enqueue_job(
        session, kind=CMDB_IMPORT_JOB.kind, payload=payload, created_by=ctx.user.id
    )
    session.add(
        TiqoraJobFile(
            job_id=job.id,
            name=f"{INPUT_PREFIX}{filename}"[:255],
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if file_format == "xlsx"
                else "text/csv"
            ),
            size_bytes=len(data),
            data=data,
            created_at=utc_now(),
        )
    )
    await session.flush()
    await session.commit()
    return JobAccepted(job_id=job.id, status="queued")


# --- export and bulk jobs ------------------------------------------------------------------------


async def _enqueue_limited(
    ctx: Ctx, handler: JobHandler, payload: BaseModel, *, limit: int, what: str
) -> JobAccepted:
    session = ctx.session
    await session.rollback()  # fresh transaction whose first statement locks the user's row
    await lock_user_job_quota(session, ctx.user.id)
    if await count_active_jobs(session, kind=handler.kind, created_by=ctx.user.id) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_jobs",
                "message": (
                    f"Es laufen bereits {limit} {what}. Bitte warten Sie, bis einer fertig ist."
                ),
            },
        )
    job = await enqueue_job(session, kind=handler.kind, payload=payload, created_by=ctx.user.id)
    await session.commit()
    return JobAccepted(job_id=job.id, status="queued")


@router.post("/export-jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_export(body: CmdbExportIn, ctx: Ctx) -> JobAccepted:
    """Queue the export of one class (CSV or XLSX); the permission is checked again when the
    worker runs the job. The file re-imports with the default mapping."""
    try:
        await require_class_access(ctx.session, ctx.access, body.class_id, "ro")
        await ci_search_statement(
            ctx.session,
            ctx.access,
            CiSearchCriteria(class_ids=[body.class_id], filter=body.filter, q=body.q),
        )
    except (CmdbError, FilterError) as exc:
        raise cmdb_http_error(exc) from exc
    payload = CmdbExportPayload(
        user_id=ctx.user.id,
        class_id=body.class_id,
        filter=body.filter,
        q=body.q,
        file_format=body.file_format,
    )
    return await _enqueue_limited(
        ctx, CMDB_EXPORT_JOB, payload, limit=MAX_ACTIVE_EXPORTS_PER_USER, what="Exporte"
    )


@router.post("/bulk-jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_bulk(body: CmdbBulkIn, ctx: Ctx) -> JobAccepted:
    """Queue a bulk edit: CIs without ``rw`` are skipped (and reported) when the job runs."""
    problem = await invalid_state_ids(ctx.session, body.deployment_state_id, body.incident_state_id)
    if problem:
        raise _bad_request("invalid_state", problem)
    payload = CmdbBulkPayload(user_id=ctx.user.id, **body.model_dump())
    return await _enqueue_limited(
        ctx, CMDB_BULK_JOB, payload, limit=MAX_ACTIVE_BULKS_PER_USER, what="Sammelbearbeitungen"
    )


__all__ = ["MAX_ACTIVE_IMPORTS_PER_USER", "router"]
