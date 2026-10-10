"""Ticket event subscriber extension point (the follower itself follows in Task 7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.context import PluginContext


@dataclass(frozen=True, slots=True)
class TicketEvent:
    id: int  # ticket_history.id
    event: str
    history_type: str
    ticket_id: int
    article_id: int | None  # None when 0/NULL
    name: str  # raw history name
    create_by: int
    create_time: datetime  # as stored (naive)


@dataclass(frozen=True, slots=True)
class EventContext:
    plugin: PluginContext
    attempt: int  # consecutive_failures + 1


EventHandler = Callable[[EventContext, TicketEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EventSubscriber:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    events: frozenset[str]  # Znuny event names, "*" = all, "TicketDynamicFieldUpdate_*" = prefix
    handler: EventHandler
    timeout_seconds: int
    context: PluginContext | None = None

    def matches(self, event: str) -> bool:
        for pattern in self.events:
            if pattern == "*" or pattern == event:
                return True
            if pattern.endswith("*") and event.startswith(pattern[:-1]):
                return True
        return False


EVENT_SUBSCRIBERS: Final[ExtensionPoint[EventSubscriber]] = ExtensionPoint(
    key="core.events.subscribers",
    item_key=lambda s: s.key,
    description="Asynchronous subscribers for ticket events (at least once).",
    sdk_stable=True,
)
