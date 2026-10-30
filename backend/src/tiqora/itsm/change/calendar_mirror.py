"""Optional Znuny calendar appointment per scheduled change (design I15).

With ``calendar_id`` set, a change in ``scheduled`` or ``implementing`` that has a planned window
owns one appointment in that Znuny calendar, written through ``CalendarService`` exactly like
Znuny's ``AppointmentCreate`` (no permission check, the same row fields). Any other phase, or a
window that went away, removes it. A changed window, title or calendar recreates it; the id is
kept in ``tiqora_itsm_change.calendar_appointment_id``.

The mirror is a courtesy: a missing calendar or appointment is logged (``change_calendar_missing``)
and never fails the transition or edit that triggered it. It runs in its own savepoint, so a
failed write leaves the caller's transaction intact. Services never commit.

The appointment title is a copy of the ticket title, so the GDPR erasure keeps it in line
(:func:`resync_tickets` after the anonymise title rewrite and after a rollback,
:func:`delete_appointments` before a hard delete of the tickets).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Collection, Sequence
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.schemas import AppointmentIn
from tiqora.calendar.service import AppointmentNotFound, CalendarNotFound, CalendarService
from tiqora.config import Settings, get_settings
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.ticket_access import CHUNK
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
) -> bool:
    """Bring the change's appointment in line with its phase and window (no-op when off).

    ``True`` when the mirror was written; a failed write is logged and answers ``False``."""
    if cfg.calendar_id == 0:
        return False
    row = await session.get(TiqoraItsmChange, ticket_id)
    if row is None:
        return False
    if not _wanted(row) and row.calendar_appointment_id is None:
        return False
    try:
        # checked before the savepoint: rolling one back expires every object the caller holds
        if _wanted(row) and await session.get(Calendar, cfg.calendar_id) is None:
            raise CalendarNotFound(f"calendar {cfg.calendar_id} not found")
        async with session.begin_nested():
            await _sync(session, cfg, row, user_id=user_id, base_url=base_url)
        return True
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
        return False


async def resync_after_title_edit(session: AsyncSession, *, ticket_id: int, user_id: int) -> None:
    """The mirrored appointment carries the ticket title: called by the generic title edit.

    Does nothing for a ticket without a mirrored appointment (one lookup). Everything runs in its
    own savepoint and any error is logged: a mirror error never fails the title write.
    """
    try:
        async with session.begin_nested():
            mirrored = await session.execute(
                select(TiqoraItsmChange.calendar_appointment_id).where(
                    TiqoraItsmChange.ticket_id == ticket_id
                )
            )
            first = mirrored.first()
            if first is None or first[0] is None:
                return
            settings = get_settings()
            cfg = await load_change_settings(session, settings)
            ticket = await session.get(Ticket, ticket_id)
            if ticket is not None:
                await session.refresh(ticket)  # the title was written with a raw UPDATE
            await sync_appointment(
                session,
                cfg,
                ticket_id=ticket_id,
                user_id=user_id,
                base_url=settings.public_base_url,
            )
    except Exception:  # noqa: BLE001 - a mirror error never fails the caller
        logger.exception("change_calendar_mirror_failed", extra={"ticket_id": ticket_id})


# --- GDPR erasure ---------------------------------------------------------------------------


async def mirrored_appointments(session: AsyncSession, ticket_ids: Sequence[int]) -> dict[int, int]:
    """``ticket_id -> calendar_appointment_id`` of the given tickets whose change owns an
    appointment (IN lists of at most ``CHUNK`` ids)."""
    out: dict[int, int] = {}
    ids = sorted({int(t) for t in ticket_ids})
    for start in range(0, len(ids), CHUNK):
        rows = await session.execute(
            select(TiqoraItsmChange.ticket_id, TiqoraItsmChange.calendar_appointment_id).where(
                TiqoraItsmChange.ticket_id.in_(ids[start : start + CHUNK]),
                TiqoraItsmChange.calendar_appointment_id.is_not(None),
            )
        )
        out.update({int(t): int(a) for t, a in rows})
    return out


async def delete_appointments(
    session: AsyncSession, appointment_ids: Collection[int], *, user_id: int
) -> int:
    """Delete the appointments of change tickets that are being hard-deleted (the mirror's
    allow-listed write; the caller deletes the change rows). One already gone is skipped."""
    service = CalendarService(session)
    removed = 0
    for appointment_id in sorted(set(appointment_ids)):
        try:
            await service.delete_appointment_unchecked(user_id, int(appointment_id))
        except AppointmentNotFound:
            continue
        removed += 1
    return removed


async def resync_tickets(
    session: AsyncSession, settings: Settings, ticket_ids: Collection[int], *, user_id: int
) -> int:
    """Bring the appointments of these change tickets in line after their rows were written
    outside the change module (the anonymised title, a rollback's restored rows): recreated with
    the current title, or created again for a restored change in a mirrored phase. With the
    mirror switched off since, a left-over appointment goes (it carries the old title). Errors
    are logged per change, like every mirror write. Returns the number of successful writes."""
    if not ticket_ids:
        return 0
    written = 0
    cfg = await load_change_settings(session, settings)
    for ticket_id in sorted(set(ticket_ids)):
        row = await session.get(TiqoraItsmChange, ticket_id)
        if row is None:
            continue
        await session.refresh(row)  # restored / rewritten with raw statements
        ticket = await session.get(Ticket, ticket_id)
        if ticket is not None:
            await session.refresh(ticket)
        if cfg.calendar_id != 0:
            written += await sync_appointment(
                session,
                cfg,
                ticket_id=ticket_id,
                user_id=user_id,
                base_url=settings.public_base_url,
            )
            continue
        if row.calendar_appointment_id is None:
            continue
        try:
            async with session.begin_nested():
                await _remove(CalendarService(session), row, user_id)
                await session.flush()
            written += 1
        except Exception:  # noqa: BLE001 - a mirror error never fails the caller
            logger.exception("change_calendar_mirror_failed", extra={"ticket_id": ticket_id})
            with contextlib.suppress(Exception):
                await session.refresh(row)
    return written


__all__ = [
    "MIRRORED_PHASES",
    "delete_appointments",
    "mirrored_appointments",
    "resync_after_title_edit",
    "resync_tickets",
    "sync_appointment",
]
