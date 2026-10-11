"""``core.queue-overview``: open / new / escalated counts per readable queue, busiest first."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tiqora.dashboard.providers import DashboardProvider, ProviderContext, filter_queue_ids
from tiqora.dashboard.settings_models import QueueOverviewSettings
from tiqora.db.legacy.queue import Queue
from tiqora.domain.queue_service import QueueService
from tiqora.domain.ticket_service import TicketService


async def run_queue_overview(
    ctx: ProviderContext, settings: QueueOverviewSettings
) -> dict[str, Any]:
    allowed = await QueueService(ctx.session).allowed_queue_ids(ctx.user_id, "ro")
    narrow = filter_queue_ids(ctx)
    if narrow is not None:
        allowed &= set(narrow)
    if not allowed:
        return {"queues": [], "total": 0}
    tickets = TicketService(ctx.session)
    # one grouped COUNT per column over the viewer's scope (the filter only narrows it further)
    scope = sorted(allowed)
    open_by = await tickets.count_by_queue(ctx.user_id, state_type="open", queue_ids=scope)
    new_by = await tickets.count_by_queue(ctx.user_id, state_type="new", queue_ids=scope)
    esc_by = await tickets.count_by_queue(
        ctx.user_id, state_type="open", escalating_within=0, queue_ids=scope
    )
    names = {
        int(qid): str(name)
        for qid, name in (
            await ctx.session.execute(select(Queue.id, Queue.name).where(Queue.id.in_(scope)))
        ).all()
    }
    rows: list[dict[str, Any]] = [
        {
            "id": qid,
            "name": names.get(qid, str(qid)),
            "open": open_by.get(qid, 0),
            "new": new_by.get(qid, 0),
            "escalated": esc_by.get(qid, 0),
        }
        for qid in scope
    ]
    if settings.only_with_work:
        rows = [r for r in rows if r["open"] or r["new"] or r["escalated"]]
    rows.sort(key=lambda r: (-int(r["open"]), -int(r["new"]), str(r["name"]).lower()))
    # ``total``: queues that qualify, before ``limit`` cuts the list
    return {"queues": rows[: settings.limit], "total": len(rows)}


def queue_overview_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.queue-overview",
        version=1,
        settings_model=QueueOverviewSettings,
        run=run_queue_overview,
    )
