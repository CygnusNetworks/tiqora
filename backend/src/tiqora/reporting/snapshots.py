"""Daily open-ticket snapshots computed from the derived ticket events (R6).

A snapshot row is the end-of-day stock of one (queue, state type, priority) in the
reporting time zone. The stock is replayed from the ``create``/``queue``/``state``/
``priority`` events, so history can be (re)built for any past day, and a day is
always replaced as a whole (idempotent). Days without open tickets write no rows.

Boundary: the state "at" a day end is the segment containing that instant with
``(start, end]`` semantics, i.e. an event stamped exactly at local midnight
belongs to the next day (and a ticket created at that instant is not yet counted).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import (
    TiqoraReportDailySnapshot,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.domain.queue_service import OPEN_STATE_TYPES
from tiqora.jobs.service import utc_now
from tiqora.reporting.history_events import EventRow

AGE_BUCKETS: Final = (
    ("age_0_1d", 1),
    ("age_1_3d", 3),
    ("age_3_7d", 7),
    ("age_7_30d", 30),
    ("age_30d_plus", None),
)
SNAPSHOT_KINDS: Final = ("create", "queue", "state", "priority")
DEFAULT_PRIORITY: Final = 3  # Znuny "3 normal"; only used when nothing says otherwise
UNKNOWN_STATE_TYPE: Final = "unknown"
_CANDIDATE_CHUNK: Final = 500
_KIND_ORDER: Final = {k: i for i, k in enumerate(SNAPSHOT_KINDS)}

SnapshotKey = tuple[date, int, str, int]  # (day, queue_id, state_type, priority_id)
TicketInput = (
    tuple[datetime, Sequence[EventRow]] | tuple[datetime, Sequence[EventRow], int]
)  # (created_at, events[, priority to assume when no priority event exists])


@dataclass(frozen=True, slots=True)
class Segment:
    start: datetime
    end: datetime | None
    queue_id: int
    state_type: str
    priority_id: int


def ticket_timeline(
    created_at: datetime,
    events: Sequence[EventRow],
    *,
    default_priority: int = DEFAULT_PRIORITY,
) -> list[Segment]:
    """Contiguous segments from the create/queue/state/priority events; last ends ``None``.

    The create event carries queue and state type but no priority: the initial priority
    is the first priority event's ``from_id``, else *default_priority* (callers pass the
    ticket's current priority, which equals the initial one when it never changed).
    """
    rel = sorted(
        (e for e in events if e.kind in _KIND_ORDER),
        key=lambda e: (e.occurred_at, e.history_id or 0, _KIND_ORDER[e.kind]),
    )
    create = next((e for e in rel if e.kind == "create"), None)
    changes = [e for e in rel if e.kind != "create"]
    queue_id = create.queue_id if create is not None else (changes[0].queue_id if changes else 0)
    state_type = (create.to_value if create is not None else None) or UNKNOWN_STATE_TYPE
    first_prio = next((e for e in changes if e.kind == "priority"), None)
    priority = (
        first_prio.from_id
        if first_prio is not None and first_prio.from_id is not None
        else default_priority
    )
    if create is not None and create.to_id is not None:
        queue_id = create.to_id

    segments: list[Segment] = []
    start = created_at
    for e in changes:
        at = max(e.occurred_at, start)
        segments.append(Segment(start, at, queue_id, state_type, priority))
        start = at
        if e.kind == "queue" and e.to_id is not None:
            queue_id = e.to_id
        elif e.kind == "state":
            state_type = e.to_value or UNKNOWN_STATE_TYPE
        elif e.kind == "priority" and e.to_id is not None:
            priority = e.to_id
    segments.append(Segment(start, None, queue_id, state_type, priority))
    return segments


def day_end_instants(days: Sequence[date], zone: str) -> dict[date, datetime]:
    """Naive UTC instant of local midnight AFTER each day (DST-correct)."""
    tz = ZoneInfo(zone)
    return {
        d: datetime.combine(d + timedelta(days=1), time.min, tz)
        .astimezone(UTC)
        .replace(tzinfo=None)
        for d in days
    }


def _bucket(age_days: float) -> int:
    for idx, (_, upper) in enumerate(AGE_BUCKETS):
        if upper is None or age_days < upper:
            return idx
    return len(AGE_BUCKETS) - 1  # pragma: no cover - last bucket has no bound


def compute_snapshot_counts(
    tickets: Iterable[TicketInput],
    ends: Mapping[date, datetime],
) -> dict[SnapshotKey, list[int]]:
    """``[open_count, age_0_1d, ...]`` per key; only open state types are counted."""
    ordered = sorted(ends.items(), key=lambda kv: kv[1])
    instants = [e for _, e in ordered]
    out: dict[SnapshotKey, list[int]] = {}
    for item in tickets:
        created_at, events = item[0], item[1]
        default_priority = item[2] if len(item) == 3 else DEFAULT_PRIORITY
        for seg in ticket_timeline(created_at, events, default_priority=default_priority):
            if seg.state_type not in OPEN_STATE_TYPES:
                continue
            # day end e belongs to the segment with start < e <= end
            lo = bisect_right(instants, seg.start)
            hi = len(instants) if seg.end is None else bisect_right(instants, seg.end)
            for idx in range(lo, hi):
                day, end = ordered[idx]
                row = out.setdefault(
                    (day, seg.queue_id, seg.state_type, seg.priority_id),
                    [0] * (1 + len(AGE_BUCKETS)),
                )
                row[0] += 1
                row[1 + _bucket((end - created_at).total_seconds() / 86400)] += 1
    return out


async def write_snapshots(
    session: AsyncSession,
    days: Sequence[date],
    counts: Mapping[SnapshotKey, list[int]],
    *,
    now: datetime,
) -> int:
    """Replace the rows of *days* with *counts*; flush only. Returns rows written."""
    if not days:
        return 0
    wanted = set(days)
    await session.execute(
        delete(TiqoraReportDailySnapshot).where(TiqoraReportDailySnapshot.snapshot_date.in_(days))
    )
    rows = [
        {
            "snapshot_date": day,
            "queue_id": queue_id,
            "state_type": state_type,
            "priority_id": priority_id,
            "open_count": vals[0],
            **{name: vals[1 + i] for i, (name, _) in enumerate(AGE_BUCKETS)},
            "built_at": now,
        }
        for (day, queue_id, state_type, priority_id), vals in sorted(counts.items())
        if day in wanted
    ]
    if rows:
        await session.execute(insert(TiqoraReportDailySnapshot), rows)
    await session.flush()
    return len(rows)


async def snapshot_days(
    factory: async_sessionmaker[AsyncSession],
    days: Sequence[date],
    *,
    zone: str,
    now: datetime | None = None,
) -> int:
    """(Re)compute and replace the snapshots of *days*; one commit. Returns rows written."""
    days = sorted(set(days))
    if not days:
        return 0
    ends = day_end_instants([days[0] - timedelta(days=1), *days], zone)
    window_start = ends.pop(days[0] - timedelta(days=1))
    last_end = ends[days[-1]]
    stamp = now if now is not None else utc_now()
    counts: dict[SnapshotKey, list[int]] = {}
    async with factory() as session:
        candidates = (
            (
                await session.execute(
                    select(
                        TiqoraReportTicketFact.ticket_id,
                        TiqoraReportTicketFact.created_at,
                        TiqoraReportTicketFact.priority_id,
                    )
                    .where(
                        TiqoraReportTicketFact.created_at < last_end,
                        or_(
                            TiqoraReportTicketFact.state_type.in_(OPEN_STATE_TYPES),
                            TiqoraReportTicketFact.last_event_at >= window_start,
                        ),
                    )
                    .order_by(TiqoraReportTicketFact.ticket_id)
                )
            )
            .tuples()
            .all()
        )
        for i in range(0, len(candidates), _CANDIDATE_CHUNK):
            chunk = candidates[i : i + _CANDIDATE_CHUNK]
            by_ticket: dict[int, list[EventRow]] = {tid: [] for tid, _, _ in chunk}
            ev_rows = await session.execute(
                select(TiqoraReportTicketEvent)
                .where(
                    TiqoraReportTicketEvent.ticket_id.in_(list(by_ticket)),
                    TiqoraReportTicketEvent.kind.in_(SNAPSHOT_KINDS),
                )
                .order_by(TiqoraReportTicketEvent.id)
            )
            for ev in ev_rows.scalars():
                by_ticket[ev.ticket_id].append(
                    EventRow(
                        ticket_id=ev.ticket_id,
                        kind=ev.kind,
                        occurred_at=ev.occurred_at,
                        queue_id=ev.queue_id,
                        history_id=ev.history_id,
                        from_id=ev.from_id,
                        to_id=ev.to_id,
                        from_value=ev.from_value,
                        to_value=ev.to_value,
                    )
                )
            part = compute_snapshot_counts(
                [(created, by_ticket[tid], int(prio)) for tid, created, prio in chunk], ends
            )
            for key, vals in part.items():
                acc = counts.setdefault(key, [0] * len(vals))
                for j, v in enumerate(vals):
                    acc[j] += v
        written = await write_snapshots(session, days, counts, now=stamp)
        await session.commit()
    return written
