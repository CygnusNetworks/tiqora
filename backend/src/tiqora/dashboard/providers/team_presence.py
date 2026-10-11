"""``core.team-presence``: who is online right now (available or in a web chat), viewer first."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tiqora.api.v1.agents import list_online_agents_for
from tiqora.dashboard.providers import DashboardProvider, ProviderContext
from tiqora.dashboard.settings_models import TeamPresenceSettings
from tiqora.db.tiqora.models import TiqoraChatSession


async def run_team_presence(ctx: ProviderContext, settings: TeamPresenceSettings) -> dict[str, Any]:
    if ctx.redis is None:
        return {"online": [], "total_online": 0}
    agents = await list_online_agents_for(ctx.redis, ctx.session)
    agents.sort(key=lambda a: (a.id != ctx.user_id, a.full_name.lower()))
    shown = agents[: settings.limit]
    in_chat: set[int] = set()
    if shown:  # integer IN-list over the shown agents only (never joined with Znuny tables)
        rows = await ctx.session.execute(
            select(TiqoraChatSession.assigned_user_id)
            .where(
                TiqoraChatSession.status == "active",
                TiqoraChatSession.assigned_user_id.in_([a.id for a in shown]),
            )
            .distinct()
        )
        in_chat = {int(uid) for (uid,) in rows.all() if uid is not None}
    return {
        "online": [
            {**a.model_dump(mode="json"), "status": "in_chat" if a.id in in_chat else "available"}
            for a in shown
        ],
        "total_online": len(agents),
    }


def team_presence_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.team-presence",
        version=1,
        settings_model=TeamPresenceSettings,
        run=run_team_presence,
    )
