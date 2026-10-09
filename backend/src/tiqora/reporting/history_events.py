"""Derive reporting events from ``ticket_history`` and articles (pure, no I/O).

Rules are fixed in the reporting-foundation plan (R4). Queue, state, owner,
priority and type changes are found by diffing the snapshot columns every
history row carries (values AFTER the action), which is identical for Znuny
and Tiqora writers. Service, SLA, responsible user, merges and process
activities have no snapshot column and are parsed from the ``%%`` names.

Unknown state ids (deleted or unmapped) keep their ``state`` event with a
``None`` type value but never produce ``close``/``reopen``. Name-parsed events
come from every history row including the first; only the column diff starts at
the second row. Garbled or empty names emit no event.

Rows moved here by a merge are skipped entirely (:func:`moved_by_merge`): Znuny's
``TicketMerge`` (and Tiqora's ``merge_tickets``) re-point every article-linked
history row of the merged ticket to the main ticket and stamp ``change_time``.
Those rows still carry the MERGED ticket's queue/state/owner/priority/type
snapshot, so diffing them would invent queue moves, closes and reopens on the
main ticket. The moved articles themselves stay (Znuny counts them for the main
ticket, e.g. ``_TicketGetFirstResponse``).
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
    #: ``ticket_history.change_time``; later than ``create_time`` only on rows a merge moved
    change_time: datetime | None = None


def moved_by_merge(row: HistoryRow) -> bool:
    """True for a row a ticket merge moved over from the merged ticket.

    ``HistoryAdd`` (Znuny and Tiqora) writes ``create_time`` and ``change_time`` in one
    statement (equal); only the merge's ``UPDATE ticket_history SET ticket_id = <main>,
    change_time = current_timestamp ... WHERE article_id IS NOT NULL`` makes them differ.
    A merge within the same second as the row's creation cannot be told apart
    (DATETIME has whole seconds) and is treated as the ticket's own row.
    """
    return (
        bool(row.article_id) and row.change_time is not None and row.change_time > row.create_time
    )


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
    # the merged ticket's rows describe the merged ticket, not this one
    rows = sorted((r for r in history if not moved_by_merge(r)), key=lambda r: r.id)
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
    else:
        base_queue, base_state = ticket.queue_id, ticket.state_id
        snapshot = (
            ticket.queue_id,
            ticket.state_id,
            ticket.user_id,
            ticket.priority_id,
            ticket.type_id if ticket.type_id is not None else 0,
        )

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
    for pos, r in enumerate(rows):
        cur = (r.queue_id, r.state_id, r.owner_id, r.priority_id, r.type_id)
        for idx, kind in enumerate(("queue", "state", "owner", "priority", "type")):
            # The first row is the baseline: its columns only start the diff.
            if pos == 0 or cur[idx] == prev[idx]:
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
                if old_t is None or new_t is None:
                    pass  # unknown state id: state event only, never close/reopen
                elif new_t == CLOSED_STATE_TYPE and old_t != CLOSED_STATE_TYPE:
                    events.append(replace(ev, kind="close"))
                elif old_t == CLOSED_STATE_TYPE and new_t != CLOSED_STATE_TYPE:
                    events.append(replace(ev, kind="reopen"))
            else:
                events.append(ev)
        prev = cur

        raw_name = r.name or ""
        values = split_history_name(raw_name)
        common: dict[str, Any] = {
            "ticket_id": ticket.id,
            "occurred_at": r.create_time,
            "queue_id": r.queue_id,
            "history_id": r.id,
            "actor_user_id": r.create_by,
        }
        htype = r.history_type
        if htype in ("ServiceUpdate", "SLAUpdate"):
            new_id, old_id = _int(_at(values, 1)), _int(_at(values, 3))
            if new_id is None and old_id is None:
                continue  # garbled or empty -> no event
            events.append(
                EventRow(
                    kind="service" if htype == "ServiceUpdate" else "sla",
                    from_id=old_id,
                    to_id=new_id,
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
            merged = raw_name.removeprefix("%%")[:100]
            if merged.strip("%"):
                events.append(EventRow(kind="merge", to_value=merged, **common))
        elif htype == "TicketDynamicFieldUpdate":
            if (
                len(values) >= 4
                and values[0] == "FieldName"
                and values[1] == PROCESS_ACTIVITY_FIELD
            ):
                new_v, old_v = _at(values, 3) or None, _at(values, 5) or None
                if new_v is None and old_v is None:
                    continue
                events.append(
                    EventRow(kind="process_activity", from_value=old_v, to_value=new_v, **common)
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
