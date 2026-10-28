"""Read contract of the change module (design I14, interface X2): list, counts, upcoming, my tasks,
calendar and projected service availability (PSA).

Other plans (dashboard widgets, the AI features, the statistics) read changes only through this
module, so permission filtering lives here: an unreadable change or task ticket is left out, never
shown as "hidden". Every function returns empty results outside Mode B (``itsm.mode = tiqora``),
never commits and never joins ``tiqora_*`` with Znuny tables (keys are loaded first, then the other
side is read with ``IN`` lists of at most :data:`CHUNK`).

Bounds (the endpoints sit behind a 90 s proxy): a scan reads at most :data:`LIST_SCAN_CAP` change
rows, newest first, and says ``truncated`` when more matched; the calendar and the PSA cover at
most :data:`MAX_RANGE` and the PSA at most :data:`PSA_SERVICE_CAP` services. Conflict flags of the
calendar are computed in one batched pass over the changes of the range and their stored impact
rows (pairwise :func:`detect_conflicts` in memory), never one load per change.

Times are naive UTC like the stored columns. ``service_ids`` of a change are the ticket's service
plus the change's extra services; the service filter also matches services reached through the
stored impact set.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, and_, bindparam, case, func, not_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import to_utc_naive
from tiqora.cmdb.impact import services_reached_per_ci
from tiqora.config import get_settings
from tiqora.domain.ticket_access import CHUNK, TicketFacts, readable_tickets
from tiqora.itsm.change.conflicts import (
    OVERLAP_PHASES,
    Conflict,
    ImpactSet,
    OtherChange,
    TaskWindow,
    detect_conflicts,
    impact_sets,
)
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import FreezeOccurrence, occurrences
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeService,
    TiqoraItsmChangeTask,
)
from tiqora.itsm.change.phases import FINAL_PHASES, PHASES
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.mode import get_itsm_mode
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.znuny.sysconfig import SysConfig

#: change rows one scan reads (newest first); the answer says ``truncated`` beyond it
LIST_SCAN_CAP: Final = 5000
#: longest range the calendar and the PSA cover
MAX_RANGE: Final = timedelta(days=92)
#: services one PSA answer carries
PSA_SERVICE_CAP: Final = 200
logger = logging.getLogger(__name__)
#: phases that do not occupy the schedule
_OFF_CALENDAR: Final = ("cancelled", "rejected")
#: phases whose tasks are no longer to be done ("rejected" can be reopened, but is parked)
_TASKS_DONE_PHASES: Final = (*sorted(FINAL_PHASES), "rejected")
#: impact row kind (a variable: the cross-join guard reads string literals as table names)
_SERVICE_KIND: Final = "service"
_CLOSED_STATE_TYPES: Final = frozenset({"closed", "removed", "merged"})
_SEVERITY_RANK: Final = {"info": 0, "warn": 1, "block": 2}


# --- contract types (field names are fixed, additive changes only) ---------------------------


@dataclass(frozen=True, slots=True)
class ChangeListItem:
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    owner_id: int
    change_type: str
    phase: str
    outcome: str | None
    #: effective level: the manager's override wins over the computed one
    risk_level: str | None
    planned_start: datetime | None
    planned_end: datetime | None
    service_ids: tuple[int, ...]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChangeQuery:
    text: str | None = None
    phases: tuple[str, ...] = ()
    change_types: tuple[str, ...] = ()
    service_id: int | None = None
    ci_id: int | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    mine: Literal["", "owned", "created"] = ""
    order: Literal["planned_start", "updated"] = "updated"
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ChangePage:
    items: tuple[ChangeListItem, ...]
    #: matches among the scanned rows (a lower bound when ``truncated``)
    total: int
    #: more than :data:`LIST_SCAN_CAP` changes matched the database filters
    truncated: bool


@dataclass(frozen=True, slots=True)
class ChangeRef:
    ticket_id: int
    change_type: str
    phase: str
    risk_level: str | None
    planned_start: datetime | None
    planned_end: datetime | None


@dataclass(frozen=True, slots=True)
class ChangeTaskRef:
    change_ticket_id: int
    #: ``None`` when the caller may not read the change (or no caller was given)
    change_tn: str | None
    position: int
    task_kind: str
    planned_start: datetime | None
    planned_end: datetime | None


@dataclass(frozen=True, slots=True)
class TaskListItem:
    ticket_id: int
    tn: str
    title: str
    queue_id: int
    owner_id: int
    state: str
    state_type: str
    finished: bool
    task_kind: str
    position: int
    planned_start: datetime | None
    planned_end: datetime | None
    change_ticket_id: int
    #: ``None`` when the caller may not read the change ticket
    change_tn: str | None
    change_title: str | None


@dataclass(frozen=True, slots=True)
class CalendarChange:
    item: ChangeListItem
    #: highest severity among the change's conflicts (``None`` = no conflict); a conflict with a
    #: change the caller cannot read counts, its reference is never given
    conflict_severity: Literal["info", "warn", "block"] | None
    conflict_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangeCalendar:
    start: datetime
    end: datetime
    changes: tuple[CalendarChange, ...]
    tasks: tuple[TaskListItem, ...]
    freezes: tuple[FreezeOccurrence, ...]
    #: more changes or tasks than :data:`LIST_SCAN_CAP` in the range (flags and tasks incomplete)
    truncated: bool
    #: the freeze occurrences were cut off (shorten the range)
    freezes_truncated: bool


@dataclass(frozen=True, slots=True)
class ServiceWindow:
    kind: Literal["change", "freeze"]
    #: change ticket id or freeze id
    ref_id: int
    #: change number; ``None`` for a freeze
    tn: str | None
    #: change title or freeze name
    title: str
    phase: str | None
    change_type: str | None
    enforcement: str | None
    #: the change names the service itself (``None`` for a freeze)
    direct: bool | None
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class ServiceAvailabilityRow:
    service_id: int
    service_name: str
    #: minutes of the range covered by planned change windows (overlaps counted once)
    minutes_affected: int
    change_count: int
    windows: tuple[ServiceWindow, ...]


# --- helpers ---------------------------------------------------------------------------------


async def _is_mode_b(session: AsyncSession) -> bool:
    return (await get_itsm_mode(session)).mode == "tiqora"


def _chunked(ids: Iterable[int]) -> Iterable[list[int]]:
    wanted = sorted(set(ids))
    for start in range(0, len(wanted), CHUNK):
        yield wanted[start : start + CHUNK]


def _check_range(lo: datetime, hi: datetime) -> None:
    if hi <= lo:
        raise ChangeError("range_invalid", 422)
    if hi - lo > MAX_RANGE:
        raise ChangeError("range_too_large", 422)


def _window_cond(
    start_col: Any,
    end_col: Any,
    lo: datetime | None,
    hi: datetime | None,
) -> list[ColumnElement[bool]]:
    """Rows whose span overlaps ``[lo, hi)``; a span with one bound counts as a point."""
    first = func.coalesce(start_col, end_col)
    last = func.coalesce(end_col, start_col)
    cond: list[ColumnElement[bool]] = []
    if lo is not None:
        cond.append(or_(last > lo, and_(last == first, last >= lo)))
    if hi is not None:
        cond.append(first < hi)
    return cond


def _service_cond(service_ids: Sequence[int]) -> ColumnElement[bool]:
    keys = [str(s) for s in service_ids]
    return or_(
        TiqoraItsmChange.ticket_id.in_(
            select(TiqoraItsmChangeImpact.ticket_id).where(
                TiqoraItsmChangeImpact.kind == _SERVICE_KIND,
                TiqoraItsmChangeImpact.ref_key.in_(keys),
            )
        ),
        TiqoraItsmChange.ticket_id.in_(
            select(TiqoraItsmChangeService.ticket_id).where(
                TiqoraItsmChangeService.service_id.in_(list(service_ids))
            )
        ),
    )


#: public names for the portal schedule (same module family, one definition)
chunked = _chunked
window_cond = _window_cond
service_cond = _service_cond


@dataclass(frozen=True, slots=True)
class _Rec:
    ticket_id: int
    change_type: str
    phase: str
    outcome: str | None
    risk_level: str | None
    planned_start: datetime | None
    planned_end: datetime | None
    updated_at: datetime
    created_by: int


_REC_COLUMNS = (
    TiqoraItsmChange.ticket_id,
    TiqoraItsmChange.change_type,
    TiqoraItsmChange.phase,
    TiqoraItsmChange.outcome,
    TiqoraItsmChange.risk_override_level,
    TiqoraItsmChange.risk_level,
    TiqoraItsmChange.planned_start,
    TiqoraItsmChange.planned_end,
    TiqoraItsmChange.updated_at,
    TiqoraItsmChange.created_by,
)


def _rec(row: Sequence[object]) -> _Rec:
    tid, ctype, phase, outcome, override, level, start, end, updated, created_by = row
    return _Rec(
        ticket_id=int(str(tid)),
        change_type=str(ctype),
        phase=str(phase),
        outcome=None if outcome is None else str(outcome),
        risk_level=None if (override or level) is None else str(override or level),
        planned_start=start if isinstance(start, datetime) else None,
        planned_end=end if isinstance(end, datetime) else None,
        updated_at=updated if isinstance(updated, datetime) else datetime.min,
        created_by=int(str(created_by)),
    )


async def _scan(
    session: AsyncSession, where: Sequence[ColumnElement[bool]], *, order_by_start: bool = False
) -> tuple[list[_Rec], bool]:
    """Change rows matching ``where``, at most :data:`LIST_SCAN_CAP`; the flag says more exist.

    Newest first (the cap keeps the most recently changed), or by planned start for the calendar.
    """
    stmt = select(*_REC_COLUMNS).where(*where)
    if order_by_start:
        # NULLs last on both dialects (MariaDB sorts them first by default)
        stmt = stmt.order_by(
            case((TiqoraItsmChange.planned_start.is_(None), 1), else_=0),
            TiqoraItsmChange.planned_start,
            TiqoraItsmChange.ticket_id,
        )
    else:
        stmt = stmt.order_by(TiqoraItsmChange.updated_at.desc(), TiqoraItsmChange.ticket_id.desc())
    rows = (await session.execute(stmt.limit(LIST_SCAN_CAP + 1))).all()
    return [_rec(r) for r in rows[:LIST_SCAN_CAP]], len(rows) > LIST_SCAN_CAP


async def _owners(session: AsyncSession, ids: Iterable[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    stmt = text("SELECT id, user_id FROM ticket WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for part in _chunked(ids):
        out.update({int(r[0]): int(r[1]) for r in await session.execute(stmt, {"ids": part})})
    return out


async def _extra_services(session: AsyncSession, ids: Iterable[int]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for part in _chunked(ids):
        rows = await session.execute(
            select(TiqoraItsmChangeService.ticket_id, TiqoraItsmChangeService.service_id).where(
                TiqoraItsmChangeService.ticket_id.in_(part)
            )
        )
        for tid, sid in rows:
            out[int(tid)].add(int(sid))
    return out


async def _readable(
    session: AsyncSession, ids: Iterable[int], *, user_id: int
) -> dict[int, TicketFacts]:
    return await readable_tickets(session, sorted(set(ids)), user_id=user_id)


async def _items(
    session: AsyncSession,
    recs: Sequence[_Rec],
    facts: dict[int, TicketFacts],
    owners: dict[int, int],
) -> list[ChangeListItem]:
    """List items of the records whose ticket is in ``facts`` (the readable ones), in order."""
    shown = [r for r in recs if r.ticket_id in facts]
    extras = await _extra_services(session, [r.ticket_id for r in shown])
    out: list[ChangeListItem] = []
    for r in shown:
        f = facts[r.ticket_id]
        services = set(extras.get(r.ticket_id, ()))
        if f.service_id is not None:
            services.add(f.service_id)
        out.append(
            ChangeListItem(
                ticket_id=r.ticket_id,
                tn=f.tn,
                title=f.title,
                queue_id=f.queue_id,
                owner_id=owners.get(r.ticket_id, 0),
                change_type=r.change_type,
                phase=r.phase,
                outcome=r.outcome,
                risk_level=r.risk_level,
                planned_start=r.planned_start,
                planned_end=r.planned_end,
                service_ids=tuple(sorted(services)),
                updated_at=r.updated_at,
            )
        )
    return out


def _sort_key_start(item: ChangeListItem) -> tuple[bool, datetime, int]:
    return (item.planned_start is None, item.planned_start or datetime.min, item.ticket_id)


async def items_for_changes(
    session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
) -> list[ChangeListItem]:
    """List items of the given change tickets the caller may read, in the order given. Tickets
    without a change record and unreadable ones are left out."""
    if not ticket_ids or not await _is_mode_b(session):
        return []
    recs: dict[int, _Rec] = {}
    for part in _chunked(ticket_ids):
        rows = await session.execute(
            select(*_REC_COLUMNS).where(TiqoraItsmChange.ticket_id.in_(part))
        )
        recs.update({r.ticket_id: r for r in map(_rec, rows)})
    ordered = [recs[t] for t in dict.fromkeys(ticket_ids) if t in recs]
    facts = await _readable(session, list(recs), user_id=user_id)
    return await _items(session, ordered, facts, await _owners(session, facts))


async def _readable_cis(session: AsyncSession, ci_ids: Iterable[int], *, user_id: int) -> set[int]:
    """The CI ids among ``ci_ids`` the caller may read (none when the CMDB is unavailable)."""
    wanted = sorted(set(ci_ids))
    provider = get_ci_provider("tiqora", SysConfig(session))
    if provider is None or not wanted:
        return set()
    out: set[int] = set()
    for part in _chunked(wanted):
        got = await provider.get_many(session, [CiRef("tiqora", c) for c in part], user_id=user_id)
        out.update(ref.id for ref in got)
    return out


async def _visible_services(
    session: AsyncSession, sets: dict[int, ImpactSet], ids: Iterable[int], *, user_id: int
) -> dict[int, frozenset[int]]:
    """Per change the services of its stored impact set the caller may see: the direct ones and
    those reached from CIs the caller can read (the rule of ``read_impact``; services reached only
    through hidden CIs stay unnamed)."""
    wanted = [i for i in ids if i in sets]
    out = {i: sets[i].direct_services for i in wanted}
    needs = [i for i in wanted if sets[i].services - sets[i].direct_services]
    if not needs:
        return out
    readable = await _readable_cis(
        session, {c for i in needs for c in sets[i].cis}, user_id=user_id
    )
    # one batched read; the reach of a CI set is the union of its CIs' reach, taken per change
    per_ci = await services_reached_per_ci(session, readable)
    for i in needs:
        reached = frozenset().union(*(per_ci.get(c, frozenset()) for c in sets[i].cis & readable))
        out[i] = sets[i].direct_services | (sets[i].services & reached)
    return out


def _like_escape(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


async def _text_candidates(session: AsyncSession, needle: str) -> tuple[list[int], bool]:
    """Change tickets whose number starts with ``needle`` or whose title contains it
    (case-insensitive via ``LOWER``, ``%``/``_`` literal), an exact number first, then newest
    first, at most :data:`LIST_SCAN_CAP`; the flag says more exist. A part inside a number does
    not match, only its beginning.

    Resolved on the Znuny side first so an old change is found by its number whatever the scan
    cap; the ids then restrict the ``tiqora_itsm_change`` scan.
    """
    cfg = await load_itsm_settings(session, get_settings())
    stmt = text(
        "SELECT t.id FROM ticket t JOIN ticket_type tt ON tt.id = t.type_id"
        " WHERE tt.name = :type AND (t.tn LIKE :tnpat ESCAPE '!'"
        " OR LOWER(t.title) LIKE :pat ESCAPE '!')"
        " ORDER BY CASE WHEN t.tn = :q THEN 0 ELSE 1 END, t.id DESC LIMIT :lim"
    )
    rows = await session.execute(
        stmt,
        {
            "type": cfg.change_type,
            "q": needle,
            "tnpat": f"{_like_escape(needle)}%",
            "pat": f"%{_like_escape(needle.lower())}%",
            "lim": LIST_SCAN_CAP + 1,
        },
    )
    ids = [int(r[0]) for r in rows]
    return ids[:LIST_SCAN_CAP], len(ids) > LIST_SCAN_CAP


# --- list ------------------------------------------------------------------------------------


async def list_changes(session: AsyncSession, q: ChangeQuery, *, user_id: int) -> ChangePage:
    """Changes the caller may read, filtered, sorted and paged.

    The database filters (phase, type, window, service, CI, "created by me") run on
    ``tiqora_itsm_change`` and cap at :data:`LIST_SCAN_CAP`; the read permission, "owned by me"
    and the text search (number or title, case-insensitive) is resolved on the ticket side first
    and restricts the scan. A ``service_id`` / ``ci_id`` filter never matches through a path the
    caller cannot see (see :func:`_visible_services`).
    """
    if not await _is_mode_b(session):
        return ChangePage((), 0, False)
    ch = TiqoraItsmChange
    where: list[ColumnElement[bool]] = []
    candidates_truncated = False
    needle = (q.text or "").strip()
    if needle:
        found, candidates_truncated = await _text_candidates(session, needle)
        if not found:
            return ChangePage((), 0, False)
        where.append(or_(*[ch.ticket_id.in_(part) for part in _chunked(found)]))
    if q.ci_id is not None and q.ci_id not in await _readable_cis(
        session, [q.ci_id], user_id=user_id
    ):
        return ChangePage((), 0, False)
    if q.phases:
        where.append(ch.phase.in_(list(q.phases)))
    if q.change_types:
        where.append(ch.change_type.in_(list(q.change_types)))
    where.extend(_window_cond(ch.planned_start, ch.planned_end, q.window_start, q.window_end))
    if q.service_id is not None:
        where.append(_service_cond([q.service_id]))
    if q.ci_id is not None:
        where.append(
            ch.ticket_id.in_(
                select(TiqoraItsmChangeImpact.ticket_id).where(
                    TiqoraItsmChangeImpact.kind == "ci",
                    TiqoraItsmChangeImpact.ref_key == str(q.ci_id),
                )
            )
        )
    if q.mine == "created":
        where.append(ch.created_by == user_id)
    recs, truncated = await _scan(session, where)
    truncated = truncated or candidates_truncated
    facts = await _readable(session, [r.ticket_id for r in recs], user_id=user_id)
    owners = await _owners(session, facts)
    items = await _items(session, recs, facts, owners)
    if q.mine == "owned":
        items = [i for i in items if i.owner_id == user_id]
    if q.service_id is not None:
        # a service named by the ticket itself always matches; one reached through the impact
        # set only when the caller can see the path
        indirect = [i.ticket_id for i in items if q.service_id not in i.service_ids]
        sets = await impact_sets(session, indirect)
        visible = await _visible_services(session, sets, indirect, user_id=user_id)
        items = [
            i
            for i in items
            if q.service_id in i.service_ids or q.service_id in visible.get(i.ticket_id, ())
        ]
    if q.order == "planned_start":
        items.sort(key=_sort_key_start)
    else:  # the scan already is newest first
        items.sort(key=lambda i: (i.updated_at, i.ticket_id), reverse=True)
    page = items[q.offset : q.offset + q.limit]
    return ChangePage(tuple(page), len(items), truncated)


async def upcoming_changes(
    session: AsyncSession, *, user_id: int, start: datetime, end: datetime, limit: int = 20
) -> list[ChangeListItem]:
    """Scheduled and running changes whose window touches ``[start, end)``, earliest first."""
    page = await list_changes(
        session,
        ChangeQuery(
            phases=("scheduled", "implementing"),
            window_start=to_utc_naive(start),
            window_end=to_utc_naive(end),
            order="planned_start",
            limit=limit,
        ),
        user_id=user_id,
    )
    return list(page.items)


async def phase_counts(
    session: AsyncSession, *, user_id: int, queue_ids: Sequence[int] | None = None
) -> dict[str, int]:
    """Readable changes per phase (every phase is a key). Counts the open phases only (scans at
    most :data:`LIST_SCAN_CAP` of them, so a long history of finished changes cannot push live ones
    out); ``closed`` and ``cancelled`` are always 0."""
    counts: dict[str, int] = dict.fromkeys(PHASES, 0)
    if not await _is_mode_b(session):
        return counts
    recs, _ = await _scan(session, [not_(TiqoraItsmChange.phase.in_(sorted(FINAL_PHASES)))])
    facts = await _readable(session, [r.ticket_id for r in recs], user_id=user_id)
    for r in recs:
        f = facts.get(r.ticket_id)
        if f is None or (queue_ids is not None and f.queue_id not in queue_ids):
            continue
        counts[r.phase] = counts.get(r.phase, 0) + 1
    return counts


# --- tasks -----------------------------------------------------------------------------------


async def _task_details(
    session: AsyncSession, ids: Iterable[int]
) -> dict[int, tuple[str, str, int, int, str, str]]:
    """``{id: (tn, title, queue_id, owner_id, state, state_type)}``, whatever the caller may read
    (callers filter first)."""
    out: dict[int, tuple[str, str, int, int, str, str]] = {}
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id, t.user_id, ts.name, tst.name FROM ticket t"
        " LEFT JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " LEFT JOIN ticket_state_type tst ON tst.id = ts.type_id WHERE t.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    for part in _chunked(ids):
        for r in await session.execute(stmt, {"ids": part}):
            out[int(r[0])] = (
                str(r[1]),
                str(r[2] or ""),
                int(r[3]),
                int(r[4]),
                str(r[5] or ""),
                str(r[6] or ""),
            )
    return out


async def _task_items(
    session: AsyncSession, rows: Sequence[TiqoraItsmChangeTask], *, user_id: int
) -> list[TaskListItem]:
    """Task list items of the rows whose task ticket the caller may read, in the rows' order."""
    readable = await _readable(session, [r.ticket_id for r in rows], user_id=user_id)
    details = await _task_details(session, readable)
    changes = await _readable(session, [r.change_ticket_id for r in rows], user_id=user_id)
    out: list[TaskListItem] = []
    for r in rows:
        d = details.get(r.ticket_id)
        if r.ticket_id not in readable or d is None:
            continue
        change = changes.get(r.change_ticket_id)
        out.append(
            TaskListItem(
                ticket_id=r.ticket_id,
                tn=d[0],
                title=d[1],
                queue_id=d[2],
                owner_id=d[3],
                state=d[4],
                state_type=d[5],
                finished=d[5] in _CLOSED_STATE_TYPES,
                task_kind=r.task_kind,
                position=r.position,
                planned_start=r.planned_start,
                planned_end=r.planned_end,
                change_ticket_id=r.change_ticket_id,
                change_tn=None if change is None else change.tn,
                change_title=None if change is None else change.title,
            )
        )
    return out


async def my_tasks(
    session: AsyncSession, *, user_id: int, open_only: bool = True, limit: int = 100
) -> list[TaskListItem]:
    """Task tickets the user owns and may read, soonest planned start first.

    ``open_only``: tasks in an open state type (not closed/removed/merged) of changes that are
    still alive: not closed, cancelled or rejected (a rejected change is parked until reworked).
    The owner filter runs on the ticket side first, so the scan cap (:data:`LIST_SCAN_CAP`,
    newest tickets) only ever sees the user's own tasks.
    """
    if not await _is_mode_b(session):
        return []
    cfg = await load_itsm_settings(session, get_settings())
    sql = (
        "SELECT t.id FROM ticket t JOIN ticket_type tt ON tt.id = t.type_id"
        " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
        " WHERE t.user_id = :u AND tt.name = :type"
    )
    params: dict[str, object] = {"u": user_id, "type": cfg.change_task_type, "lim": LIST_SCAN_CAP}
    if open_only:
        sql += " AND tst.name NOT IN :closed"
        params["closed"] = sorted(_CLOSED_STATE_TYPES)
    stmt = text(sql + " ORDER BY t.id DESC LIMIT :lim")
    if open_only:
        stmt = stmt.bindparams(bindparam("closed", expanding=True))
    owned = [int(r[0]) for r in await session.execute(stmt, params)]
    t = TiqoraItsmChangeTask
    rows: list[TiqoraItsmChangeTask] = []
    for part in _chunked(owned):
        q = select(t).where(t.ticket_id.in_(part))
        if open_only:
            q = q.where(
                t.change_ticket_id.in_(
                    select(TiqoraItsmChange.ticket_id).where(
                        not_(TiqoraItsmChange.phase.in_(list(_TASKS_DONE_PHASES)))
                    )
                )
            )
        rows.extend((await session.execute(q)).scalars())
    items = await _task_items(session, rows, user_id=user_id)
    if open_only:
        items = [i for i in items if not i.finished]
    far = datetime.max
    items.sort(key=lambda i: (i.planned_start or far, i.change_ticket_id, i.position, i.ticket_id))
    return items[: max(limit, 0)]


# --- refs ------------------------------------------------------------------------------------


async def change_refs_for_tickets(
    session: AsyncSession, ticket_ids: Sequence[int], *, mode_b: bool | None = None
) -> dict[int, ChangeRef]:
    """Short change facts of the given tickets (those that are changes). No permission filter:
    the callers already hold the tickets. ``mode_b`` skips the mode read for a caller that knows."""
    if not ticket_ids or not (mode_b if mode_b is not None else await _is_mode_b(session)):
        return {}
    out: dict[int, ChangeRef] = {}
    for part in _chunked(ticket_ids):
        rows = await session.execute(
            select(*_REC_COLUMNS).where(TiqoraItsmChange.ticket_id.in_(part))
        )
        for row in rows:
            r = _rec(row)
            out[r.ticket_id] = ChangeRef(
                r.ticket_id, r.change_type, r.phase, r.risk_level, r.planned_start, r.planned_end
            )
    return out


async def task_refs_for_tickets(
    session: AsyncSession,
    ticket_ids: Sequence[int],
    *,
    user_id: int | None = None,
    mode_b: bool | None = None,
) -> dict[int, ChangeTaskRef]:
    """Short task facts of the given tickets (those that are change tasks).

    ``change_tn`` is only given when ``user_id`` is passed and may read the change ticket.
    ``mode_b`` skips the mode read for a caller that knows.
    """
    if not ticket_ids or not (mode_b if mode_b is not None else await _is_mode_b(session)):
        return {}
    rows: list[TiqoraItsmChangeTask] = []
    for part in _chunked(ticket_ids):
        rows.extend(
            (
                await session.execute(
                    select(TiqoraItsmChangeTask).where(TiqoraItsmChangeTask.ticket_id.in_(part))
                )
            ).scalars()
        )
    changes = (
        {}
        if user_id is None
        else await _readable(session, [r.change_ticket_id for r in rows], user_id=user_id)
    )
    return {
        r.ticket_id: ChangeTaskRef(
            change_ticket_id=r.change_ticket_id,
            change_tn=None if r.change_ticket_id not in changes else changes[r.change_ticket_id].tn,
            position=r.position,
            task_kind=r.task_kind,
            planned_start=r.planned_start,
            planned_end=r.planned_end,
        )
        for r in rows
    }


# --- freezes, calendar -----------------------------------------------------------------------


async def active_freezes(
    session: AsyncSession, *, start: datetime, end: datetime
) -> list[FreezeOccurrence]:
    """Occurrences of the valid freeze windows that overlap ``[start, end)``."""
    if not await _is_mode_b(session):
        return []
    return (await occurrences(session, start=to_utc_naive(start), end=to_utc_naive(end))).items


async def _task_windows(
    session: AsyncSession, change_ids: Iterable[int], *, user_id: int
) -> dict[int, list[TaskWindow]]:
    """Task windows per change; a task ticket the caller cannot read is left out."""
    rows: list[tuple[int, int, datetime | None, datetime | None]] = []
    for part in _chunked(change_ids):
        found = await session.execute(
            select(
                TiqoraItsmChangeTask.change_ticket_id,
                TiqoraItsmChangeTask.ticket_id,
                TiqoraItsmChangeTask.planned_start,
                TiqoraItsmChangeTask.planned_end,
            ).where(TiqoraItsmChangeTask.change_ticket_id.in_(part))
        )
        rows.extend((int(c), int(t), s, e) for c, t, s, e in found)
    readable = await _readable(session, [t for _, t, _, _ in rows], user_id=user_id)
    out: dict[int, list[TaskWindow]] = defaultdict(list)
    for change_id, task_id, s, e in rows:
        if task_id in readable:
            out[change_id].append(TaskWindow(task_id, s, e))
    return out


def _flag(
    found: Sequence[Conflict],
) -> tuple[Literal["info", "warn", "block"] | None, tuple[str, ...]]:
    if not found:
        return None, ()
    top = max(found, key=lambda c: _SEVERITY_RANK[c.severity]).severity
    return top, tuple(sorted({c.kind for c in found}))


async def calendar(
    session: AsyncSession,
    *,
    user_id: int,
    start: datetime,
    end: datetime,
    service_ids: Sequence[int] = (),
    include_tasks: bool = True,
) -> ChangeCalendar:
    """Changes, tasks and freeze occurrences of ``[start, end)`` with conflict flags.

    Outside Mode B the answer is empty; the range may not exceed :data:`MAX_RANGE` (422
    ``range_too_large``). Cancelled and rejected changes (and their tasks) are not shown.
    ``service_ids`` keeps the changes that touch one of the services the caller can see (the
    rule of ``read_impact``) and the tasks of those changes; freezes are always shown.

    Conflict flags equal what ``read_conflicts`` reports: they are computed against all changes
    and freeze occurrences over the union of the shown changes' own windows (at most
    :data:`MAX_RANGE` beyond the range), readable or not, in one batched pass.

    ``truncated`` also covers the conflict pool and ``freezes_truncated`` the widened freeze
    window: a flag may be incomplete although everything shown is complete. Tasks are listed when
    their own window touches the range; under a service filter only those of the shown changes.
    """
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    if not await _is_mode_b(session):
        return ChangeCalendar(lo, hi, (), (), (), False, False)
    _check_range(lo, hi)
    ch = TiqoraItsmChange
    recs, truncated = await _scan(
        session,
        [
            ch.phase.not_in(list(_OFF_CALENDAR)),
            *_window_cond(ch.planned_start, ch.planned_end, lo, hi),
        ],
        order_by_start=True,
    )
    facts = await _readable(session, [r.ticket_id for r in recs], user_id=user_id)
    owners = await _owners(session, facts)
    items = {i.ticket_id: i for i in await _items(session, recs, facts, owners)}
    sets = await impact_sets(session, [r.ticket_id for r in recs])
    wanted = set(service_ids)
    shown = [r for r in recs if r.ticket_id in items]
    if wanted:
        visible = await _visible_services(
            session, sets, [r.ticket_id for r in shown], user_id=user_id
        )
        shown = [
            r
            for r in shown
            if wanted & (set(items[r.ticket_id].service_ids) | visible.get(r.ticket_id, set()))
        ]

    # the conflict window: everything the shown changes can collide with, not only the range
    starts = [r.planned_start for r in shown if r.planned_start is not None]
    ends = [r.planned_end for r in shown if r.planned_end is not None]
    wide_lo = max(min([lo, *starts]), lo - MAX_RANGE)
    wide_hi = min(max([hi, *ends]), hi + MAX_RANGE)
    pool = list(recs)
    if (wide_lo, wide_hi) != (lo, hi):
        more, more_truncated = await _scan(
            session,
            [
                ch.phase.in_(sorted(OVERLAP_PHASES)),
                *_window_cond(ch.planned_start, ch.planned_end, wide_lo, wide_hi),
            ],
            order_by_start=True,
        )
        truncated = truncated or more_truncated
        have = {r.ticket_id for r in recs}
        more = [r for r in more if r.ticket_id not in have]
        pool += more
        sets.update(await impact_sets(session, [r.ticket_id for r in more]))
    found = await occurrences(session, start=wide_lo, end=wide_hi)
    tasks_by_change = await _task_windows(session, [r.ticket_id for r in shown], user_id=user_id)

    others = sorted(
        (
            OtherChange(
                r.ticket_id,
                r.phase,
                r.planned_start,
                r.planned_end,
                sets[r.ticket_id].services,
                sets[r.ticket_id].cis,
            )
            for r in pool
            if r.phase in OVERLAP_PHASES
            and r.planned_start is not None
            and r.planned_end is not None
        ),
        key=lambda o: (o.start, o.ticket_id),
    )
    changes: list[CalendarChange] = []
    for r in shown:
        mine: ImpactSet = sets[r.ticket_id]
        window = (
            (r.planned_start, r.planned_end)
            if r.planned_start is not None
            and r.planned_end is not None
            and r.planned_end > r.planned_start
            else None
        )
        near: list[OtherChange] = []
        if window is not None:
            for o in others:
                if o.start >= window[1]:
                    break
                if o.end > window[0] and o.ticket_id != r.ticket_id:
                    near.append(o)
        severity, kinds = _flag(
            detect_conflicts(
                change_type=r.change_type,
                window=window,
                services=mine.services,
                cis=mine.cis,
                freezes=found.items,
                others=near,
                tasks=tasks_by_change.get(r.ticket_id, ()),
            )
        )
        changes.append(CalendarChange(items[r.ticket_id], severity, kinds))

    tasks: list[TaskListItem] = []
    if include_tasks:
        t = TiqoraItsmChangeTask
        stmt = select(t).where(
            *_window_cond(t.planned_start, t.planned_end, lo, hi),
            t.change_ticket_id.in_(
                select(ch.ticket_id).where(not_(ch.phase.in_(list(_OFF_CALENDAR))))
            ),
        )
        if wanted:
            stmt = stmt.where(
                t.change_ticket_id.in_(select(ch.ticket_id).where(_service_cond(sorted(wanted))))
            )
        rows = list(
            (
                await session.execute(
                    stmt.order_by(
                        case((t.planned_start.is_(None), 1), else_=0), t.planned_start, t.ticket_id
                    ).limit(LIST_SCAN_CAP + 1)
                )
            ).scalars()
        )
        if len(rows) > LIST_SCAN_CAP:
            truncated = True
        tasks = await _task_items(session, rows[:LIST_SCAN_CAP], user_id=user_id)
        if wanted:  # same visibility rule as for the changes
            keep = {r.ticket_id for r in shown}
            tasks = [x for x in tasks if x.change_ticket_id in keep]
    in_range = [f for f in found.items if f.end > lo and f.start < hi]
    return ChangeCalendar(
        lo, hi, tuple(changes), tuple(tasks), tuple(in_range), truncated, found.truncated
    )


# --- projected service availability ----------------------------------------------------------


def _union_minutes(spans: Sequence[tuple[datetime, datetime]]) -> int:
    total = timedelta()
    cur_start: datetime | None = None
    cur_end: datetime | None = None
    for s, e in sorted(spans):
        if cur_end is None or s > cur_end:
            if cur_start is not None and cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = s, e
        elif e > cur_end:
            cur_end = e
    if cur_start is not None and cur_end is not None:
        total += cur_end - cur_start
    return int(total.total_seconds() // 60)


async def _service_names(session: AsyncSession, ids: Iterable[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    stmt = text("SELECT id, name FROM service WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for part in _chunked(ids):
        out.update({int(r[0]): str(r[1]) for r in await session.execute(stmt, {"ids": part})})
    return out


async def service_availability(
    session: AsyncSession, *, user_id: int, start: datetime, end: datetime
) -> list[ServiceAvailabilityRow]:
    """Per service the planned change windows (approval, scheduled, implementing) and freeze
    windows of ``[start, end)``: the projected service availability.

    Only services that have a change window or a service-scoped freeze get a row; freezes for
    every service are added to those rows. Changes the caller cannot read are left out. At most
    :data:`PSA_SERVICE_CAP` rows, the most affected first, then ordered by service name.
    """
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    if not await _is_mode_b(session):
        return []
    _check_range(lo, hi)
    ch = TiqoraItsmChange
    recs, scan_truncated = await _scan(
        session,
        [
            ch.phase.in_(sorted(OVERLAP_PHASES)),
            *_window_cond(ch.planned_start, ch.planned_end, lo, hi),
        ],
        order_by_start=True,
    )
    facts = await _readable(session, [r.ticket_id for r in recs], user_id=user_id)
    recs = [r for r in recs if r.ticket_id in facts and r.planned_start and r.planned_end]
    sets = await impact_sets(session, [r.ticket_id for r in recs])
    extras = await _extra_services(session, [r.ticket_id for r in recs])
    # services reached only through CIs the caller cannot read stay out (like ``read_impact``)
    visible = await _visible_services(session, sets, [r.ticket_id for r in recs], user_id=user_id)
    freezes = (await occurrences(session, start=lo, end=hi)).items

    windows: dict[int, list[ServiceWindow]] = defaultdict(list)
    spans: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for r in recs:
        assert r.planned_start is not None and r.planned_end is not None  # filtered above
        direct = set(extras.get(r.ticket_id, ()))
        if facts[r.ticket_id].service_id is not None:
            direct.add(facts[r.ticket_id].service_id or 0)
        direct |= set(sets[r.ticket_id].direct_services)
        for sid in visible[r.ticket_id] | frozenset(direct):
            windows[sid].append(
                ServiceWindow(
                    "change",
                    r.ticket_id,
                    facts[r.ticket_id].tn,
                    facts[r.ticket_id].title,
                    r.phase,
                    r.change_type,
                    None,
                    sid in direct,
                    r.planned_start,
                    r.planned_end,
                )
            )
            spans[sid].append((max(r.planned_start, lo), min(r.planned_end, hi)))
    for f in freezes:
        if f.scope == "services":
            for sid in f.service_ids:
                windows[sid].append(_freeze_window(f))
    for f in freezes:
        if f.scope == "all":
            for sid in windows:
                windows[sid].append(_freeze_window(f))

    names = await _service_names(session, windows)
    rows = [
        ServiceAvailabilityRow(
            service_id=sid,
            service_name=names[sid],
            minutes_affected=_union_minutes(spans.get(sid, [])),
            change_count=sum(1 for w in ws if w.kind == "change"),
            windows=tuple(sorted(ws, key=lambda w: (w.start, w.kind, w.ref_id))),
        )
        for sid, ws in windows.items()
        if sid in names
    ]
    rows.sort(key=lambda r: (-r.minutes_affected, r.service_id))
    if scan_truncated or len(rows) > PSA_SERVICE_CAP:
        logger.warning(
            "change PSA truncated (changes cut: %s, services: %d of cap %d)",
            scan_truncated,
            len(rows),
            PSA_SERVICE_CAP,
        )
    return sorted(rows[:PSA_SERVICE_CAP], key=lambda r: (r.service_name.casefold(), r.service_id))


def _freeze_window(f: FreezeOccurrence) -> ServiceWindow:
    return ServiceWindow(
        "freeze", f.freeze_id, None, f.name, None, None, f.enforcement, None, f.start, f.end
    )


__all__ = [
    "LIST_SCAN_CAP",
    "MAX_RANGE",
    "PSA_SERVICE_CAP",
    "CalendarChange",
    "ChangeCalendar",
    "ChangeListItem",
    "ChangePage",
    "ChangeQuery",
    "ChangeRef",
    "ChangeTaskRef",
    "ServiceAvailabilityRow",
    "ServiceWindow",
    "TaskListItem",
    "active_freezes",
    "calendar",
    "change_refs_for_tickets",
    "items_for_changes",
    "list_changes",
    "my_tasks",
    "phase_counts",
    "service_availability",
    "task_refs_for_tickets",
    "upcoming_changes",
]
