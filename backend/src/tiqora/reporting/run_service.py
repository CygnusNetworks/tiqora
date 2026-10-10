"""Run a report by key or inline definition: preparation, deferral, enqueueing (plan D6).

One service behind ``POST /reports/run``, the report jobs and (later) dashboard
widgets. A report is addressed by ``builtin.<name>`` or a saved report's decimal id;
everything is evaluated in the *viewer's* permission scope (``plan_report`` calls
``ticket_query_scope`` for ``user_id``), so a shared report never shows the owner's
numbers. The result cache key contains the user's scope, the effective definition and
the narrowing filters.

Import direction: ``reporting/jobs.py`` imports this module (never the other way round),
so the job payload models and the two job kind strings live here.

Narrowing filters (``RunOverrides.extra_filter`` and the dashboard ``common`` keys) are
ANDed into the compiled WHERE after the scope clause; they can only narrow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal

from fastapi import status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import get_settings
from tiqora.domain.timezones import resolve_user_time_zone, valid_time_zone
from tiqora.extensions.core import get_registry
from tiqora.filters import AllOf, Condition, Filter
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import count_active_jobs, enqueue_job, lock_user_job_quota
from tiqora.permissions.engine import PermissionEngine
from tiqora.reporting.access import require_report_access
from tiqora.reporting.cache import ReportCache
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition, TimeRange
from tiqora.reporting.engine import QueryBudget, estimate_rows, execute_and_store, plan_report
from tiqora.reporting.saved import (
    ReportGone,
    ReportNotFound,
    SavedReport,
    resolve_report_ref,
)
from tiqora.reporting.schemas import (
    ReportExportIn,
    ReportResultOut,
    ReportRunIn,
)
from tiqora.reporting.semantic import REPORT_SOURCES, ReportSource
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.sql import Grain
from tiqora.znuny.sysconfig import SysConfig

#: job kinds (the handlers in ``reporting/jobs.py`` register under these)
REPORT_RUN_KIND: Final = "core.report_run"
REPORT_EXPORT_KIND: Final = "core.report_export"

#: ``ticket_events`` windows longer than this always run as a job
EVENTS_INLINE_MAX_DAYS: Final = 366
#: queued or running report runs + exports one user may have at once (inline runs not counted)
MAX_ACTIVE_REPORT_JOBS_PER_USER: Final = 3

NOT_FOUND_MESSAGE: Final = "Bericht nicht gefunden."
TOO_MANY_JOBS_MESSAGE: Final = (
    f"Es laufen bereits {MAX_ACTIVE_REPORT_JOBS_PER_USER} Berichte im "
    "Hintergrund. Bitte warte, bis einer fertig ist."
)

CommonFilterKey = Literal[
    "queue",
    "state_type",
    "priority",
    "type",
    "service",
    "sla",
    "owner",
    "customer",
    "customer_user",
    "channel",
]
COMMON_FILTER_KEYS: Final[tuple[CommonFilterKey, ...]] = (
    "queue",
    "state_type",
    "priority",
    "type",
    "service",
    "sla",
    "owner",
    "customer",
    "customer_user",
    "channel",
)


class ReportRunPayload(BaseModel):
    user_id: int
    run: ReportRunIn
    #: narrowing filter in the source's field keys (dashboards); absent in wave-1 payloads
    extra_filter: Filter | None = None


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


class TooManyReportJobs(Exception):  # noqa: N818 - plan D6 name
    """The user already has the maximum of report jobs queued or running (429)."""

    def __init__(self) -> None:
        super().__init__(TOO_MANY_JOBS_MESSAGE)
        self.message = TOO_MANY_JOBS_MESSAGE


@dataclass(frozen=True, slots=True)
class RunOverrides:
    """What a caller may change when running a report (HTTP body, dashboard widget, job)."""

    time_range: TimeRange | None = None
    grain: Grain | None = None
    time_zone: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    dimension_choices: Mapping[int, str] = field(default_factory=dict)
    compare_previous: bool = False
    no_cache: bool = False
    #: narrowing filter in the SOURCE's field keys (ANDed like the /stats wrappers' extra_filter);
    #: in-process only (dashboards, jobs) - ReportRunIn deliberately has no such field
    extra_filter: Filter | None = None
    #: dashboard-level filters by common key; mapped through ``ReportSource.common_fields``
    common: Mapping[str, Sequence[str | int]] = field(default_factory=dict)

    @classmethod
    def from_run_in(cls, body: ReportRunIn) -> RunOverrides:
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
        return cls(
            time_range=body.time_range,
            grain=body.grain,
            time_zone=body.time_zone,
            parameters=dict(body.parameters),
            dimension_choices=choices,
            compare_previous=body.compare_previous,
            no_cache=body.no_cache,
        )


@dataclass(frozen=True, slots=True)
class PreparedRun:
    definition: ReportDefinition
    params: RunParams  # extra_filter = overrides.extra_filter AND common filter
    report_key: str | None
    saved: SavedReport | None
    ignored_common: tuple[str, ...]  # common keys the source cannot apply


def common_filter(
    source: ReportSource, values: Mapping[str, Sequence[str | int]]
) -> tuple[Filter | None, tuple[str, ...]]:
    """Dashboard filters -> a narrowing filter on ``source``, plus the keys it cannot apply.

    Keys with an empty value list are skipped silently; keys outside
    :data:`COMMON_FILTER_KEYS` or not mapped by the source are returned (sorted).
    """
    nodes: list[Condition] = []
    ignored: list[str] = []
    known = set(COMMON_FILTER_KEYS)
    for key in sorted(values):
        raw = values[key]
        # a bare scalar is one value, never a sequence of characters
        chosen = [raw] if isinstance(raw, str | int) else list(raw)
        if not chosen:
            continue
        mapped = source.common_fields.get(key) if key in known else None
        if mapped is None:
            ignored.append(key)
            continue
        nodes.append(Condition(field=mapped, op="in", value=chosen))
    if not nodes:
        return None, tuple(ignored)
    where: Condition | AllOf = nodes[0] if len(nodes) == 1 else AllOf(all=list(nodes))
    return Filter(where=where), tuple(ignored)


def _and_filters(first: Filter | None, second: Filter | None) -> Filter | None:
    nodes = [f.where for f in (first, second) if f is not None and f.where is not None]
    if not nodes:
        return None
    if len(nodes) == 1:
        return Filter(where=nodes[0])
    return Filter(where=AllOf(all=nodes))


async def prepare_ref(
    session: AsyncSession,
    user_id: int,
    *,
    report_key: str | None,
    definition: ReportDefinition | None,
    overrides: RunOverrides,
) -> PreparedRun:
    """Definition, run parameters and report key of a request (API, jobs, dashboards).

    Zone: override > definition > the viewer's zone. A saved report is resolved in the
    viewer's visibility (unknown and invisible both answer 404, a deleted one 410).
    """
    if (report_key is None) == (definition is None):
        raise RequestError(
            "Bitte genau eines von „report_key“ und „definition“ angeben.", path="report_key"
        )
    saved: SavedReport | None = None
    if report_key is not None:
        admin = await PermissionEngine(session).is_admin(user_id)
        try:
            resolved = await resolve_report_ref(session, report_key, user_id, admin=admin)
        except ReportNotFound:
            if report_key.startswith("builtin."):
                raise RequestError(
                    f"Unbekannter Bericht „{report_key}“.",
                    path="report_key",
                    status_code=status.HTTP_404_NOT_FOUND,
                ) from None
            raise RequestError(
                NOT_FOUND_MESSAGE, path="report_key", status_code=status.HTTP_404_NOT_FOUND
            ) from None
        except ReportGone as exc:
            raise RequestError(
                f"Der Bericht „{exc.name}“ wurde gelöscht.",
                path="report_key",
                status_code=status.HTTP_410_GONE,
            ) from None
        defn = resolved.definition
        saved = resolved.saved
    else:
        assert definition is not None
        defn = definition
    source = get_registry().get(REPORT_SOURCES, defn.source)
    if source is None:
        raise DefinitionError(f"Unbekannte Datenbasis „{defn.source}“.", path="source")
    narrowing, ignored = common_filter(source, overrides.common)
    zone = (
        valid_time_zone(overrides.time_zone)
        or valid_time_zone(defn.time_zone)
        or await resolve_user_time_zone(session, SysConfig(session), user_id)
    )
    params = RunParams(
        time_range=overrides.time_range,
        grain=overrides.grain,
        time_zone=zone,
        parameters=dict(overrides.parameters),
        dimension_choices=dict(overrides.dimension_choices),
        extra_filter=_and_filters(overrides.extra_filter, narrowing),
    )
    return PreparedRun(
        definition=defn,
        params=params,
        report_key=report_key,
        saved=saved,
        ignored_common=ignored,
    )


async def prepare_run(
    session: AsyncSession,
    user_id: int,
    body: ReportRunIn,
    *,
    extra_filter: Filter | None = None,
) -> tuple[ReportDefinition, RunParams, str | None]:
    """Wave-1 shape of :func:`prepare_ref` for a request body (API and jobs)."""
    overrides = RunOverrides.from_run_in(body)
    if extra_filter is not None:
        overrides = _with_extra_filter(overrides, extra_filter)
    prepared = await prepare_ref(
        session,
        user_id,
        report_key=body.report_key,
        definition=body.definition,
        overrides=overrides,
    )
    return prepared.definition, prepared.params, prepared.report_key


def _with_extra_filter(overrides: RunOverrides, extra_filter: Filter) -> RunOverrides:
    return replace(overrides, extra_filter=extra_filter)


@dataclass(frozen=True, slots=True)
class RunRefOutcome:
    result: ReportResultOut | None
    #: set -> the caller enqueues (or shows "pending")
    deferred: Literal["forced", "events_window", "estimate"] | None
    #: effective narrowing filter a job must carry
    extra_filter: Filter | None
    ignored_common: tuple[str, ...]


async def run_report_ref(
    session: AsyncSession,
    *,
    user_id: int,
    report_key: str | None,
    definition: ReportDefinition | None,
    overrides: RunOverrides,
    reporting: ReportingSettings,
    cache: ReportCache | None,
    budget: QueryBudget,
    force_job: bool = False,
) -> RunRefOutcome:
    """Prepare, plan, look in the cache, decide inline vs job, execute.

    Raises :class:`RequestError`, ``DefinitionError``, ``FilterError`` and
    ``ReportQueryTimeout``; the caller maps them. Report access is enforced here too
    (403 ``HTTPException``, like the routes), so a direct caller cannot skip it.
    """
    await require_report_access(session, user_id, reporting)
    prepared = await prepare_ref(
        session,
        user_id,
        report_key=report_key,
        definition=definition,
        overrides=overrides,
    )
    plan = await plan_report(
        session,
        defn=prepared.definition,
        params=prepared.params,
        user_id=user_id,
        reporting=reporting,
        compare_previous=overrides.compare_previous,
    )
    use_cache = cache is not None and not overrides.no_cache
    if use_cache:
        assert cache is not None
        hit = await cache.get(plan.cache_key)
        if hit is not None:
            result = hit.model_copy(update={"report_key": prepared.report_key, "facts": plan.facts})
            return RunRefOutcome(
                result=result,
                deferred=None,
                extra_filter=prepared.params.extra_filter,
                ignored_common=prepared.ignored_common,
            )
    deferred: Literal["forced", "events_window", "estimate"] | None = None
    window = plan.compiled.window
    if force_job:
        deferred = "forced"
    elif (
        plan.compiled.source.key == "ticket_events"
        and window is not None
        and (window.end_date - window.start_date).days + 1 > EVENTS_INLINE_MAX_DAYS
    ):
        deferred = "events_window"
    elif (
        await estimate_rows(session, plan.compiled, reporting=reporting, budget=budget)
        > reporting.inline_row_threshold
    ):
        deferred = "estimate"
    if deferred is not None:
        return RunRefOutcome(
            result=None,
            deferred=deferred,
            extra_filter=prepared.params.extra_filter,
            ignored_common=prepared.ignored_common,
        )
    # the plan above is reused: no second scope/state/compile, no second lookup
    result = await execute_and_store(
        session,
        plan,
        report_key=prepared.report_key,
        reporting=reporting,
        cache=cache if use_cache else None,
        budget=budget,
    )
    return RunRefOutcome(
        result=result,
        deferred=None,
        extra_filter=prepared.params.extra_filter,
        ignored_common=prepared.ignored_common,
    )


async def check_job_limit(session: AsyncSession, user_id: int) -> None:
    """:class:`TooManyReportJobs` when the user already has the maximum of report jobs.

    Must start a fresh transaction (callers roll back first); the user row lock
    serialises concurrent requests of one user until their enqueue is committed.
    """
    await lock_user_job_quota(session, user_id)
    active = 0
    for kind in (REPORT_RUN_KIND, REPORT_EXPORT_KIND):
        # a cancelled report job stops within about a second: it no longer counts
        active += await count_active_jobs(
            session, kind=kind, created_by=user_id, include_cancelling=False
        )
    if active >= MAX_ACTIVE_REPORT_JOBS_PER_USER:
        raise TooManyReportJobs


def accepted(job_id: int, job_status: str) -> JobAccepted:
    return JobAccepted(job_id=job_id, status="running" if job_status == "running" else "queued")


async def enqueue_report_ref(
    session: AsyncSession,
    *,
    user_id: int,
    run: ReportRunIn,
    extra_filter: Filter | None,
) -> JobAccepted:
    """Queue a ``core.report_run`` job (private result); :class:`TooManyReportJobs` at the cap.

    Report access is checked first (403 ``HTTPException``, like the routes).
    """
    await require_report_access(
        session, user_id, await load_reporting_settings(session, get_settings())
    )
    # PostgreSQL: an estimate's SET LOCAL statement_timeout (possibly ~1 s near the deadline)
    # lives until the transaction ends; never let it hit the enqueue
    await session.rollback()
    await check_job_limit(session, user_id)
    job = await enqueue_job(
        session,
        kind=REPORT_RUN_KIND,
        payload=ReportRunPayload(user_id=user_id, run=run, extra_filter=extra_filter),
        created_by=user_id,
    )
    await session.commit()
    return accepted(job.id, job.status)
