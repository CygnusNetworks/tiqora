"""``core.escalations``: tickets near or past an SLA deadline, nearest deadline first."""

from __future__ import annotations

from typing import Any

from tiqora.dashboard.providers import DashboardProvider, ProviderContext, filter_queue_ids
from tiqora.dashboard.settings_models import EscalationsSettings
from tiqora.domain.schemas import TicketListItem
from tiqora.domain.ticket_service import TicketService

#: ``(kind, TicketListItem attribute)``; on equal epochs the first kind wins
_KINDS = (
    ("first_response", "escalation_response_time"),
    ("update", "escalation_update_time"),
    ("solution", "escalation_solution_time"),
    ("escalation", "escalation_time"),
)


def nearest_deadline(item: TicketListItem) -> tuple[int, str] | None:
    """The smallest ``escalation_*`` epoch above 0 and which deadline it is."""
    best: tuple[int, str] | None = None
    for kind, attr in _KINDS:
        epoch = int(getattr(item, attr))
        if epoch > 0 and (best is None or epoch < best[0]):
            best = (epoch, kind)
    return best


async def run_escalations(ctx: ProviderContext, settings: EscalationsSettings) -> dict[str, Any]:
    page = await TicketService(ctx.session).list_tickets(
        ctx.user_id,
        state_type="open",
        escalating_within=settings.within_hours * 3600,
        sort="deadline",
        order="asc",
        limit=settings.limit,
        queue_ids=filter_queue_ids(ctx),
    )
    items: list[dict[str, Any]] = []
    skipped = 0
    for ticket in page.items:
        deadline = nearest_deadline(ticket)
        if deadline is None:
            skipped += 1
            continue
        epoch, kind = deadline
        items.append({"ticket": ticket.model_dump(mode="json"), "epoch": epoch, "kind": kind})
    oldest = min((i["epoch"] for i in items), default=None)
    return {"items": items, "total": page.total - skipped, "oldest_epoch": oldest}


def escalations_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.escalations",
        version=1,
        settings_model=EscalationsSettings,
        run=run_escalations,
    )
