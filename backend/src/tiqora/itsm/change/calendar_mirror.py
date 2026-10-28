"""Optional Znuny calendar appointment per scheduled change (design I15).

With ``calendar_id`` set, a change in ``scheduled`` or ``implementing`` that has a planned window
owns one appointment in that Znuny calendar, written through ``CalendarService`` exactly like
Znuny's ``AppointmentCreate`` (no permission check, the same row fields). Any other phase, or a
window that went away, removes it. A changed window, title or calendar recreates it; the id is
kept in ``tiqora_itsm_change.calendar_appointment_id``.

The mirror is a courtesy: a missing calendar or appointment is logged (``change_calendar_missing``)
and never fails the transition or edit that triggered it. It runs in its own savepoint, so a
failed write leaves the caller's transaction intact. Services never commit.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.schemas import AppointmentIn
from tiqora.calendar.service import AppointmentNotFound, CalendarNotFound, CalendarService
from tiqora.config import get_settings
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment
from tiqora.db.legacy.ticket import Ticket
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.settings import ItsmChangeSettings, load_change_settings

logger = logging.getLogger(__name__)

#: phases that own an appointment
MIRRORED_PHASES: Final = ("scheduled", "implementing")
_MAX_TITLE: Final = 255


def _wanted(row: TiqoraItsmChange) -> bool:
    start, end = row.planned_start, row.planned_end
    return row.phase in MIRRORED_PHASES and start is not None and end is not None and end > start


async def _remove(service: CalendarService, row: TiqoraItsmChange, user_id: int) -> None:
    appointment_id = row.calendar_appointment_id
    row.calendar_appointment_id = None
    if appointment_id is None:
        return
    # already gone in Znuny: nothing left to remove
    with contextlib.suppress(AppointmentNotFound):
        await service.delete_appointment_unchecked(user_id, int(appointment_id))


async def _sync(
    session: AsyncSession,
    cfg: ItsmChangeSettings,
    row: TiqoraItsmChange,
    *,
    user_id: int,
    base_url: str,
) -> None:
    service = CalendarService(session)
    if not _wanted(row):
        await _remove(service, row, user_id)
        await session.flush()
        return
    assert row.planned_start is not None and row.planned_end is not None  # noqa: S101
    ticket = await session.get(Ticket, row.ticket_id)
    if ticket is None:
        await _remove(service, row, user_id)
        await session.flush()
        return
    title = f"Change {ticket.tn}: {ticket.title or ''}"[:_MAX_TITLE]
    link = f"{base_url.rstrip('/')}/tickets/{row.ticket_id}" if base_url.strip() else None
    description = f"Tiqora: {link}" if link else f"Tiqora ticket {ticket.tn}"
    current = (
        await session.get(CalendarAppointment, int(row.calendar_appointment_id))
        if row.calendar_appointment_id is not None
        else None
    )
    if (
        current is not None
        and current.calendar_id == cfg.calendar_id
        and current.title == title
        and current.start_time == row.planned_start
        and current.end_time == row.planned_end
    ):
        return
    await _remove(service, row, user_id)
    created = await service.create_appointment_unchecked(
        user_id,
        AppointmentIn(
            calendar_id=cfg.calendar_id,
            title=title,
            description=description,
            start_time=row.planned_start,
            end_time=row.planned_end,
        ),
    )
    row.calendar_appointment_id = created.id
    await session.flush()


async def sync_appointment(
    session: AsyncSession,
    cfg: ItsmChangeSettings,
    *,
    ticket_id: int,
    user_id: int,
    base_url: str,
) -> None:
    """Bring the change's appointment in line with its phase and window (no-op when off)."""
    if cfg.calendar_id == 0:
        return
    row = await session.get(TiqoraItsmChange, ticket_id)
    if row is None:
        return
    if not _wanted(row) and row.calendar_appointment_id is None:
        return
    try:
        # checked before the savepoint: rolling one back expires every object the caller holds
        if _wanted(row) and await session.get(Calendar, cfg.calendar_id) is None:
            raise CalendarNotFound(f"calendar {cfg.calendar_id} not found")
        async with session.begin_nested():
            await _sync(session, cfg, row, user_id=user_id, base_url=base_url)
    except Exception as exc:  # noqa: BLE001 - a mirror error never fails the caller
        if isinstance(exc, CalendarNotFound | AppointmentNotFound):
            logger.warning(
                "change_calendar_missing",
                extra={"ticket_id": ticket_id, "calendar_id": cfg.calendar_id},
            )
        else:
            logger.exception("change_calendar_mirror_failed", extra={"ticket_id": ticket_id})
        # the savepoint rollback expired the row (and undid the id change): reload it
        with contextlib.suppress(Exception):
            await session.refresh(row)


async def resync_after_title_edit(session: AsyncSession, *, ticket_id: int, user_id: int) -> None:
    """The mirrored appointment carries the ticket title: called by the generic title edit.

    Does nothing for a ticket without a mirrored appointment (one lookup); never fails the caller.
    """
    try:
        async with session.begin_nested():
            mirrored = await session.execute(
                select(TiqoraItsmChange.calendar_appointment_id).where(
                    TiqoraItsmChange.ticket_id == ticket_id
                )
            )
            first = mirrored.first()
    except ProgrammingError:  # a bare Znuny schema has no change table
        return
    if first is None or first[0] is None:
        return
    try:
        settings = get_settings()
        cfg = await load_change_settings(session, settings)
        ticket = await session.get(Ticket, ticket_id)
        if ticket is not None:
            await session.refresh(ticket)  # the title was written with a raw UPDATE
    except Exception:  # noqa: BLE001 - a mirror error never fails the caller
        logger.exception("change_calendar_mirror_failed", extra={"ticket_id": ticket_id})
        return
    await sync_appointment(
        session, cfg, ticket_id=ticket_id, user_id=user_id, base_url=settings.public_base_url
    )


__all__ = ["MIRRORED_PHASES", "resync_after_title_edit", "sync_appointment"]
