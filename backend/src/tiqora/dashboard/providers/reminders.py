"""``core.reminders``: the viewer's pending-reminder tickets that fall due within a window."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from tiqora.dashboard.providers import DashboardProvider, ProviderContext, filter_queue_ids
from tiqora.dashboard.settings_models import RemindersSettings
from tiqora.domain.ticket_service import TicketService

#: how many pending tickets are looked at: the soonest-due ones (sorted by ``until_time``),
#: so a due reminder is never hidden behind later ones; 200 is the service's page cap
_SCAN_LIMIT = 200


async def run_reminders(ctx: ProviderContext, settings: RemindersSettings) -> dict[str, Any]:
    now_epoch = int(ctx.now.replace(tzinfo=UTC).timestamp())  # ctx.now is naive UTC
    horizon = now_epoch + settings.window_hours * 3600
    page = await TicketService(ctx.session).list_tickets(
        ctx.user_id,
        owner_id=ctx.user_id,
        state_type="pending",
        sort="until",
        order="asc",
        limit=_SCAN_LIMIT,
        queue_ids=filter_queue_ids(ctx),
    )
    due = sorted((t for t in page.items if 0 < t.until_time <= horizon), key=lambda t: t.until_time)
    items = [
        {
            "ticket": t.model_dump(mode="json"),
            "until": t.until_time,
            "overdue": t.until_time <= now_epoch,
        }
        for t in due[: settings.limit]
    ]
    return {"items": items, "total": len(due)}


def reminders_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.reminders", version=1, settings_model=RemindersSettings, run=run_reminders
    )
