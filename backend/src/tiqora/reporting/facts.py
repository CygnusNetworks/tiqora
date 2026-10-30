"""Compute one ``tiqora_report_ticket_fact`` row from already loaded ticket data (pure, no I/O).

Rules are fixed in the reporting-foundation plan (R5). SLA figures are measured in
working minutes of the ticket's calendar (wall-clock minutes when the calendar has no
working hours), like Znuny's stats; targets of 0 mean "no SLA" and leave every
``*_target_minutes`` / ``*_due_at`` / ``*_breached`` column empty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from tiqora.reporting.history_events import ArticleRow, EventRow, HistoryRow, TicketRow
from tiqora.reporting.working_time import (
    EscalationTargets,
    WorkingCalendar,
    due_at,
    working_minutes,
)

#: 2: rows a ticket merge moved in no longer drive the event diff
#: 3: first_level_solved, itsm_solution_working_minutes
FACT_VERSION: Final = 3
#: Znuny's solution time of a ticket created closed (and never reopened)
ITSM_SOLUTION_CREATED_CLOSED_MINUTES: Final = 3
CHANNEL_KEYS: Final[dict[str, str]] = {
    "Email": "email",
    "Phone": "phone",
    "Internal": "internal",
    "Telegram": "telegram",
    "Chat": "webchat",
    "WebChat": "webchat",
    "SMS": "sms",
    "WhatsApp": "whatsapp",
}
_DEFAULT_CHANNEL: Final = "email"
_MERGED_STATE_TYPE: Final = "merged"


@dataclass(frozen=True, slots=True)
class AiFlags:
    triaged: bool | None = None
    triage_queue_applied: bool | None = None
    auto_replied: bool | None = None
    draft_used: bool | None = None
    handed_off: bool | None = None  # currently handed off (cleared again on resume)


@dataclass(frozen=True, slots=True)
class FactInputs:
    ticket: TicketRow
    history: Sequence[HistoryRow]
    articles: Sequence[ArticleRow]
    events: Sequence[EventRow]
    state_types: Mapping[int, str]
    accounted_time: Decimal
    targets: EscalationTargets
    calendar: WorkingCalendar
    storage_tz: str
    ai: AiFlags
    process_entity_id: str | None
    now: datetime  # naive UTC (built_at)


@dataclass(frozen=True, slots=True)
class FactRow:
    """One attribute per ``tiqora_report_ticket_fact`` column, same names."""

    ticket_id: int
    tn: str
    created_at: datetime
    created_by: int
    queue_id: int
    queue_id_at_create: int
    state_id: int
    state_type: str
    priority_id: int
    type_id: int | None
    service_id: int | None
    sla_id: int | None
    owner_id: int
    responsible_id: int | None
    customer_id: str | None
    customer_user: str | None
    channel: str
    archived: bool
    merged: bool
    first_response_at: datetime | None
    first_response_minutes: int | None
    first_response_working_minutes: int | None
    first_response_target_minutes: int | None
    first_response_due_at: datetime | None
    first_response_breached: bool | None
    first_closed_at: datetime | None
    closed_at: datetime | None
    solution_minutes: int | None
    solution_working_minutes: int | None
    solution_target_minutes: int | None
    solution_due_at: datetime | None
    solution_breached: bool | None
    update_target_minutes: int | None
    update_breach_count: int
    calendar_name: str | None
    close_count: int
    reopen_count: int
    queue_move_count: int
    queue_pingpong: bool
    owner_change_count: int
    article_in_count: int
    article_out_count: int
    first_level_solved: bool
    itsm_solution_working_minutes: int | None
    note_count: int
    agent_touch_count: int
    accounted_time: Decimal
    ai_triaged: bool | None
    ai_triage_queue_applied: bool | None
    ai_auto_replied: bool | None
    ai_draft_used: bool | None
    ai_handed_off: bool | None
    process_entity_id: str | None
    last_event_at: datetime
    last_history_id: int
    fact_version: int
    built_at: datetime

    def as_insert_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def channel_key(name: str) -> str:
    return CHANNEL_KEYS.get(name, name.lower()[:30])


def _wall_minutes(start: datetime, stop: datetime) -> int:
    return int((max(stop, start) - start).total_seconds() // 60)


def _target(minutes: int) -> int | None:
    return minutes if minutes > 0 else None


def _has_queue_pingpong(queues: Sequence[int]) -> bool:
    seen: set[int] = set()
    for idx, queue in enumerate(queues):
        if idx > 0 and queue in seen:
            return True
        seen.add(queue)
    return False


def first_level_solved(articles: Sequence[ArticleRow]) -> bool:
    """Znuny's first-level rule on the customer-visible articles (in order).

    Solved at first contact: one article from the customer or an agent, or a customer
    article answered by exactly one agent article. Anything else (system first, an
    agent article followed by more, an auto reply after the customer) is not.
    """
    senders = [a.sender_type for a in articles if a.visible]
    if len(senders) == 1:
        return senders[0] in ("customer", "agent")
    return senders == ["customer", "agent"]


def open_span_minutes(
    created: datetime,
    events: Sequence[EventRow],
    measure: Callable[[datetime, datetime], int],
) -> int | None:
    """Time the ticket was open, summed over its open spans (Znuny's solution time).

    A span starts at creation and at every reopen and ends at the next close; a
    trailing span (reopened, not closed again) is not counted. ``None`` when the
    ticket was never closed. A ticket created closed and never reopened counts
    ``ITSM_SOLUTION_CREATED_CLOSED_MINUTES``.
    """
    changes = sorted(
        (e for e in events if e.kind in ("close", "reopen")),
        key=lambda e: (e.occurred_at, e.history_id or 0),
    )
    if not any(e.kind == "close" for e in changes):
        return None
    created_closed = changes[0].kind == "close" and changes[0].occurred_at <= created
    if created_closed and not any(e.kind == "reopen" for e in changes):
        return ITSM_SOLUTION_CREATED_CLOSED_MINUTES
    total = 0
    opened_at: datetime | None = created
    for e in changes:
        if e.kind == "close" and opened_at is not None:
            total += measure(opened_at, max(e.occurred_at, opened_at))
            opened_at = None
        elif e.kind == "reopen" and opened_at is None:
            opened_at = e.occurred_at
    return total


def compute_fact(inp: FactInputs) -> FactRow:
    t = inp.ticket
    cal, tz = inp.calendar, inp.storage_tz
    created = t.create_time
    articles = sorted(inp.articles, key=lambda a: (a.create_time, a.id))

    def span(start: datetime, stop: datetime) -> tuple[int, int | None, int]:
        """(wall minutes, working minutes or None, minutes the SLA is measured in)."""
        wall = _wall_minutes(start, stop)
        working = working_minutes(start, max(stop, start), cal, tz)
        return wall, working, wall if working is None else working

    # events -> close / reopen / queue / owner counters
    closes = [e for e in inp.events if e.kind == "close"]
    reopen_count = sum(1 for e in inp.events if e.kind == "reopen")
    queue_events = [e for e in inp.events if e.kind == "queue"]
    create_event = next((e for e in inp.events if e.kind == "create"), None)
    queue_at_create = (
        create_event.to_id if create_event is not None and create_event.to_id is not None else None
    )
    if queue_at_create is None:
        queue_at_create = t.queue_id
    queue_path = [queue_at_create] + [e.to_id for e in queue_events if e.to_id is not None]
    first_closed_at = min((e.occurred_at for e in closes), default=None)
    closed_at = max((e.occurred_at for e in closes), default=None)

    # first response: earliest agent article visible for the customer
    responses = [a for a in articles if a.sender_type == "agent" and a.visible]
    first_response = responses[0].create_time if responses else None

    first_target = _target(inp.targets.first_response_minutes)
    update_target = _target(inp.targets.update_minutes)
    solution_target = _target(inp.targets.solution_minutes)

    fr_wall = fr_working = None
    fr_due = None
    fr_breached: bool | None = None
    if first_response is not None:
        fr_wall, fr_working, fr_eff = span(created, first_response)
    if first_target is not None:
        fr_due = due_at(created, first_target, cal, tz)
        if first_response is not None:
            fr_breached = fr_eff > first_target
        elif first_closed_at is not None:
            fr_breached = first_closed_at > fr_due

    sol_wall = sol_working = None
    sol_due = None
    sol_breached: bool | None = None
    if closed_at is not None:
        sol_wall, sol_working, sol_eff = span(created, closed_at)
    if solution_target is not None:
        sol_due = due_at(created, solution_target, cal, tz)
        if closed_at is not None:
            sol_breached = sol_eff > solution_target

    update_breaches = 0
    if update_target is not None:
        waiting_since: datetime | None = None
        for a in articles:
            if a.sender_type == "customer":
                if waiting_since is None:
                    waiting_since = a.create_time
            elif a.sender_type == "agent" and a.visible and waiting_since is not None:
                if span(waiting_since, a.create_time)[2] > update_target:
                    update_breaches += 1
                waiting_since = None

    agent_articles = [a for a in articles if a.sender_type == "agent"]
    state_type = inp.state_types.get(t.state_id, "unknown")
    last_event = max(
        [created, *(h.create_time for h in inp.history), *(a.create_time for a in articles)]
    )

    return FactRow(
        ticket_id=t.id,
        tn=t.tn,
        created_at=created,
        created_by=t.create_by,
        queue_id=t.queue_id,
        queue_id_at_create=queue_at_create,
        state_id=t.state_id,
        state_type=state_type,
        priority_id=t.priority_id,
        type_id=t.type_id,
        service_id=t.service_id,
        sla_id=t.sla_id,
        owner_id=t.user_id,
        responsible_id=t.responsible_user_id,
        customer_id=t.customer_id,
        customer_user=t.customer_user_id,
        channel=channel_key(articles[0].channel) if articles else _DEFAULT_CHANNEL,
        archived=bool(t.archive_flag),
        merged=state_type == _MERGED_STATE_TYPE,
        first_response_at=first_response,
        first_response_minutes=fr_wall,
        first_response_working_minutes=fr_working,
        first_response_target_minutes=first_target,
        first_response_due_at=fr_due,
        first_response_breached=fr_breached,
        first_closed_at=first_closed_at,
        closed_at=closed_at,
        solution_minutes=sol_wall,
        solution_working_minutes=sol_working,
        solution_target_minutes=solution_target,
        solution_due_at=sol_due,
        solution_breached=sol_breached,
        update_target_minutes=update_target,
        update_breach_count=update_breaches,
        calendar_name=cal.name,
        close_count=len(closes),
        reopen_count=reopen_count,
        queue_move_count=len(queue_events),
        queue_pingpong=_has_queue_pingpong(queue_path),
        owner_change_count=sum(1 for e in inp.events if e.kind == "owner"),
        article_in_count=sum(1 for a in articles if a.sender_type == "customer"),
        article_out_count=len(responses),
        first_level_solved=first_level_solved(articles),
        itsm_solution_working_minutes=open_span_minutes(
            created, inp.events, lambda a, b: span(a, b)[2]
        ),
        note_count=sum(1 for a in agent_articles if not a.visible),
        agent_touch_count=len({a.create_by for a in agent_articles}),
        accounted_time=inp.accounted_time,
        ai_triaged=inp.ai.triaged,
        ai_triage_queue_applied=inp.ai.triage_queue_applied,
        ai_auto_replied=inp.ai.auto_replied,
        ai_draft_used=inp.ai.draft_used,
        ai_handed_off=inp.ai.handed_off,
        process_entity_id=inp.process_entity_id,
        last_event_at=last_event,
        last_history_id=max((h.id for h in inp.history), default=0),
        fact_version=FACT_VERSION,
        built_at=inp.now,
    )
