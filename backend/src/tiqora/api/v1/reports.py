"""Reports API: catalogue, inline or background runs, XLSX/CSV export jobs (plan R9).

Every route needs a logged-in, valid agent (``CurrentUser``) and report access
(``reporting.access``). What a report shows is limited to the viewer's queues
by the compiler; inline definitions are therefore safe to run.

Inline runs (``POST /reports/run``) share one time budget of at most 75 s over
all their queries (row estimate included), below nginx's 90 s ``/api/``
timeout; a used-up budget answers 504 with a JSON message. Large reports
(row estimate above ``inline_row_threshold``, ``ticket_events`` over more than
366 days, or ``force_job``) run as background jobs (202); a user may have at most
:data:`MAX_ACTIVE_REPORT_JOBS_PER_USER` report runs and exports queued or running
(429 beyond that). Job results are private to their creator (``result_private``).
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession, get_redis
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import get_registry
from tiqora.filters import FilterError
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import count_active_jobs, enqueue_job, lock_user_job_quota
from tiqora.permissions.sharing import ShareGroupError
from tiqora.reporting.access import require_report_access
from tiqora.reporting.cache import CacheBackend, ReportCache, read_cache_generation
from tiqora.reporting.catalogue import BUILTIN_REPORTS, BuiltinReport
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition, validate_definition
from tiqora.reporting.engine import QueryBudget, estimate_rows, execute_and_store, plan_report
from tiqora.reporting.jobs import (
    REPORT_EXPORT_JOB,
    REPORT_RUN_JOB,
    TIMEOUT_MESSAGE,
    ReportExportPayload,
    ReportRunPayload,
    RequestError,
    export_slug,
    prepare_run,
)
from tiqora.reporting.labels import resolve_labels
from tiqora.reporting.report_file import (
    ReportFileImportIn,
    ReportFileOut,
    dump_report_file,
    parse_report_file,
)
from tiqora.reporting.rights import ReportRights, report_rights
from tiqora.reporting.saved import (
    SAVED_KEY_RE,
    ReportForbidden,
    ReportGone,
    ReportNotFound,
    ReportVersionConflict,
    SavedReport,
    SavedReportIn,
    SavedReportUpdateIn,
    can_edit_report,
    create_saved_report,
    delete_saved_report,
    get_saved_report,
    update_saved_report,
    visible_saved_reports,
)
from tiqora.reporting.schemas import (
    FactsStatusOut,
    ReportCatalogueOut,
    ReportExportIn,
    ReportOut,
    ReportOwnerOut,
    ReportRunIn,
    ReportRunOut,
    ReportSourceOut,
    SourceDimensionOut,
    SourceMeasureOut,
)
from tiqora.reporting.semantic import REPORT_SOURCES, ReportSource
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.reporting.state import facts_status

router = APIRouter(prefix="/reports", tags=["reports"])
sources_router = APIRouter(prefix="/reporting", tags=["reports"])

#: ``ticket_events`` windows longer than this always run as a job
EVENTS_INLINE_MAX_DAYS: Final = 366
#: queued or running report runs + exports one user may have at once (inline runs not counted)
MAX_ACTIVE_REPORT_JOBS_PER_USER: Final = 3


async def get_report_cache(redis_client: Annotated[Any, Depends(get_redis)]) -> CacheBackend:
    """Backend of the report result cache (Redis); tests override it with an in-memory one."""
    return cast(CacheBackend, redis_client)


ReportCacheBackend = Annotated[CacheBackend, Depends(get_report_cache)]


async def _access(
    session: AsyncSession, user: AuthenticatedUser, settings: AppSettings
) -> ReportingSettings:
    reporting = await load_reporting_settings(session, settings)
    await require_report_access(session, user.id, reporting)
    return reporting


def _unprocessable(message: str, path: str, status_code: int = 422) -> NoReturn:
    raise HTTPException(status_code=status_code, detail={"message": message, "path": path})


async def _prepare(
    session: AsyncSession, user: AuthenticatedUser, body: ReportRunIn
) -> tuple[ReportDefinition, RunParams, str | None]:
    try:
        return await prepare_run(session, user.id, body)
    except RequestError as exc:
        _unprocessable(exc.message, exc.path, exc.status_code)


def _builtin_out(report: BuiltinReport) -> ReportOut:
    return ReportOut(
        key=report.key,
        kind="builtin",
        category=report.category,
        title_key=report.title_key,
        description_key=report.description_key,
        definition=report.definition,
        overview=report.overview,
        read_only=True,
    )


def _saved_out(
    report: SavedReport, rights: ReportRights, user_id: int, owner_names: dict[str, str]
) -> ReportOut:
    return ReportOut(
        key=str(report.id),
        kind="saved",
        id=report.id,
        name=report.name,
        description=report.description,
        definition=report.definition,
        definition_error=report.definition_error,
        read_only=not can_edit_report(report, user_id, rights),
        owner=ReportOwnerOut(
            user_id=report.owner_user_id,
            name=owner_names.get(str(report.owner_user_id), str(report.owner_user_id)),
        ),
        visibility=report.visibility,
        group_ids=list(report.group_ids),
        version=report.version,
        copied_from=report.copied_from,
        changed_at=report.changed_at,
    )


NOT_FOUND_MESSAGE: Final = "Bericht nicht gefunden."


def _store_error(exc: Exception) -> NoReturn:
    """Map the saved-report store's exceptions to the API's error bodies."""
    if isinstance(exc, ReportNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"message": NOT_FOUND_MESSAGE}
        ) from None
    if isinstance(exc, ReportGone):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"message": f"Der Bericht „{exc.name}“ wurde gelöscht."},
        ) from None
    if isinstance(exc, ReportForbidden):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"message": exc.message}
        ) from None
    if isinstance(exc, ReportVersionConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Der Bericht wurde inzwischen geändert. Bitte neu laden.",
                "current_version": exc.current_version,
            },
        ) from None
    if isinstance(exc, ShareGroupError | DefinitionError):
        _unprocessable(exc.message, exc.path)
    raise exc


_STORE_ERRORS = (
    ReportNotFound,
    ReportGone,
    ReportForbidden,
    ReportVersionConflict,
    ShareGroupError,
    DefinitionError,
)


async def _owner_names(session: AsyncSession, reports: list[SavedReport]) -> dict[str, str]:
    ids = sorted({r.owner_user_id for r in reports})
    return await resolve_labels(session, "user", ids) if ids else {}


def _source_out(src: ReportSource) -> ReportSourceOut:
    return ReportSourceOut(
        key=src.key,
        label_key=src.label_key,
        dimensions=[
            SourceDimensionOut(
                key=d.key,
                label_key=d.label_key,
                kind=d.kind,
                labels=d.labels,
                i18n_values=d.i18n_values,
                filter_type=d.filter_type,
                options_source=d.options_source,
                time_field=d.key in src.time_fields,
            )
            for d in src.dimensions
        ],
        measures=[
            SourceMeasureOut(key=m.key, label_key=m.label_key, agg=m.agg, unit=m.unit)
            for m in src.measures
        ],
        filter_fields=[
            SourceDimensionOut(
                key=f.key,
                label_key=f.label_key,
                kind=f.filter_type,
                labels="none",
                i18n_values=None,
                filter_type=f.filter_type,
                options_source=None,
                time_field=False,
            )
            for f in src.filter_fields
        ],
        default_time_field=src.default_time_field,
    )


async def _check_job_limit(session: AsyncSession, user_id: int) -> None:
    """429 when the user already has the maximum of report jobs queued or running.

    Must start a fresh transaction (callers roll back first); the user row lock
    serialises concurrent requests of one user until their enqueue is committed.
    """
    await lock_user_job_quota(session, user_id)
    active = 0
    for kind in (REPORT_RUN_JOB.kind, REPORT_EXPORT_JOB.kind):
        # a cancelled report job stops within about a second: it no longer counts
        active += await count_active_jobs(
            session, kind=kind, created_by=user_id, include_cancelling=False
        )
    if active >= MAX_ACTIVE_REPORT_JOBS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": (
                    f"Es laufen bereits {MAX_ACTIVE_REPORT_JOBS_PER_USER} Berichte im "
                    "Hintergrund. Bitte warte, bis einer fertig ist."
                )
            },
        )


def _accepted(job_id: int, job_status: str) -> JobAccepted:
    return JobAccepted(job_id=job_id, status="running" if job_status == "running" else "queued")


@router.get("", response_model=ReportCatalogueOut)
async def list_reports(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    all_: Annotated[bool, Query(alias="all")] = False,
) -> ReportCatalogueOut:
    """Built-in reports (catalogue order), then the saved reports the viewer may open (by name).

    ``all=true`` additionally lists other people's private reports; admins only.
    """
    reporting = await _access(session, user, settings)
    rights = await report_rights(session, user.id, reporting)
    saved = await visible_saved_reports(
        session, user.id, admin=rights.admin, include_others_private=all_ and rights.admin
    )
    names = await _owner_names(session, saved)
    reports = [_builtin_out(r) for r in get_registry().items(BUILTIN_REPORTS)]
    reports.extend(_saved_out(r, rights, user.id, names) for r in saved)
    return ReportCatalogueOut(reports=reports, facts=await facts_status(session))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ReportOut)
async def create_report(
    body: SavedReportIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportOut:
    """Save a report; ``visibility`` other than ``private`` needs the share right."""
    reporting = await _access(session, user, settings)
    rights = await report_rights(session, user.id, reporting)
    try:
        created = await create_saved_report(session, user.id, rights, body, registry=get_registry())
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = _saved_out(created, rights, user.id, await _owner_names(session, [created]))
    await session.commit()
    return out


@router.post("/import-file", response_model=ReportFileOut)
async def import_report_file(
    body: ReportFileImportIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportFileOut:
    """Parse and validate a report file (JSON or YAML); saves nothing."""
    await _access(session, user, settings)
    try:
        parsed = parse_report_file(body.content)
        try:
            validate_definition(parsed.definition, get_registry())
        except DefinitionError as exc:
            raise DefinitionError(exc.message, path="definition." + exc.path) from exc
    except DefinitionError as exc:
        _unprocessable(exc.message, exc.path)
    return parsed


@router.post(
    "/run",
    response_model=ReportRunOut,
    responses={202: {"model": ReportRunOut, "description": "Runs as a background job."}},
)
async def run_report_endpoint(
    body: ReportRunIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    cache_backend: ReportCacheBackend,
) -> ReportRunOut | JSONResponse:
    """Run a built-in report (``report_key``) or an inline ``definition``.

    200 with ``result`` (inline or from the cache), or 202 with ``job`` when
    the report is large; poll ``GET /jobs/{id}``, the job result is the report.
    """
    # the cache generation is read before this request's first database read: a purge
    # (GDPR erasure, rebuild) after it lets nothing this request computes be found again
    generation = await read_cache_generation(cache_backend)
    reporting = await _access(session, user, settings)
    definition, params, report_key = await _prepare(session, user, body)
    cache = ReportCache(cache_backend, reporting.cache_seconds, generation=generation)
    # one shared deadline for every query of this request (estimate included)
    budget = QueryBudget.inline(reporting)
    try:
        plan = await plan_report(
            session,
            defn=definition,
            params=params,
            user_id=user.id,
            reporting=reporting,
            compare_previous=body.compare_previous,
        )
        if not body.no_cache:
            hit = await cache.get(plan.cache_key)
            if hit is not None:
                return ReportRunOut(
                    result=hit.model_copy(update={"report_key": report_key, "facts": plan.facts})
                )
        window = plan.compiled.window
        as_job = (
            body.force_job
            or (
                plan.compiled.source.key == "ticket_events"
                and window is not None
                and (window.end_date - window.start_date).days + 1 > EVENTS_INLINE_MAX_DAYS
            )
            or await estimate_rows(session, plan.compiled, reporting=reporting, budget=budget)
            > reporting.inline_row_threshold
        )
        if as_job:
            # PostgreSQL: the estimate's SET LOCAL statement_timeout (possibly ~1 s near
            # the deadline) lives until the transaction ends; never let it hit the enqueue
            await session.rollback()
            await _check_job_limit(session, user.id)
            job = await enqueue_job(
                session,
                kind=REPORT_RUN_JOB.kind,
                payload=ReportRunPayload(user_id=user.id, run=body),
                created_by=user.id,
            )
            await session.commit()
            out = ReportRunOut(job=_accepted(job.id, job.status))
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED, content=out.model_dump(mode="json")
            )
        # the plan above is reused: no second scope/state/compile, no second lookup
        result = await execute_and_store(
            session,
            plan,
            report_key=report_key,
            reporting=reporting,
            cache=None if body.no_cache else cache,
            budget=budget,
        )
    except (DefinitionError, FilterError) as exc:
        _unprocessable(exc.message, exc.path)
    except ReportQueryTimeout:
        # the connection may be invalidated; get_db closes the session
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail={"message": TIMEOUT_MESSAGE}
        ) from None
    return ReportRunOut(result=result)


@router.post("/export", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def export_report(
    body: ReportExportIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> JobAccepted:
    """Queue an XLSX/CSV export (always a job; the file is ``<title slug>.xlsx|.csv``).

    The request is checked now (422 for invalid definitions or parameters);
    the worker evaluates access and the viewer's scope again when it runs.
    """
    reporting = await _access(session, user, settings)
    definition, params, _key = await _prepare(session, user, body)
    try:
        # scope + compile only: no report query runs here
        await plan_report(
            session,
            defn=definition,
            params=params,
            user_id=user.id,
            reporting=reporting,
            row_cap=reporting.export_row_limit,
            compare_previous=body.compare_previous,
        )
    except (DefinitionError, FilterError) as exc:
        _unprocessable(exc.message, exc.path)
    await session.rollback()
    await _check_job_limit(session, user.id)
    job = await enqueue_job(
        session,
        kind=REPORT_EXPORT_JOB.kind,
        payload=ReportExportPayload(user_id=user.id, export=body),
        created_by=user.id,
    )
    await session.commit()
    return _accepted(job.id, job.status)


async def _load_for_view(
    session: AsyncSession, user: AuthenticatedUser, reporting: ReportingSettings, report_key: str
) -> tuple[ReportOut, ReportRights]:
    rights = await report_rights(session, user.id, reporting)
    if report_key.startswith("builtin."):
        report = get_registry().get(BUILTIN_REPORTS, report_key)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": f"Unbekannter Bericht „{report_key}“.", "path": "report_key"},
            )
        return _builtin_out(report), rights
    if not SAVED_KEY_RE.fullmatch(report_key):
        _store_error(ReportNotFound(report_key))
    try:
        saved = await get_saved_report(session, int(report_key), user.id, admin=rights.admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    names = await _owner_names(session, [saved])
    return _saved_out(saved, rights, user.id, names), rights


@router.get("/{report_key}/file")
async def download_report_file(
    report_key: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    fmt: Annotated[Literal["json", "yaml"], Query(alias="format")] = "json",
) -> Response:
    """The report as a JSON / YAML file (built-ins: the report key is the name)."""
    reporting = await _access(session, user, settings)
    report, _rights = await _load_for_view(session, user, reporting, report_key)
    if report.definition is None:
        _unprocessable(
            report.definition_error or "Der Bericht kann nicht gelesen werden.", "definition"
        )
    name = report.name or report.key
    content = dump_report_file(
        ReportFileOut(
            name=name, description=report.description or "", definition=report.definition
        ),
        fmt,
    )
    return Response(
        content=content,
        media_type="application/json" if fmt == "json" else "application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{export_slug(name)}.{fmt}"'},
    )


@router.get("/{report_key}", response_model=ReportOut)
async def get_report(
    report_key: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportOut:
    """A built-in report (404 for an unknown key) or a saved one by id.

    Unknown and invisible saved reports are both 404; a deleted one is 410.
    """
    reporting = await _access(session, user, settings)
    report, _rights = await _load_for_view(session, user, reporting, report_key)
    return report


@router.put("/{report_id}", response_model=ReportOut)
async def update_report(
    report_id: int,
    body: SavedReportUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ReportOut:
    """Replace a saved report completely (every field is written).

    ``version`` is the optimistic lock (409 with ``current_version``). The stored sharing
    may be kept unchanged without the share right; changing it needs that right.
    """
    reporting = await _access(session, user, settings)
    rights = await report_rights(session, user.id, reporting)
    try:
        updated = await update_saved_report(
            session, report_id, user.id, rights, body, registry=get_registry()
        )
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = _saved_out(updated, rights, user.id, await _owner_names(session, [updated]))
    await session.commit()
    return out


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> Response:
    """Soft-delete a saved report (its owner always may, admins any)."""
    reporting = await _access(session, user, settings)
    rights = await report_rights(session, user.id, reporting)
    try:
        await delete_saved_report(session, report_id, user.id, rights)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@sources_router.get("/sources", response_model=list[ReportSourceOut])
async def list_sources(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[ReportSourceOut]:
    """Report sources with their dimensions, measures and filter fields (builder, filters)."""
    await _access(session, user, settings)
    return [_source_out(s) for s in get_registry().items(REPORT_SOURCES)]


@sources_router.get("/status", response_model=FactsStatusOut)
async def get_facts_status(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> FactsStatusOut:
    """Whether the report data is ready (banner while the first build runs)."""
    await _access(session, user, settings)
    return await facts_status(session)
