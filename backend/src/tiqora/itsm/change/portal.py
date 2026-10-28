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
from typing import Final

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import to_utc_naive
from tiqora.config import Settings
from tiqora.itsm.change.conflicts import impact_sets
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import TiqoraItsmChange, TiqoraItsmChangeService
from tiqora.itsm.change.read import LIST_SCAN_CAP, MAX_RANGE, _chunked, _window_cond
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.mode import get_itsm_mode
from tiqora.process.dialog_form import customer_service_map
from tiqora.znuny.sysconfig import SysConfig

#: phases a customer is told about
PORTAL_PHASES: Final = ("scheduled", "implementing", "closed")


@dataclass(frozen=True, slots=True)
class PortalChange:
    tn: str
    title: str
    change_type: str
    phase: str
    planned_start: datetime | None
    planned_end: datetime | None
    services: tuple[str, ...]


async def schedule_enabled(session: AsyncSession, settings: Settings) -> bool:
    """Mode B and the setting ``portal_schedule_enabled``."""
    if (await get_itsm_mode(session)).mode != "tiqora":
        return False
    return (await load_change_settings(session, settings)).portal_schedule_enabled


async def schedule_for_customer(
    session: AsyncSession, *, login: str, start: datetime, end: datetime
) -> list[PortalChange]:
    start, end = to_utc_naive(start), to_utc_naive(end)
    if end <= start:
        raise ChangeError("range_invalid", 422)
    if end - start > MAX_RANGE:
        raise ChangeError("range_too_large", 422)

    mine = await customer_service_map(session, login, SysConfig(session))
    if not mine:
        return []
    stmt = (
        select(TiqoraItsmChange)
        .where(
            TiqoraItsmChange.portal_visible.is_(True),
            TiqoraItsmChange.phase.in_(PORTAL_PHASES),
            *_window_cond(TiqoraItsmChange.planned_start, TiqoraItsmChange.planned_end, start, end),
        )
        .order_by(TiqoraItsmChange.planned_start, TiqoraItsmChange.ticket_id)
        .limit(LIST_SCAN_CAP)
    )
    rows = list((await session.execute(stmt)).scalars())
    if not rows:
        return []
    ids = [r.ticket_id for r in rows]

    tickets: dict[int, tuple[str, str, int | None]] = {}
    ticket_stmt = text("SELECT id, tn, title, service_id FROM ticket WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for part in _chunked(ids):
        for tid, tn, title, svc in await session.execute(ticket_stmt, {"ids": part}):
            tickets[int(tid)] = (str(tn), str(title), None if svc is None else int(svc))

    services: dict[int, set[int]] = defaultdict(set)
    for tid, (_tn, _title, svc) in tickets.items():
        if svc is not None:
            services[tid].add(svc)
    for part in _chunked(ids):
        extra = await session.execute(
            select(TiqoraItsmChangeService.ticket_id, TiqoraItsmChangeService.service_id).where(
                TiqoraItsmChangeService.ticket_id.in_(part)
            )
        )
        for tid, sid in extra:
            services[int(tid)].add(int(sid))
    for tid, impact in (await impact_sets(session, ids)).items():
        services[tid] |= impact.services

    out: list[PortalChange] = []
    for row in rows:
        ticket = tickets.get(row.ticket_id)
        shown = sorted(mine[s] for s in services[row.ticket_id] if s in mine)
        if ticket is None or not shown:
            continue
        out.append(
            PortalChange(
                tn=ticket[0],
                title=ticket[1],
                change_type=row.change_type,
                phase=row.phase,
                planned_start=row.planned_start,
                planned_end=row.planned_end,
                services=tuple(shown),
            )
        )
    return out
