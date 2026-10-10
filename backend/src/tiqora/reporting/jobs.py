"""Background report runs and XLSX/CSV exports (plan R9).

Both jobs evaluate everything again when the worker runs them: report access
(``reporting.access``), the viewer's queue scope (``run_report`` calls
``ticket_query_scope`` for ``payload.user_id``) and the definition. The run
job runs in job mode (``query_timeout_seconds`` per statement) and writes the
result cache with the same key builder as the API, so the same request asked
again afterwards is answered inline. The export job never uses the cache and
caps the rows at ``reporting.export_row_limit``.

Cancelling: while a job computes, :func:`_until_cancelled` polls
``cancel_requested`` every :data:`CANCEL_POLL_SECONDS`; a cancel cancels the
computation. A statement in flight is cut off client-side and its connection is
invalidated (``engine._fetch``), the next statement never starts, and the job ends
``cancelled`` — so a cancelled job really frees its slot of the per-user cap.
(The server may finish the cut-off statement up to its statement timeout.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.filters import Filter, FilterError
from tiqora.jobs.context import JobCancelled, JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import MAX_RESULT_CHARS
from tiqora.reporting.access import has_report_access
from tiqora.reporting.cache import (
    ReportCache,
    cache_backend_from_settings,
    read_cache_generation,
)
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.export import report_sheet, write_report_csv, write_xlsx_workbook
from tiqora.reporting.run_service import (
    REPORT_EXPORT_KIND,
    REPORT_RUN_KIND,
    ReportExportPayload,
    ReportRunPayload,
    RequestError,
    prepare_run,
)
from tiqora.reporting.schemas import ReportResultOut, ReportRunIn
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.sql import ReportQueryTimeout

logger = structlog.get_logger(__name__)

NO_ACCESS_JOB_MESSAGE: Final = "Für Berichte fehlt die Berechtigung."
TIMEOUT_MESSAGE: Final = (
    "Der Bericht hat zu lange gebraucht. "
    "Bitte den Zeitraum verkleinern oder ihn als Export starten."
)
EXPORT_TIMEOUT_MESSAGE: Final = "Der Export hat zu lange gebraucht. Bitte den Zeitraum verkleinern."
RESULT_TOO_LARGE_MESSAGE: Final = (
    "Das Ergebnis ist zu groß für die Anzeige. Bitte den Bericht als Export starten."
)
XLSX_CONTENT_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_CONTENT_TYPE: Final = "text/csv; charset=utf-8"
_UMLAUTS: Final = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
#: seconds between two looks at ``cancel_requested`` while a report computes
CANCEL_POLL_SECONDS: float = 1.0


def export_slug(title: str) -> str:
    """File name stem of an export: ``"Zeit für Kunden"`` -> ``"zeit-fuer-kunden"``."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower().translate(_UMLAUTS)).strip("-") or "bericht"


@asynccontextmanager
async def _job_cache_backend(settings: Settings) -> AsyncIterator[Any]:
    """The Redis client of the API's result cache (same key builder), closed afterwards."""
    backend: Any = cache_backend_from_settings(settings)
    try:
        yield backend
    finally:
        close = getattr(backend, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning("report_job_cache_close_failed", error=str(exc))


async def _checked(
    session: AsyncSession,
    ctx: JobContext,
    user_id: int,
    body: ReportRunIn,
    extra_filter: Filter | None = None,
) -> tuple[ReportingSettings, ReportDefinition, RunParams, str | None]:
    reporting = await load_reporting_settings(session, ctx.settings)
    if not await has_report_access(session, user_id, reporting):
        raise JobFailed(NO_ACCESS_JOB_MESSAGE)
    try:
        definition, params, key = await prepare_run(
            session, user_id, body, extra_filter=extra_filter
        )
    except (RequestError, DefinitionError) as exc:
        raise JobFailed(f"Der Bericht ist nicht mehr gültig: {exc.message}") from exc
    return reporting, definition, params, key


async def _until_cancelled[R](ctx: JobContext, work: Awaitable[R]) -> R:
    """Run ``work``; when the job's cancel is requested meanwhile, cancel it and raise
    :class:`JobCancelled` (the engine invalidates a connection cut off mid-statement)."""
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            try:
                await ctx.check_cancelled()
            except JobCancelled:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                raise
    finally:
        if not task.done():  # the job itself was cancelled (runner timeout, shutdown)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


async def _run(
    session: AsyncSession,
    ctx: JobContext,
    *,
    user_id: int,
    definition: ReportDefinition,
    params: RunParams,
    key: str | None,
    reporting: ReportingSettings,
    timeout_message: str,
    **kwargs: Any,
) -> ReportResultOut:
    try:
        return await _until_cancelled(
            ctx,
            run_report(
                session,
                defn=definition,
                params=params,
                user_id=user_id,
                report_key=key,
                reporting=reporting,
                **kwargs,
            ),
        )
    except (DefinitionError, FilterError) as exc:
        raise JobFailed(f"Der Bericht ist nicht mehr gültig: {exc.message}") from exc
    except ReportQueryTimeout as exc:
        raise JobFailed(timeout_message) from exc


async def run_report_job(ctx: JobContext, payload: ReportRunPayload) -> dict[str, Any]:
    """Run one report in the background; the result is the job result (and cached)."""
    body = payload.run
    await ctx.progress(0, None, "Bericht wird berechnet …")
    async with _job_cache_backend(ctx.settings) as backend:
        # read before the run's first database read (see tiqora.reporting.cache)
        generation = await read_cache_generation(backend)
        async with ctx.session_factory() as session:
            reporting, definition, params, key = await _checked(
                session, ctx, payload.user_id, body, payload.extra_filter
            )
            cache = ReportCache(backend, reporting.cache_seconds, generation=generation)
            result = await _run(
                session,
                ctx,
                user_id=payload.user_id,
                definition=definition,
                params=params,
                key=key,
                reporting=reporting,
                timeout_message=TIMEOUT_MESSAGE,
                cache=cache,
                use_cache=not body.no_cache,
                compare_previous=body.compare_previous,
            )
    out = result.model_dump(mode="json")
    if len(json.dumps(out)) > MAX_RESULT_CHARS:
        raise JobFailed(RESULT_TOO_LARGE_MESSAGE)
    return out


async def run_export_job(ctx: JobContext, payload: ReportExportPayload) -> dict[str, Any]:
    """Run one report without cache and row cap ``export_row_limit``; store it as a file."""
    body = payload.export
    await ctx.progress(0, None, "Bericht wird berechnet …")
    async with ctx.session_factory() as session:
        reporting, definition, params, key = await _checked(session, ctx, payload.user_id, body)
        result = await _run(
            session,
            ctx,
            user_id=payload.user_id,
            definition=definition,
            params=params,
            key=key,
            reporting=reporting,
            timeout_message=EXPORT_TIMEOUT_MESSAGE,
            row_cap=reporting.export_row_limit,
            use_cache=False,
            compare_previous=body.compare_previous,
        )
    rows = len(result.rows)
    await ctx.progress(rows, rows, "Datei wird gespeichert …")
    slug = export_slug(body.title)
    if body.format == "csv":
        data = write_report_csv(
            result,
            column_labels=body.column_labels,
            value_labels=body.value_labels,
            totals_label=body.totals_label,
        )
        await ctx.add_file(f"{slug}.csv", CSV_CONTENT_TYPE, data)
    else:
        sheet = report_sheet(
            result,
            title=body.title,
            column_labels=body.column_labels,
            value_labels=body.value_labels,
            totals_label=body.totals_label,
            info_lines=body.info_lines,
        )
        await ctx.add_file(f"{slug}.xlsx", XLSX_CONTENT_TYPE, write_xlsx_workbook([sheet]))
    return {"rows": rows}


REPORT_RUN_JOB = JobHandler(
    kind=REPORT_RUN_KIND,
    payload_model=ReportRunPayload,
    run=run_report_job,
    title_key="jobs.kinds.core.report_run",
    timeout_seconds=1800,
    result_private=True,
)
REPORT_EXPORT_JOB = JobHandler(
    kind=REPORT_EXPORT_KIND,
    payload_model=ReportExportPayload,
    run=run_export_job,
    title_key="jobs.kinds.core.report_export",
    timeout_seconds=3600,
    result_private=True,
)
