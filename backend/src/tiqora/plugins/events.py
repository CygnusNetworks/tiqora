"""Ticket event subscriber extension point (the follower itself follows in Task 7)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

import structlog

from tiqora.domain.history_render import split_history_name
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.context import PluginContext

logger = structlog.get_logger(__name__)

#: Znuny ``ticket_history_type`` name -> Znuny event name (Kernel/System/Ticket.pm). Every name is a
#: seeded type in backend/src/tiqora/bootstrap/schema/initial_insert.mysql.sql (Znuny 6.0-7.3).
HISTORY_EVENT_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "NewTicket": "TicketCreate",
        "StateUpdate": "TicketStateUpdate",
        "Move": "TicketQueueUpdate",
        "OwnerUpdate": "TicketOwnerUpdate",
        "ResponsibleUpdate": "TicketResponsibleUpdate",
        "PriorityUpdate": "TicketPriorityUpdate",
        "TypeUpdate": "TicketTypeUpdate",
        "ServiceUpdate": "TicketServiceUpdate",
        "SLAUpdate": "TicketSLAUpdate",
        "TitleUpdate": "TicketTitleUpdate",
        "CustomerUpdate": "TicketCustomerUpdate",
        "SetPendingTime": "TicketPendingTimeUpdate",
        "Lock": "TicketLockUpdate",
        "Unlock": "TicketLockUpdate",
        "ArchiveFlagUpdate": "TicketArchiveFlagUpdate",
        "TimeAccounting": "TicketAccountTime",
        "Merged": "TicketMerge",
        "Subscribe": "TicketSubscribe",
        "Unsubscribe": "TicketUnsubscribe",
    }
)

#: History types written with a new article (``ArticleCreate`` when an article id is set).
ARTICLE_HISTORY_TYPES: Final = frozenset(
    {
        "FollowUp",
        "SendAutoReject",
        "SendAutoReply",
        "SendAutoFollowUp",
        "Forward",
        "Bounce",
        "SendAnswer",
        "SendCustomerNotification",
        "EmailAgent",
        "EmailCustomer",
        "PhoneCallAgent",
        "PhoneCallCustomer",
        "AddNote",
        "WebRequestCustomer",
        "EmailResend",
    }
)

#: Rows Znuny writes next to NewTicket in the same second; its TicketCreate fires only TicketCreate.
_CREATE_TIME_DUPLICATES: Final = frozenset({"TypeUpdate", "ServiceUpdate", "SLAUpdate"})


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
class HistoryRow:
    id: int
    history_type: str
    ticket_id: int
    article_id: int | None
    name: str
    create_by: int
    create_time: datetime


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


def _event_name(row: HistoryRow, article_id: int | None) -> str:
    htype = row.history_type
    if htype == "TicketDynamicFieldUpdate":
        values = split_history_name(row.name)
        if len(values) >= 2 and values[0] == "FieldName" and values[1].strip():
            return f"TicketDynamicFieldUpdate_{values[1].strip()}"
        return "HistoryAdd"
    mapped = HISTORY_EVENT_MAP.get(htype)
    if mapped is not None:
        return mapped
    if htype in ARTICLE_HISTORY_TYPES and article_id:
        return "ArticleCreate"
    return "HistoryAdd"


def events_from_history(rows: Sequence[HistoryRow]) -> list[TicketEvent]:
    """Map ``ticket_history`` rows (sorted by id) to Znuny event names, keeping their order.

    Type/Service/SLA rows with the same ticket and create time as a ``NewTicket`` row *of the same
    batch* are dropped. A NewTicket and its duplicates split across two batches are not
    de-duplicated (rare boundary case, documented).
    """
    created = {(r.ticket_id, r.create_time) for r in rows if r.history_type == "NewTicket"}
    events: list[TicketEvent] = []
    for row in rows:
        if (
            row.history_type in _CREATE_TIME_DUPLICATES
            and (row.ticket_id, row.create_time) in created
        ):
            continue
        article_id = row.article_id or None
        events.append(
            TicketEvent(
                id=row.id,
                event=_event_name(row, article_id),
                history_type=row.history_type,
                ticket_id=row.ticket_id,
                article_id=article_id,
                name=row.name,
                create_by=row.create_by,
                create_time=row.create_time,
            )
        )
    return events


class GapTracker:
    """Highest ``ticket_history.id`` that is safe to deliver up to.

    Auto-increment ids can be missing temporarily (a transaction that has taken an id but not yet
    committed) or forever (rollbacks). A missing id holds delivery back for ``hold_seconds``;
    then it is skipped and logged once. ``step``/``offset`` are the connection's
    auto_increment_increment/offset: ids that are not on this writer's lattice
    (``id % step != offset % step``) belong to another Galera writer and count as present,
    never as gaps. (Galera multi-writer can therefore delay events by up to the hold time.)
    """

    def __init__(self, *, hold_seconds: float, step: int = 1, offset: int | None = None) -> None:
        self._hold = hold_seconds
        self._step = max(step, 1)
        self._offset = offset
        self._seen: dict[int, float] = {}

    def pending_gaps(self) -> dict[int, float]:
        """Read-only copy: expected-but-missing id -> monotonic time it was first missed."""
        return dict(self._seen)

    def _on_lattice(self, id_: int) -> bool:
        return self._offset is None or id_ % self._step == self._offset % self._step

    def _first_expected(self, after: int) -> int:
        expected = after + self._step
        if self._offset is None:
            return expected
        expected = after + 1
        while not self._on_lattice(expected):
            expected += 1
        return expected

    def horizon(self, after: int, ids: Sequence[int], now: float) -> int:
        horizon = after
        expected = self._first_expected(after)
        for id_ in sorted({i for i in ids if i > after}):
            if not self._on_lattice(id_):
                horizon = id_
                continue
            if id_ > expected:
                first = self._seen.setdefault(expected, now)
                if now - first < self._hold:
                    break
                logger.warning("plugin_event_gap_skipped", from_id=expected, to_id=id_ - self._step)
                del self._seen[expected]
            horizon = id_
            expected = id_ + self._step
        for key in [k for k in self._seen if k <= horizon]:
            del self._seen[key]
        return horizon
