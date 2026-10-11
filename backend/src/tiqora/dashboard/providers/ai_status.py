"""``core.ai-status``: AI handovers, today's AI runs and budgets, scoped to the viewer.

Queue budgets and run counts cover the viewer's ``ro`` queues (narrowed by the dashboard's
queue filter); provider cost budgets are shown to administrators only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from tiqora.ai.limits import KIND_PROVIDER_COST, KIND_QUEUE_TOKENS_DAY, collect_limits
from tiqora.ai.models import TiqoraAiUsage
from tiqora.dashboard.providers import DashboardProvider, ProviderContext, filter_queue_ids
from tiqora.dashboard.providers.presets import preset_kwargs
from tiqora.dashboard.settings_models import AiStatusSettings
from tiqora.domain.queue_service import QueueService
from tiqora.domain.ticket_service import TicketService
from tiqora.permissions.engine import PermissionEngine


async def run_ai_status(ctx: ProviderContext, settings: AiStatusSettings) -> dict[str, Any]:
    ro = await QueueService(ctx.session).allowed_queue_ids(ctx.user_id, "ro")
    narrow = filter_queue_ids(ctx)
    if narrow is not None:
        ro &= set(narrow)
    escalated = await TicketService(ctx.session).count_filtered(
        ctx.user_id, queue_ids=narrow, **preset_kwargs("ai_escalated", ctx.user_id)
    )
    runs_today: dict[str, int] = {}
    if ro:
        day_start = ctx.now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await ctx.session.execute(
            select(TiqoraAiUsage.feature, func.count())
            .where(TiqoraAiUsage.queue_id.in_(sorted(ro)), TiqoraAiUsage.ts >= day_start)
            .group_by(TiqoraAiUsage.feature)
        )
        runs_today = {str(f): int(n) for f, n in rows.all()}
    is_admin = await PermissionEngine(ctx.session).is_admin(ctx.user_id)
    budgets: list[dict[str, Any]] = []
    for status in await collect_limits(ctx.session, now=ctx.now):
        if status.kind == KIND_QUEUE_TOKENS_DAY:
            if status.subject_id not in ro:
                continue
        elif status.kind == KIND_PROVIDER_COST:
            if not is_admin:
                continue
        else:
            continue
        budgets.append(
            {
                "kind": status.kind,
                "subject_name": status.subject_name,
                "window": status.window,
                "used": status.used,
                "limit": status.limit,
                "exhausted": status.exhausted,
                "resets_at": status.resets_at.isoformat() + "Z",
                "currency": status.currency,
            }
        )
    return {"ai_escalated": escalated, "runs_today": runs_today, "budgets": budgets}


def ai_status_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.ai-status", version=1, settings_model=AiStatusSettings, run=run_ai_status
    )
