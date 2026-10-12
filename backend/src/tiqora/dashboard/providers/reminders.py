"""``core.reminders``: the viewer's pending-reminder tickets that fall due within a window.

The due window is part of the query (:func:`reminders_stmt`), so the widget's page, its total
and the drill-down list (``dashboard/drill.py``) are one statement and agree by construction.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard.providers import DashboardProvider, ProviderContext, filter_queue_ids
from tiqora.dashboard.settings_models import RemindersSettings
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.ticket_service import VIEW_STATE_TYPES, TicketService
from tiqora.filters import AllOf, Condition, Filter
from tiqora.filters.tickets import compile_ticket_filter, ticket_query_scope

#: ``pending_until`` is compared as a datetime; the lower bound is the first second of 1970
_EPOCH_START = "1970-01-01T00:00:01"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).replace(tzinfo=None).isoformat()


def reminders_filter(user_id: int, now_epoch: int, window_hours: int) -> Filter:
    """Pending tickets of ``user_id`` with a reminder time up to ``window_hours`` from now.

    Reminders already overdue are included (``0 < until_time <= horizon``).
    """
    horizon = now_epoch + window_hours * 3600
    return Filter(
        where=AllOf(
            all=[
                Condition(field="state_type", op="in", value=sorted(VIEW_STATE_TYPES["pending"])),
                Condition(field="owner_id", op="eq", value=user_id),
                Condition(
                    field="pending_until",
                    op="between",
                    value={"from": _EPOCH_START, "to": _iso(horizon + 1)},
                ),
            ]
        )
    )


async def reminders_stmt(
    session: AsyncSession,
    user_id: int,
    *,
    now_epoch: int,
    window_hours: int,
    queue_ids: list[int] | None,
) -> Select[tuple[Ticket]] | None:
    """The reminders statement in the viewer's scope, narrowed to ``queue_ids`` when given."""
    scope = await ticket_query_scope(session, user_id, include_archived=False)
    if queue_ids:
        scope = replace(scope, queue_ids=scope.queue_ids & frozenset(queue_ids))
    return await compile_ticket_filter(
        session, reminders_filter(user_id, now_epoch, window_hours), scope
    )


async def run_reminders(ctx: ProviderContext, settings: RemindersSettings) -> dict[str, Any]:
    now_epoch = int(ctx.now.replace(tzinfo=UTC).timestamp())  # ctx.now is naive UTC
    stmt = await reminders_stmt(
        ctx.session,
        ctx.user_id,
        now_epoch=now_epoch,
        window_hours=settings.window_hours,
        queue_ids=filter_queue_ids(ctx),
    )
    page = await TicketService(ctx.session).page_from_stmt(
        ctx.user_id, stmt, offset=0, limit=settings.limit, sort="until", order="asc"
    )
    items = [
        {
            "ticket": t.model_dump(mode="json"),
            "until": t.until_time,
            "overdue": t.until_time <= now_epoch,
        }
        for t in page.items
    ]
    return {"items": items, "total": page.total}


def reminders_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.reminders", version=1, settings_model=RemindersSettings, run=run_reminders
    )
