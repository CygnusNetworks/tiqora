"""Change conflicts (design I10): freeze windows, other changes and tasks, found from stored data.

:func:`detect_conflicts` is pure (no database, network or file system). :func:`load_conflicts`
feeds it from the stored impact set, the freeze occurrences and a capped list of overlapping
changes; it never reads another ticket's content. Two situations are reported as findings of their
own instead of passing silently: the freeze occurrences were cut off (``freeze_truncated``) or
more overlapping changes exist than are examined (``overlap_truncated``).

``user_id=None`` is the system view (guards and the risk score); with a user, an overlapping
change on a queue the user cannot read becomes ``hidden`` (no reference, window kept, ruling F27)
and a task ticket the user cannot read is left out. :func:`read_conflicts` is the API read: it
also drops service and CI ids the caller cannot see (counts stay).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.impact import services_reached_from
from tiqora.config import Settings
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.ticket_access import CHUNK, readable_tickets
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import FreezeOccurrence, occurrences
from tiqora.itsm.change.gate import require_change_mode
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeTask,
)
from tiqora.itsm.change.service import _row, _ticket
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.providers import get_ci_provider
from tiqora.znuny.sysconfig import SysConfig

#: other changes examined for an overlap (the answer says when more exist)
CONFLICT_CANDIDATE_CAP: Final = 500
#: phases in which another change can still collide with this one
OVERLAP_PHASES: Final = frozenset({"approval", "scheduled", "implementing"})

ConflictKind = Literal[
    "freeze", "change_overlap", "task_outside", "no_window", "freeze_truncated", "overlap_truncated"
]
Severity = Literal["info", "warn", "block"]


@dataclass(frozen=True, slots=True)
class Conflict:
    kind: ConflictKind
    severity: Severity
    #: ``freeze:<id>`` | ``change:<ticket_id>`` | ``task:<ticket_id>``; ``None`` when hidden
    ref: str | None
    start: datetime | None
    end: datetime | None
    shared_services: tuple[int, ...] = ()
    shared_cis: tuple[int, ...] = ()
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class OtherChange:
    ticket_id: int
    phase: str
    start: datetime
    end: datetime
    services: frozenset[int]
    cis: frozenset[int]


@dataclass(frozen=True, slots=True)
class TaskWindow:
    ticket_id: int
    start: datetime | None
    end: datetime | None


def _overlap(a: tuple[datetime, datetime], b_start: datetime, b_end: datetime) -> bool:
    """Half-open spans: a window that ends where the next one starts does not overlap."""
    return a[0] < b_end and b_start < a[1]


def detect_conflicts(
    *,
    change_type: str,
    window: tuple[datetime, datetime] | None,
    services: frozenset[int],
    cis: frozenset[int],
    freezes: Sequence[FreezeOccurrence],
    others: Sequence[OtherChange],
    tasks: Sequence[TaskWindow],
) -> list[Conflict]:
    """Pure: freeze overlaps, overlapping changes sharing a CI or service, tasks off the window."""
    if window is None or window[1] <= window[0]:
        return [Conflict("no_window", "info", None, None, None)]
    out: list[Conflict] = []
    for freeze in sorted(freezes, key=lambda f: (f.start, f.freeze_id)):
        if not _overlap(window, freeze.start, freeze.end):
            continue
        shared: tuple[int, ...] = ()
        if freeze.scope == "services":
            shared = tuple(sorted(services & freeze.service_ids))
            if not shared:
                continue
        severity: Severity = "block" if freeze.enforcement == "block" else "warn"
        if severity == "block" and change_type == "emergency" and freeze.allow_emergency:
            severity = "warn"
        out.append(
            Conflict(
                "freeze",
                severity,
                f"freeze:{freeze.freeze_id}",
                freeze.start,
                freeze.end,
                shared_services=shared,
            )
        )
    for other in sorted(others, key=lambda o: (o.start, o.ticket_id)):
        if other.phase not in OVERLAP_PHASES or not _overlap(window, other.start, other.end):
            continue
        shared_services = tuple(sorted(services & other.services))
        shared_cis = tuple(sorted(cis & other.cis))
        if not shared_services and not shared_cis:
            continue
        out.append(
            Conflict(
                "change_overlap",
                "warn",
                f"change:{other.ticket_id}",
                other.start,
                other.end,
                shared_services,
                shared_cis,
            )
        )
    for task in sorted(tasks, key=lambda t: t.ticket_id):
        times = [t for t in (task.start, task.end) if t is not None]
        if times and any(not window[0] <= t <= window[1] for t in times):
            out.append(
                Conflict("task_outside", "warn", f"task:{task.ticket_id}", task.start, task.end)
            )
    return out


# --- loading ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ImpactSet:
    services: frozenset[int]
    cis: frozenset[int]
    direct_services: frozenset[int]


async def _impact_sets(session: AsyncSession, ticket_ids: Sequence[int]) -> dict[int, _ImpactSet]:
    services: dict[int, set[int]] = {t: set() for t in ticket_ids}
    cis: dict[int, set[int]] = {t: set() for t in ticket_ids}
    direct: dict[int, set[int]] = {t: set() for t in ticket_ids}
    ids = list(ticket_ids)
    for start in range(0, len(ids), CHUNK):
        rows = await session.execute(
            select(
                TiqoraItsmChangeImpact.ticket_id,
                TiqoraItsmChangeImpact.kind,
                TiqoraItsmChangeImpact.ref_key,
                TiqoraItsmChangeImpact.direct,
            ).where(TiqoraItsmChangeImpact.ticket_id.in_(ids[start : start + CHUNK]))
        )
        for ticket_id, kind, key, is_direct in rows:
            if not str(key).isdigit():
                continue
            if kind == "service":
                services[int(ticket_id)].add(int(key))
                if is_direct:
                    direct[int(ticket_id)].add(int(key))
            elif kind == "ci":
                cis[int(ticket_id)].add(int(key))
    return {
        t: _ImpactSet(frozenset(services[t]), frozenset(cis[t]), frozenset(direct[t])) for t in ids
    }


def _ref_id(ref: str | None, prefix: str) -> int | None:
    if ref is None or not ref.startswith(prefix + ":"):
        return None
    return int(ref.split(":", 1)[1])


async def _filter_readable(
    session: AsyncSession, conflicts: list[Conflict], *, user_id: int
) -> list[Conflict]:
    wanted = [
        i
        for c in conflicts
        for i in (_ref_id(c.ref, "change"), _ref_id(c.ref, "task"))
        if i is not None
    ]
    readable = await readable_tickets(session, wanted, user_id=user_id)
    out: list[Conflict] = []
    for c in conflicts:
        change_id = _ref_id(c.ref, "change")
        task_id = _ref_id(c.ref, "task")
        if change_id is not None and change_id not in readable:
            out.append(replace(c, ref=None, hidden=True))
        elif task_id is not None and task_id not in readable:
            continue  # an unreadable linked ticket is omitted, not announced
        else:
            out.append(c)
    return out


async def load_conflicts(
    session: AsyncSession, *, ticket_id: int, user_id: int | None = None
) -> list[Conflict]:
    """Conflicts of one change from stored data (reads only, bounded).

    ``user_id=None``: everything, for guards and risk. With a user: see the module docstring.
    """
    row = await session.get(TiqoraItsmChange, ticket_id)
    if row is None:
        raise ChangeError("change_not_found", 404)
    window = None
    start, end = row.planned_start, row.planned_end
    if start is not None and end is not None and end > start:
        window = (start, end)
    mine = (await _impact_sets(session, [ticket_id]))[ticket_id]

    freezes: list[FreezeOccurrence] = []
    freeze_truncated = False
    others: list[OtherChange] = []
    overlap_truncated = False
    tasks: list[TaskWindow] = []
    if window is not None:
        found = await occurrences(session, start=window[0], end=window[1])
        freezes, freeze_truncated = found.items, found.truncated
        if mine.services or mine.cis:
            candidates = (
                await session.execute(
                    select(
                        TiqoraItsmChange.ticket_id,
                        TiqoraItsmChange.phase,
                        TiqoraItsmChange.planned_start,
                        TiqoraItsmChange.planned_end,
                    )
                    .where(
                        TiqoraItsmChange.ticket_id != ticket_id,
                        TiqoraItsmChange.phase.in_(sorted(OVERLAP_PHASES)),
                        TiqoraItsmChange.planned_start < window[1],
                        TiqoraItsmChange.planned_end > window[0],
                    )
                    .order_by(TiqoraItsmChange.ticket_id)
                    .limit(CONFLICT_CANDIDATE_CAP + 1)
                )
            ).all()
            overlap_truncated = len(candidates) > CONFLICT_CANDIDATE_CAP
            candidates = candidates[:CONFLICT_CANDIDATE_CAP]
            sets = await _impact_sets(session, [int(c[0]) for c in candidates])
            others = [
                OtherChange(
                    int(tid), str(phase), start, end, sets[int(tid)].services, sets[int(tid)].cis
                )
                for tid, phase, start, end in candidates
                if start is not None and end is not None
            ]
        task_rows = await session.execute(
            select(
                TiqoraItsmChangeTask.ticket_id,
                TiqoraItsmChangeTask.planned_start,
                TiqoraItsmChangeTask.planned_end,
            ).where(TiqoraItsmChangeTask.change_ticket_id == ticket_id)
        )
        tasks = [TaskWindow(int(t), s, e) for t, s, e in task_rows]

    conflicts = detect_conflicts(
        change_type=row.change_type,
        window=window,
        services=mine.services,
        cis=mine.cis,
        freezes=freezes,
        others=others,
        tasks=tasks,
    )
    if user_id is not None:
        conflicts = await _filter_readable(session, conflicts, user_id=user_id)
    if freeze_truncated:
        conflicts.append(Conflict("freeze_truncated", "warn", None, *(window or (None, None))))
    if overlap_truncated:
        conflicts.append(Conflict("overlap_truncated", "warn", None, *(window or (None, None))))
    return conflicts


# --- API read --------------------------------------------------------------------------------


class ConflictOut(BaseModel):
    kind: ConflictKind
    severity: Severity
    ref: str | None
    hidden: bool
    start: UtcDateTime | None
    end: UtcDateTime | None
    #: only ids the caller may see; the counts cover all shared ones (a hidden entry has only those)
    shared_services: list[int]
    shared_cis: list[int]
    shared_service_count: int
    shared_ci_count: int


async def read_conflicts(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> list[ConflictOut]:
    """Conflicts as the caller may see them (404 without ``ro`` on the change ticket)."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    await _row(session, ticket_id)
    conflicts = await load_conflicts(session, ticket_id=ticket_id, user_id=user_id)

    mine = (await _impact_sets(session, [ticket_id]))[ticket_id]
    shared_cis = {c for item in conflicts if not item.hidden for c in item.shared_cis}
    readable_cis: set[int] = set()
    if shared_cis or mine.cis:
        provider = get_ci_provider("tiqora", sysconfig)
        if provider is None:
            raise ChangeError("cmdb_unavailable", 409)
        summaries = await provider.get_many(
            session, [CiRef("tiqora", i) for i in sorted(mine.cis)], user_id=user_id
        )
        readable_cis = {ref.id for ref in summaries}
    # services reached only through CIs the caller cannot read stay unnamed (like read_impact)
    visible_services = set(mine.direct_services)
    if readable_cis:
        visible_services |= await services_reached_from(session, sorted(readable_cis))
    return [
        ConflictOut(
            kind=c.kind,
            severity=c.severity,
            ref=c.ref,
            hidden=c.hidden,
            start=c.start,
            end=c.end,
            shared_services=[]
            if c.hidden
            else [s for s in c.shared_services if s in visible_services],
            shared_cis=[] if c.hidden else [i for i in c.shared_cis if i in readable_cis],
            shared_service_count=len(c.shared_services),
            shared_ci_count=len(c.shared_cis),
        )
        for c in conflicts
    ]


__all__ = [
    "CONFLICT_CANDIDATE_CAP",
    "OVERLAP_PHASES",
    "Conflict",
    "ConflictKind",
    "ConflictOut",
    "OtherChange",
    "TaskWindow",
    "detect_conflicts",
    "load_conflicts",
    "read_conflicts",
]
