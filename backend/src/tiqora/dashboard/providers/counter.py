"""``core.counter``: one number for the viewer, with delta and sparkline (D6 rule 4, D7).

The value is the ticket list's total for the preset (``TicketService.count_filtered`` with the
preset's filter arguments), so it equals ``count_dashboard_summary`` and the list it stands for.
Previews and wallboards read the history but never write it (R-DASH-SAMPLES-PREVIEW).
"""

from __future__ import annotations

from typing import Any

from tiqora.dashboard.filters import DashboardFilters, filters_key
from tiqora.dashboard.providers import (
    DashboardProvider,
    ProviderContext,
    ProviderForbidden,
    filter_queue_ids,
)
from tiqora.dashboard.providers.presets import preset_kwargs
from tiqora.dashboard.providers.sources import MSG_FILTER_INVALID, resolve_source
from tiqora.dashboard.samples import record_and_history
from tiqora.dashboard.settings_models import CounterSettings, PresetSource
from tiqora.domain.ticket_service import TicketService
from tiqora.filters import FilterError
from tiqora.filters.tickets import count_tickets


async def run_counter(ctx: ProviderContext, settings: CounterSettings) -> dict[str, Any]:
    source = settings.source
    queue_ids = filter_queue_ids(ctx)
    # a filtered counter has its own history (its numbers are not comparable to the unfiltered)
    suffix = filters_key(DashboardFilters(queue_ids=queue_ids or []))
    if isinstance(source, PresetSource):
        counter_key = f"preset:{source.preset}"
        value = await TicketService(ctx.session).count_filtered(
            ctx.user_id, queue_ids=queue_ids, **preset_kwargs(source.preset, ctx.user_id)
        )
    else:
        resolved = await resolve_source(ctx.session, ctx.user_id, source, queue_ids)
        counter_key = resolved.counter_key
        try:
            value = await count_tickets(ctx.session, resolved.criteria, resolved.scope)
        except FilterError:  # a field vanished after saving
            raise ProviderForbidden(MSG_FILTER_INVALID) from None
    if suffix:
        counter_key = f"{counter_key}|{suffix}"
    delta, spark = await record_and_history(
        ctx.session,
        user_id=ctx.user_id,
        counter_key=counter_key,
        value=value,
        today=ctx.now.date(),
        persist=not (ctx.preview or ctx.kiosk),
    )
    return {"value": value, "delta": delta, "spark": spark}


def counter_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.counter", version=1, settings_model=CounterSettings, run=run_counter
    )
