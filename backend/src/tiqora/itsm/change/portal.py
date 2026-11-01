"""Change schedule for portal customers (design I17).

A customer sees a change only when it is flagged "Im Kundenportal anzeigen", sits in a phase that
is worth announcing (planned, being implemented, closed), overlaps the asked range and touches at
least one service assigned to the customer (the ticket's service, the extra services or any
service of the stored impact set). The answer carries title, window and the names of the
*customer's own* affected services only - the other services of a change are never named.

The customer's services are those of Znuny's ``TicketServiceList`` (``customer_service_map``: own
assignments, else the ``<DEFAULT>`` services). Keys are loaded per side and combined in memory
(no ``tiqora_*`` x Znuny join). Times are naive UTC like the stored columns.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, cast

from pydantic import BaseModel
from sqlalchemy import ColumnElement, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import to_utc_naive
from tiqora.config import Settings
from tiqora.itsm.change.conflicts import impact_sets
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import change_switched_off
from tiqora.itsm.change.models import TiqoraItsmChange, TiqoraItsmChangeService
from tiqora.itsm.change.read import (
    LIST_SCAN_CAP,
    MAX_RANGE,
    chunked,
    service_cond,
    window_cond,
)
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.mode import get_itsm_mode
from tiqora.process.dialog_form import customer_service_map
from tiqora.znuny.sysconfig import SysConfig

#: phases a customer is told about
PORTAL_PHASES: Final = ("scheduled", "implementing", "closed")


class PortalChangeOut(BaseModel):
    tn: str
    title: str
    change_type: Literal["standard", "normal", "emergency"]
    phase: Literal["scheduled", "implementing", "closed"]
    planned_start: datetime | None
    planned_end: datetime | None
    #: only the customer's own services among the affected ones
    services: list[str]


@dataclass(frozen=True, slots=True)
class PortalSchedule:
    items: list[PortalChangeOut]
    #: the scan cap bit; later changes of the range may be missing
    truncated: bool


async def schedule_enabled(session: AsyncSession, settings: Settings) -> bool:
    """Mode B, both module switches on and the setting ``portal_schedule_enabled``."""
    if (await get_itsm_mode(session)).mode != "tiqora":
        return False
    if await change_switched_off(session) is not None:
        return False
    return (await load_change_settings(session, settings)).portal_schedule_enabled


async def _candidate_ids(
    session: AsyncSession, where: list[ColumnElement[bool]], mine: dict[int, str]
) -> tuple[list[int], bool]:
    """Ids of the flagged changes of the range that touch one of the customer's services, the
    earliest planned start first, at most :data:`LIST_SCAN_CAP` (``True``: more exist).

    Extra and impact service rows are filtered in SQL; the ticket's own service lives in Znuny,
    so the flagged ids are loaded first and checked there with an IN list (no cross join). Every
    scan is ordered before its cap, so a cut keeps the earliest changes on both dialects, and the
    customer's service ids go in chunks (the ``<DEFAULT>`` services can be many).
    """
    base = (
        select(TiqoraItsmChange.ticket_id, TiqoraItsmChange.planned_start)
        .where(*where)
        .order_by(TiqoraItsmChange.planned_start, TiqoraItsmChange.ticket_id)
    )
    sids = sorted(mine)
    starts: dict[int, datetime | None] = {}
    truncated = False
    for sid_part in chunked(sids):
        rows = (
            await session.execute(base.where(service_cond(sid_part)).limit(LIST_SCAN_CAP + 1))
        ).all()
        truncated = truncated or len(rows) > LIST_SCAN_CAP
        starts.update((int(tid), when) for tid, when in rows[:LIST_SCAN_CAP])
    flagged_rows = (await session.execute(base.limit(LIST_SCAN_CAP + 1))).all()
    truncated = truncated or len(flagged_rows) > LIST_SCAN_CAP
    flagged = {int(tid): when for tid, when in flagged_rows[:LIST_SCAN_CAP]}
    stmt = text("SELECT id FROM ticket WHERE id IN :ids AND service_id IN :sids").bindparams(
        bindparam("ids", expanding=True), bindparam("sids", expanding=True)
    )
    for part in chunked(list(flagged)):
        for sid_part in chunked(sids):
            for (tid,) in await session.execute(stmt, {"ids": part, "sids": list(sid_part)}):
                starts[int(tid)] = flagged[int(tid)]
    ordered = sorted(starts, key=lambda t: (starts[t] or datetime.max, t))
    return ordered[:LIST_SCAN_CAP], truncated or len(ordered) > LIST_SCAN_CAP


async def schedule_for_customer(
    session: AsyncSession, *, login: str, start: datetime, end: datetime
) -> PortalSchedule:
    start, end = to_utc_naive(start), to_utc_naive(end)
    if end <= start:
        raise ChangeError("range_invalid", 422)
    if end - start > MAX_RANGE:
        raise ChangeError("range_too_large", 422)

    mine = await customer_service_map(session, login, SysConfig(session))
    if not mine:
        return PortalSchedule([], False)
    where = [
        TiqoraItsmChange.portal_visible.is_(True),
        TiqoraItsmChange.phase.in_(PORTAL_PHASES),
        *window_cond(TiqoraItsmChange.planned_start, TiqoraItsmChange.planned_end, start, end),
    ]
    ids, truncated = await _candidate_ids(session, where, mine)
    if not ids:
        return PortalSchedule([], truncated)
    rows: list[TiqoraItsmChange] = []
    for part in chunked(ids):
        rows.extend(
            (
                await session.execute(
                    select(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id.in_(part))
                )
            ).scalars()
        )
    rows.sort(key=lambda r: (r.planned_start or datetime.max, r.ticket_id))

    tickets: dict[int, tuple[str, str, int | None]] = {}
    ticket_stmt = text("SELECT id, tn, title, service_id FROM ticket WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for part in chunked(ids):
        for tid, tn, title, svc in await session.execute(ticket_stmt, {"ids": part}):
            tickets[int(tid)] = (str(tn), str(title), None if svc is None else int(svc))

    services: dict[int, set[int]] = defaultdict(set)
    for tid, (_tn, _title, svc) in tickets.items():
        if svc is not None:
            services[tid].add(svc)
    for part in chunked(ids):
        extra = await session.execute(
            select(TiqoraItsmChangeService.ticket_id, TiqoraItsmChangeService.service_id).where(
                TiqoraItsmChangeService.ticket_id.in_(part)
            )
        )
        for tid, sid in extra:
            services[int(tid)].add(int(sid))
    for tid, impact in (await impact_sets(session, ids)).items():
        services[tid] |= impact.services

    out: list[PortalChangeOut] = []
    for row in rows:
        ticket = tickets.get(row.ticket_id)
        shown = sorted(mine[s] for s in services[row.ticket_id] if s in mine)
        if ticket is None or not shown:
            continue
        out.append(
            PortalChangeOut(
                tn=ticket[0],
                title=ticket[1],
                change_type=cast(Literal["standard", "normal", "emergency"], row.change_type),
                phase=cast(Literal["scheduled", "implementing", "closed"], row.phase),
                planned_start=row.planned_start,
                planned_end=row.planned_end,
                services=shown,
            )
        )
    return PortalSchedule(out, truncated)
