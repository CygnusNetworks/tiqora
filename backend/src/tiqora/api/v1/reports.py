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

from typing import Annotated, Any, Final, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession, get_redis
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import get_registry
from tiqora.filters import FilterError
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import count_active_jobs, enqueue_job, lock_user_job_quota
from tiqora.reporting.access import require_report_access
from tiqora.reporting.cache import CacheBackend, ReportCache, read_cache_generation
from tiqora.reporting.catalogue import BUILTIN_REPORTS, BuiltinReport
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.engine import QueryBudget, estimate_rows, execute_and_store, plan_report
from tiqora.reporting.jobs import (
    REPORT_EXPORT_JOB,
    REPORT_RUN_JOB,
    TIMEOUT_MESSAGE,
    ReportExportPayload,
    ReportRunPayload,
    RequestError,
    prepare_run,
)
from tiqora.reporting.schemas import (
    BuiltinReportOut,
    FactsStatusOut,
    ReportCatalogueOut,
    ReportExportIn,
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


def _builtin_out(report: BuiltinReport) -> BuiltinReportOut:
    return BuiltinReportOut(
        key=report.key,
        category=report.category,
        title_key=report.title_key,
        description_key=report.description_key,
        definition=report.definition,
        overview=report.overview,
    )


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
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportCatalogueOut:
    """The built-in reports (catalogue order) and the readiness of the report data."""
    await _access(session, user, settings)
    reports = [_builtin_out(r) for r in get_registry().items(BUILTIN_REPORTS)]
    return ReportCatalogueOut(reports=reports, facts=await facts_status(session))


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


@router.get("/{report_key}", response_model=BuiltinReportOut)
async def get_report(
    report_key: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> BuiltinReportOut:
    await _access(session, user, settings)
    report = get_registry().get(BUILTIN_REPORTS, report_key)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"Unbekannter Bericht „{report_key}“."},
        )
    return _builtin_out(report)


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
