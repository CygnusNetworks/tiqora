"""Derive reporting events from ``ticket_history`` and articles (pure, no I/O).

Rules are fixed in the reporting-foundation plan (R4). Queue, state, owner,
priority and type changes are found by diffing the snapshot columns every
history row carries (values AFTER the action), which is identical for Znuny
and Tiqora writers. Service, SLA, responsible user, merges and process
activities have no snapshot column and are parsed from the ``%%`` names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

from tiqora.domain.history_render import split_history_name

EVENT_KINDS: Final = (
    "create",
    "state",
    "close",
    "reopen",
    "queue",
    "owner",
    "responsible",
    "priority",
    "type",
    "service",
    "sla",
    "merge",
    "process_activity",
    "reply",
    "note",
    "customer_message",
)
CLOSED_STATE_TYPE: Final = "closed"
PROCESS_ACTIVITY_FIELD: Final = (
    "ProcessManagementActivityID"  # = process.ticket_state.ACTIVITY_ID_DF_NAME
)

_DURATION_KINDS: Final = frozenset(
    {
        "queue",
        "state",
        "owner",
        "priority",
        "type",
        "service",
        "sla",
        "responsible",
        "process_activity",
    }
)


@dataclass(frozen=True, slots=True)
class TicketRow:
    id: int
    tn: str
    create_time: datetime
    create_by: int
    queue_id: int
    state_id: int
    priority_id: int
    type_id: int | None
    service_id: int | None
    sla_id: int | None
    user_id: int
    responsible_user_id: int | None
    customer_id: str | None
    customer_user_id: str | None
    archive_flag: int


@dataclass(frozen=True, slots=True)
class HistoryRow:
    id: int
    history_type: str
    name: str
    article_id: int | None
    queue_id: int
    state_id: int
    priority_id: int
    owner_id: int
    type_id: int
    create_time: datetime
    create_by: int


@dataclass(frozen=True, slots=True)
class ArticleRow:
    id: int
    sender_type: str
    visible: bool
    channel: str
    create_time: datetime
    create_by: int


@dataclass(frozen=True, slots=True)
class EventRow:
    ticket_id: int
    kind: str
    occurred_at: datetime
    queue_id: int
    history_id: int | None = None
    article_id: int | None = None
    actor_user_id: int | None = None
    from_id: int | None = None
    to_id: int | None = None
    from_value: str | None = None
    to_value: str | None = None
    from_duration_seconds: int | None = None


def _int(value: str) -> int | None:
    v = value.strip()
    return int(v) if v.isdigit() else None


def _at(values: list[str], idx: int) -> str:
    return values[idx] if 0 <= idx < len(values) else ""


def derive_events(
    ticket: TicketRow,
    history: Sequence[HistoryRow],
    articles: Sequence[ArticleRow],
    state_types: Mapping[int, str],
) -> list[EventRow]:
    rows = sorted(history, key=lambda r: r.id)
    if rows:
        first = rows[0]
        base_queue, base_state = first.queue_id, first.state_id
        snapshot = (
            first.queue_id,
            first.state_id,
            first.owner_id,
            first.priority_id,
            first.type_id,
        )
        later = rows[1:]
    else:
        base_queue, base_state = ticket.queue_id, ticket.state_id
        snapshot = (
            ticket.queue_id,
            ticket.state_id,
            ticket.user_id,
            ticket.priority_id,
            ticket.type_id if ticket.type_id is not None else 0,
        )
        later = []

    events: list[EventRow] = []
    base_state_type = state_types.get(base_state)
    events.append(
        EventRow(
            ticket_id=ticket.id,
            kind="create",
            occurred_at=ticket.create_time,
            queue_id=base_queue,
            actor_user_id=ticket.create_by,
            to_id=base_queue,
            to_value=base_state_type,
        )
    )
    if base_state_type == CLOSED_STATE_TYPE:
        events.append(
            EventRow(
                ticket_id=ticket.id,
                kind="close",
                occurred_at=ticket.create_time,
                queue_id=base_queue,
                actor_user_id=ticket.create_by,
                to_id=base_state,
                to_value=base_state_type,
            )
        )

    prev = snapshot
    last_responsible: int | None = None
    for r in later:
        cur = (r.queue_id, r.state_id, r.owner_id, r.priority_id, r.type_id)
        for idx, kind in enumerate(("queue", "state", "owner", "priority", "type")):
            if cur[idx] == prev[idx]:
                continue
            ev = EventRow(
                ticket_id=ticket.id,
                kind=kind,
                occurred_at=r.create_time,
                queue_id=r.queue_id,
                history_id=r.id,
                actor_user_id=r.create_by,
                from_id=prev[idx],
                to_id=cur[idx],
            )
            if kind == "state":
                old_t, new_t = state_types.get(prev[1]), state_types.get(cur[1])
                ev = replace(ev, from_value=old_t, to_value=new_t)
                events.append(ev)
                if new_t == CLOSED_STATE_TYPE and old_t != CLOSED_STATE_TYPE:
                    events.append(replace(ev, kind="close"))
                elif old_t == CLOSED_STATE_TYPE and new_t != CLOSED_STATE_TYPE:
                    events.append(replace(ev, kind="reopen"))
            else:
                events.append(ev)
        prev = cur

        values = split_history_name(r.name)
        common: dict[str, Any] = {
            "ticket_id": ticket.id,
            "occurred_at": r.create_time,
            "queue_id": r.queue_id,
            "history_id": r.id,
            "actor_user_id": r.create_by,
        }
        htype = r.history_type
        if htype in ("ServiceUpdate", "SLAUpdate"):
            if len(values) < 2:
                continue
            events.append(
                EventRow(
                    kind="service" if htype == "ServiceUpdate" else "sla",
                    from_id=_int(_at(values, 3)),
                    to_id=_int(values[1]),
                    **common,
                )
            )
        elif htype == "ResponsibleUpdate":
            new_id = _int(_at(values, 1))
            if new_id is None:
                continue
            events.append(
                EventRow(kind="responsible", from_id=last_responsible, to_id=new_id, **common)
            )
            last_responsible = new_id
        elif htype == "Merged":
            events.append(
                EventRow(kind="merge", to_value=r.name.removeprefix("%%")[:100], **common)
            )
        elif htype == "TicketDynamicFieldUpdate":
            if (
                len(values) >= 4
                and values[0] == "FieldName"
                and values[1] == PROCESS_ACTIVITY_FIELD
            ):
                old = _at(values, 5)
                events.append(
                    EventRow(
                        kind="process_activity",
                        from_value=old or None,
                        to_value=values[3],
                        **common,
                    )
                )

    qrows = sorted(rows, key=lambda r: (r.create_time, r.id))
    for a in sorted(articles, key=lambda x: (x.create_time, x.id)):
        if a.sender_type == "agent":
            kind = "reply" if a.visible else "note"
        elif a.sender_type == "customer":
            kind = "customer_message"
        else:
            continue
        queue = base_queue
        for r in qrows:
            if r.create_time <= a.create_time:
                queue = r.queue_id
            else:
                break
        events.append(
            EventRow(
                ticket_id=ticket.id,
                kind=kind,
                occurred_at=a.create_time,
                queue_id=queue,
                article_id=a.id,
                actor_user_id=a.create_by,
            )
        )

    events.sort(
        key=lambda e: (
            e.occurred_at,
            e.history_id or 0,
            e.article_id or 0,
            EVENT_KINDS.index(e.kind),
        )
    )
    return _with_durations(ticket, events)


def _with_durations(ticket: TicketRow, events: list[EventRow]) -> list[EventRow]:
    last: dict[str, datetime] = {}
    out: list[EventRow] = []
    for e in events:
        if e.kind in _DURATION_KINDS:
            since = last.get(e.kind, ticket.create_time)
            seconds = max(0, int((e.occurred_at - since).total_seconds()))
            e = replace(e, from_duration_seconds=seconds)
            last[e.kind] = e.occurred_at
        out.append(e)
    return out
