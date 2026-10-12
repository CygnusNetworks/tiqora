"""``core.chat-queue``: waiting web chats in the viewer's ``rw`` queues.

Reuses the agent inbox (:func:`tiqora.webchat.agent.list_inbox`), so the scoping is exactly the
inbox's. ``waiting`` counts up to ``INBOX_WAITING_LIMIT`` (200). The dashboard filter does not
apply, and the visitor's name never leaves this provider.

``bot`` counts the chats the AI assistant is answering (status ``bot``, same ``rw`` filter, up to
``INBOX_BOT_LIMIT``, 200). A bot chat is not waiting for a human, so it is never in ``waiting``
or ``items``; the widget shows the count on its own (ruling M5: count only, never items).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tiqora.dashboard.providers import DashboardProvider, ProviderContext
from tiqora.dashboard.settings_models import ChatQueueSettings
from tiqora.db.tiqora.models import TiqoraChatConfig
from tiqora.webchat.agent import list_inbox
from tiqora.webchat.store import config_values


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat() + "Z"


async def chat_enabled(ctx: ProviderContext) -> bool:
    """True when at least one valid chat configuration has a topic."""
    rows = (
        (
            await ctx.session.execute(
                select(TiqoraChatConfig).where(TiqoraChatConfig.valid.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return any(config_values(row).topics for row in rows)


async def run_chat_queue(ctx: ProviderContext, settings: ChatQueueSettings) -> dict[str, Any]:
    if not await chat_enabled(ctx):
        return {
            "enabled": False,
            "waiting": 0,
            "oldest_waiting_since": None,
            "mine": 0,
            "bot": 0,
            "items": [],
        }
    inbox = await list_inbox(ctx.session, user_id=ctx.user_id)
    waiting = inbox.waiting  # oldest first
    return {
        "enabled": True,
        "waiting": len(waiting),
        "oldest_waiting_since": _iso(waiting[0].waiting_since) if waiting else None,
        "mine": len(inbox.mine),
        "bot": len(inbox.bot),
        "items": [
            {
                "session_id": w.session_id,
                "ticket_id": w.ticket_id,
                "tn": w.tn,
                "title": w.title,
                "topic_label": w.topic_label,
                "waiting_since": _iso(w.waiting_since),
            }
            for w in waiting[: settings.limit]
        ],
    }


def chat_queue_provider() -> DashboardProvider:
    return DashboardProvider(
        key="core.chat-queue",
        version=1,
        settings_model=ChatQueueSettings,
        run=run_chat_queue,
    )
