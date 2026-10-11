"""``core.report``: any built-in or saved report as a tile, run in the viewer's scope (E6).

One runner, :func:`run_report_widget`, serves every report-based widget. Rules:

- The report runs **as the viewer** (``run_report_ref`` evaluates the viewer's queue scope and
  visibility); a shared report never shows the owner's numbers.
- A report too large to run inline is answered ``deferred`` and **never** enqueued here: the
  enqueue commits, and providers never commit. The browser starts the job through the
  dashboard's report-job endpoint.
- Every reporting error becomes a tile, never an ``HTTPException`` (the runner would turn it
  into the generic error text).
- Query budget: the provider is ``self_limited``. The runner sets no ``statement_timeout``
  around it; instead the :class:`QueryBudget` below ends at ``ProviderContext.deadline`` minus
  :data:`DEADLINE_MARGIN_SECONDS`, and the reporting engine bounds each statement itself with
  ``min(query_timeout_seconds, time left)`` (``QueryBudget.statement_seconds``), so the smaller
  limit wins and the client-side ``wait_for`` of the runner stays a backstop. (The engine
  resets MariaDB's ``max_statement_time`` to 0 after every statement; a runner limit around it
  would be lifted after the first one, which is why the runner steps aside.)
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from tiqora.config import get_settings
from tiqora.dashboard.filters import TimeRangePreset, report_time_range
from tiqora.dashboard.providers import (
    DashboardProvider,
    ProviderContext,
    ProviderError,
    ProviderForbidden,
    filter_queue_ids,
)
from tiqora.dashboard.settings_models import ReportWidgetSettings
from tiqora.filters import FilterError
from tiqora.reporting.access import has_report_access
from tiqora.reporting.cache import ReportCache, read_cache_generation
from tiqora.reporting.definition import DefinitionError, ReportDefinition, TimeRange
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.run_service import (
    RequestError,
    RunOverrides,
    RunRefOutcome,
    parse_dimension_choices,
    run_report_ref,
)
from tiqora.reporting.settings import load_reporting_settings

MSG_NO_ACCESS: Final = "Für Berichte fehlt Ihnen die Berechtigung."
MSG_NOT_FOUND: Final = "Diesen Bericht gibt es nicht oder er ist für Sie nicht freigegeben."
MSG_CANNOT_RUN: Final = "Der Bericht kann nicht ausgeführt werden: "

#: seconds of the provider budget kept for serialising the answer
DEADLINE_MARGIN_SECONDS: Final = 1.5
#: pivot and heatmap results need every cell: rows are capped here instead of at ``max_rows``
WIDE_VISUALIZATIONS: Final = frozenset({"pivot", "heatmap"})
MAX_WIDE_ROWS: Final = 200
#: sources whose rows are tickets (a chart point can open a ticket list)
TICKET_SOURCES: Final = frozenset({"tickets", "open_tickets"})
#: dimension keys of a ticket source that map to a ticket-filter field (Task 9 refines this)
TICKET_DRILL_DIMENSIONS: Final = frozenset(
    {"queue", "state", "state_type", "priority", "type", "service", "sla", "channel", "created"}
)


def drillable_dimensions(definition: ReportDefinition | None) -> list[str]:
    """Dimension keys of the report whose chart points can open a ticket list.

    Pure. Only reports on ticket sources qualify; every other source answers ``[]``.
    """
    if definition is None or definition.source not in TICKET_SOURCES:
        return []
    return [d.key for d in definition.dimensions if d.key in TICKET_DRILL_DIMENSIONS]


def _report_meta(report_key: str | None, saved_name: str | None) -> dict[str, Any]:
    if report_key is None:
        return {"key": None, "kind": "inline", "name": None}
    if report_key.startswith("builtin."):
        name = report_key.removeprefix("builtin.")
        return {"key": report_key, "kind": "builtin", "title_key": f"reports.builtin.{name}.title"}
    return {"key": report_key, "kind": "saved", "name": saved_name}


def _result_data(
    outcome: RunRefOutcome, definition: ReportDefinition, max_rows: int
) -> dict[str, Any]:
    assert outcome.result is not None
    result = outcome.result.model_dump(mode="json")
    cap = MAX_WIDE_ROWS if definition.visualization.type in WIDE_VISUALIZATIONS else max_rows
    rows = result["rows"]
    if len(rows) > cap:
        result["rows"] = rows[:cap]
        result["truncated"] = True
    return result


async def run_report_widget(
    ctx: ProviderContext,
    *,
    report_key: str | None,
    definition: ReportDefinition | None,
    time_range: TimeRange | None,
    parameters: Mapping[str, Any],
    dimension_choices: Mapping[str, str],
    max_rows: int,
    display: str,
) -> dict[str, Any]:
    """Run one report for the viewer and shape the tile data (E6)."""
    backend = ctx.report_cache_backend
    # read before the first report query: a purge after it lets nothing computed here be found
    generation = await read_cache_generation(backend)
    reporting = await load_reporting_settings(ctx.session, ctx.app_settings or get_settings())
    if not await has_report_access(ctx.session, ctx.user_id, reporting):
        raise ProviderForbidden(MSG_NO_ACCESS)
    cache = ReportCache(backend, reporting.cache_seconds, generation=generation)
    clock = asyncio.get_running_loop().time
    deadline = ctx.deadline if ctx.deadline is not None else clock() + 8.0
    budget = QueryBudget(
        reporting.query_timeout_seconds, deadline=deadline - DEADLINE_MARGIN_SECONDS, clock=clock
    )
    queue_ids = filter_queue_ids(ctx)
    try:
        overrides = RunOverrides(
            time_range=time_range,
            parameters=dict(parameters),
            dimension_choices=parse_dimension_choices(dimension_choices),
            common={"queue": queue_ids} if queue_ids else {},
        )
        outcome = await run_report_ref(
            ctx.session,
            user_id=ctx.user_id,
            report_key=report_key,
            definition=definition,
            overrides=overrides,
            reporting=reporting,
            cache=cache,
            budget=budget,
            force_job=False,
        )
    except RequestError as exc:
        if exc.status_code == 404:
            raise ProviderForbidden(MSG_NOT_FOUND) from None
        if exc.status_code == 410:
            raise ProviderError(exc.message) from None
        raise ProviderError(MSG_CANNOT_RUN + exc.message) from None
    except (DefinitionError, FilterError) as exc:
        raise ProviderError(MSG_CANNOT_RUN + exc.message) from None
    effective = outcome.definition or definition
    assert effective is not None
    data: dict[str, Any] = {
        "report": _report_meta(report_key, outcome.saved_name),
        "display": display,
    }
    if outcome.result is None:
        data["deferred"] = True
        data["reason"] = outcome.deferred
        if ctx.kiosk:
            data["kiosk"] = True  # a wallboard never starts a job
        return data
    data.update(
        deferred=False,
        definition=effective.model_dump(mode="json", exclude_none=True),
        result=_result_data(outcome, effective, max_rows),
        ignored_filters=list(outcome.ignored_common),
        drillable_dimensions=drillable_dimensions(effective),
    )
    return data


def effective_time_range(ctx: ProviderContext, own: TimeRangePreset | None) -> TimeRange | None:
    """The dashboard's time range wins; a pinned widget has none, so its own applies."""
    preset: TimeRangePreset | None = ctx.filters.get("time_range") if ctx.filters else None
    chosen = preset or own
    return report_time_range(chosen) if chosen else None


async def run_core_report(ctx: ProviderContext, settings: ReportWidgetSettings) -> dict[str, Any]:
    return await run_report_widget(
        ctx,
        report_key=settings.report_key,
        definition=None,
        time_range=effective_time_range(ctx, settings.time_range),
        parameters=settings.parameters,
        dimension_choices=settings.dimension_choices,
        max_rows=settings.max_rows,
        display=settings.display,
    )


def report_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.report",
        version=1,
        settings_model=ReportWidgetSettings,
        run=run_core_report,
        timeout_seconds=8.0,
        self_limited=True,
    )
