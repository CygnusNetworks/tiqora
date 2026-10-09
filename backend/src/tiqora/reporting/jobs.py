"""Background report runs and XLSX/CSV exports (plan R9).

Both jobs evaluate everything again when the worker runs them: report access
(``reporting.access``), the viewer's queue scope (``run_report`` calls
``ticket_query_scope`` for ``payload.user_id``) and the definition. The run
job runs in job mode (``query_timeout_seconds`` per statement) and writes the
result cache with the same key builder as the API, so the same request asked
again afterwards is answered inline. The export job never uses the cache and
caps the rows at ``reporting.export_row_limit``.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.timezones import resolve_user_time_zone, valid_time_zone
from tiqora.extensions.core import get_registry
from tiqora.filters import FilterError
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import MAX_RESULT_CHARS
from tiqora.reporting.access import has_report_access
from tiqora.reporting.cache import ReportCache, cache_backend_from_settings
from tiqora.reporting.catalogue import BUILTIN_REPORTS
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.export import report_sheet, write_report_csv, write_xlsx_workbook
from tiqora.reporting.schemas import ReportExportIn, ReportResultOut, ReportRunIn
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.znuny.sysconfig import SysConfig

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


class ReportRunPayload(BaseModel):
    user_id: int
    run: ReportRunIn


class ReportExportPayload(BaseModel):
    user_id: int
    export: ReportExportIn


class RequestError(Exception):
    """A request that cannot run (unknown report, bad field); ``message``/``path`` as for 422."""

    def __init__(self, message: str, *, path: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.status_code = status_code


def export_slug(title: str) -> str:
    """File name stem of an export: ``"Zeit für Kunden"`` -> ``"zeit-fuer-kunden"``."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower().translate(_UMLAUTS)).strip("-") or "bericht"


async def prepare_run(
    session: AsyncSession, user_id: int, body: ReportRunIn
) -> tuple[ReportDefinition, RunParams, str | None]:
    """Definition, run parameters and report key of a request (API and jobs).

    Zone: request > definition > the viewer's zone. ``dimension_choices`` keys
    are dimension indices; a non-integer key raises :class:`RequestError`.
    """
    if (body.report_key is None) == (body.definition is None):
        raise RequestError(
            "Bitte genau eines von „report_key“ und „definition“ angeben.", path="report_key"
        )
    if body.report_key is not None:
        report = get_registry().get(BUILTIN_REPORTS, body.report_key)
        if report is None:
            raise RequestError(
                f"Unbekannter Bericht „{body.report_key}“.", path="report_key", status_code=404
            )
        definition = report.definition
    else:
        assert body.definition is not None
        definition = body.definition
    choices: dict[int, str] = {}
    for raw_key, choice in body.dimension_choices.items():
        try:
            index = int(raw_key)
        except ValueError:
            raise RequestError(
                "Der Schlüssel muss eine Dimensionsnummer sein.",
                path=f"dimension_choices.{raw_key}",
            ) from None
        choices[index] = choice
    zone = (
        valid_time_zone(body.time_zone)
        or valid_time_zone(definition.time_zone)
        or await resolve_user_time_zone(session, SysConfig(session), user_id)
    )
    params = RunParams(
        time_range=body.time_range,
        grain=body.grain,
        time_zone=zone,
        parameters=dict(body.parameters),
        dimension_choices=choices,
    )
    return definition, params, body.report_key


@asynccontextmanager
async def _job_cache(
    settings: Settings, reporting: ReportingSettings
) -> AsyncIterator[ReportCache]:
    """The result cache of the API (same key builder), on its own Redis client."""
    backend: Any = cache_backend_from_settings(settings)
    try:
        yield ReportCache(backend, reporting.cache_seconds)
    finally:
        close = getattr(backend, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning("report_job_cache_close_failed", error=str(exc))


async def _checked(
    session: AsyncSession, ctx: JobContext, user_id: int, body: ReportRunIn
) -> tuple[ReportingSettings, ReportDefinition, RunParams, str | None]:
    reporting = await load_reporting_settings(session, ctx.settings)
    if not await has_report_access(session, user_id, reporting):
        raise JobFailed(NO_ACCESS_JOB_MESSAGE)
    try:
        definition, params, key = await prepare_run(session, user_id, body)
    except RequestError as exc:
        raise JobFailed(f"Der Bericht ist nicht mehr gültig: {exc.message}") from exc
    return reporting, definition, params, key


async def _run(
    session: AsyncSession,
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
        return await run_report(
            session,
            defn=definition,
            params=params,
            user_id=user_id,
            report_key=key,
            reporting=reporting,
            **kwargs,
        )
    except (DefinitionError, FilterError) as exc:
        raise JobFailed(f"Der Bericht ist nicht mehr gültig: {exc.message}") from exc
    except ReportQueryTimeout as exc:
        raise JobFailed(timeout_message) from exc


async def run_report_job(ctx: JobContext, payload: ReportRunPayload) -> dict[str, Any]:
    """Run one report in the background; the result is the job result (and cached)."""
    body = payload.run
    await ctx.progress(0, None, "Bericht wird berechnet …")
    async with ctx.session_factory() as session:
        reporting, definition, params, key = await _checked(session, ctx, payload.user_id, body)
        async with _job_cache(ctx.settings, reporting) as cache:
            result = await _run(
                session,
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
    kind="core.report_run",
    payload_model=ReportRunPayload,
    run=run_report_job,
    title_key="jobs.kinds.core.report_run",
    timeout_seconds=1800,
)
REPORT_EXPORT_JOB = JobHandler(
    kind="core.report_export",
    payload_model=ReportExportPayload,
    run=run_export_job,
    title_key="jobs.kinds.core.report_export",
    timeout_seconds=3600,
)
