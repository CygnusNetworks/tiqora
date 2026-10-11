"""``core.ticket-list``: a page of tickets for a preset, in the viewer's scope (D6 rule 4)."""

from __future__ import annotations

from typing import Any

from tiqora.dashboard.providers import DashboardProvider, ProviderContext
from tiqora.dashboard.providers.presets import preset_kwargs
from tiqora.dashboard.providers.sources import compile_for_viewer, resolve_source
from tiqora.dashboard.settings_models import PresetSource, TicketListSettings
from tiqora.domain.ticket_service import TicketService


async def run_ticket_list(ctx: ProviderContext, settings: TicketListSettings) -> dict[str, Any]:
    source = settings.source
    if not isinstance(source, PresetSource):
        resolved = await resolve_source(ctx.session, ctx.user_id, source)
        stmt = await compile_for_viewer(ctx.session, resolved)
        found = await TicketService(ctx.session).page_from_stmt(
            ctx.user_id,
            stmt,
            offset=0,
            limit=settings.limit,
            sort=settings.sort,
            order=settings.order,
        )
        return {"items": [i.model_dump(mode="json") for i in found.items], "total": found.total}
    page = await TicketService(ctx.session).list_tickets(
        ctx.user_id,
        offset=0,
        limit=settings.limit,
        sort=settings.sort,
        order=settings.order,
        **preset_kwargs(source.preset, ctx.user_id),
    )
    return {"items": [i.model_dump(mode="json") for i in page.items], "total": page.total}


def ticket_list_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.ticket-list", version=1, settings_model=TicketListSettings, run=run_ticket_list
    )
