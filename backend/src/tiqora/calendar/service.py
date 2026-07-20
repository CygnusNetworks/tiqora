"""CalendarService: permission-filtered calendars, appointment CRUD,
recurrence expansion, ICS export, and ticket linking.

Write methods do not commit — callers wrap calls in ``async with
session.begin():`` (same convention as ``ticket_write_service``/``kb.service``).

Cache invalidation: appointments do not participate in the ticket search
index or the ``tiqora_event_outbox`` drain — calendars are a separate Znuny
subsystem with no ticket-cache interaction, so no invalidation hook is wired
here (see docs/architecture.md Calendar section).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import (
    expand_occurrences,
    parse_exclude_list,
    serialize_exclude_list,
    to_utc_naive,
)
from tiqora.calendar.schemas import AppointmentIn, AppointmentUpdateIn, OccurrenceOut
from tiqora.db.legacy.calendar import Calendar, CalendarAppointment, CalendarAppointmentTicket
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.legacy.user import Users
from tiqora.permissions.engine import PermissionEngine


class CalendarNotFound(Exception):
    pass


class AppointmentNotFound(Exception):
    pass


class CalendarForbidden(Exception):
    """User lacks the required permission on the calendar's group."""


class CalendarService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)

    # ── Calendars ────────────────────────────────────────────────────────

    async def list_calendars(self, user_id: int, perm: str = "ro") -> list[Calendar]:
        group_ids = await self._perms.groups_for_permission(user_id, perm)
        if not group_ids:
            return []
        result = await self._session.execute(
            select(Calendar)
            .where(Calendar.group_id.in_(group_ids), Calendar.valid_id == 1)
            .order_by(Calendar.name)
        )
        return list(result.scalars().all())

    async def get_calendar(self, calendar_id: int, user_id: int, perm: str = "ro") -> Calendar:
        row = await self._calendar_or_404(calendar_id)
        await self._require_group_permission(user_id, row.group_id, perm)
        return row

    async def _require_group_permission(self, user_id: int, group_id: int, perm: str) -> None:
        group_ids = await self._perms.groups_for_permission(user_id, perm)
        if group_id not in group_ids:
            raise CalendarForbidden(f"user {user_id} lacks {perm!r} on group {group_id}")

    async def _calendar_or_404(self, calendar_id: int) -> Calendar:
        row = await self._session.get(Calendar, calendar_id)
        if row is None:
            raise CalendarNotFound(f"calendar {calendar_id} not found")
        return row

    async def _appointment_or_404(self, appointment_id: int) -> CalendarAppointment:
        row = await self._session.get(CalendarAppointment, appointment_id)
        if row is None:
            raise AppointmentNotFound(f"appointment {appointment_id} not found")
        return row

    # ── Appointments: CRUD ───────────────────────────────────────────────

    async def create_appointment(self, user_id: int, data: AppointmentIn) -> CalendarAppointment:
        cal = await self._calendar_or_404(data.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, "rw")

        now = datetime.now(UTC).replace(tzinfo=None)
        row = CalendarAppointment(
            calendar_id=cal.id,
            unique_id=f"{now.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(6)}@tiqora",
            title=data.title,
            description=data.description,
            location=data.location,
            start_time=to_utc_naive(data.start_time),
            end_time=to_utc_naive(data.end_time),
            all_day=1 if data.all_day else 0,
            team_id=data.team_id,
            resource_id=data.resource_id,
            create_time=now,
            create_by=user_id,
            change_time=now,
            change_by=user_id,
        )
        if data.recurrence:
            row.recurring = 1
            row.recur_type = data.recurrence.type
            row.recur_interval = data.recurrence.interval
            row.recur_count = data.recurrence.count
            row.recur_until = to_utc_naive(data.recurrence.until) if data.recurrence.until else None
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_appointment(
        self, appointment_id: int, user_id: int, perm: str = "ro"
    ) -> CalendarAppointment:
        row = await self._appointment_or_404(appointment_id)
        cal = await self._calendar_or_404(row.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, perm)
        return row

    async def update_appointment(
        self, user_id: int, appointment_id: int, data: AppointmentUpdateIn
    ) -> CalendarAppointment:
        row = await self._appointment_or_404(appointment_id)
        cal = await self._calendar_or_404(row.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, "rw")

        if data.title is not None:
            row.title = data.title
        if data.description is not None:
            row.description = data.description
        if data.location is not None:
            row.location = data.location
        if data.start_time is not None:
            row.start_time = to_utc_naive(data.start_time)
        if data.end_time is not None:
            row.end_time = to_utc_naive(data.end_time)
        if data.all_day is not None:
            row.all_day = 1 if data.all_day else 0
        if data.team_id is not None:
            row.team_id = data.team_id
        if data.resource_id is not None:
            row.resource_id = data.resource_id
        if data.clear_recurrence:
            row.recurring = None
            row.recur_type = None
            row.recur_interval = None
            row.recur_count = None
            row.recur_until = None
            row.recur_exclude = None
        elif data.recurrence is not None:
            row.recurring = 1
            row.recur_type = data.recurrence.type
            row.recur_interval = data.recurrence.interval
            row.recur_count = data.recurrence.count
            row.recur_until = to_utc_naive(data.recurrence.until) if data.recurrence.until else None

        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        row.change_by = user_id
        await self._session.flush()
        return row

    async def delete_appointment(
        self, user_id: int, appointment_id: int, *, occurrence: datetime | None = None
    ) -> None:
        """Delete an appointment. If ``occurrence`` is given for a recurring
        series, only that occurrence is excluded (added to ``recur_exclude``)
        instead of deleting the whole series.
        """
        row = await self._appointment_or_404(appointment_id)
        cal = await self._calendar_or_404(row.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, "rw")

        if occurrence is not None and row.recur_type:
            excluded = parse_exclude_list(row.recur_exclude)
            occ_naive = to_utc_naive(occurrence)
            if occ_naive not in excluded:
                excluded.append(occ_naive)
            row.recur_exclude = serialize_exclude_list(excluded)
            row.change_time = datetime.now(UTC).replace(tzinfo=None)
            row.change_by = user_id
            await self._session.flush()
            return

        await self._session.execute(
            delete(CalendarAppointmentTicket).where(
                CalendarAppointmentTicket.appointment_id == appointment_id
            )
        )
        await self._session.delete(row)
        await self._session.flush()

    # ── Appointments: range query (occurrence expansion) ────────────────

    async def list_occurrences(
        self,
        user_id: int,
        *,
        range_start: datetime,
        range_end: datetime,
        calendar_ids: list[int] | None = None,
    ) -> list[OccurrenceOut]:
        allowed = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed:
            return []

        stmt = select(Calendar).where(Calendar.group_id.in_(allowed), Calendar.valid_id == 1)
        if calendar_ids:
            stmt = stmt.where(Calendar.id.in_(calendar_ids))
        cal_result = await self._session.execute(stmt)
        cals = {c.id: c for c in cal_result.scalars().all()}
        if not cals:
            return []

        appt_result = await self._session.execute(
            select(CalendarAppointment).where(
                CalendarAppointment.calendar_id.in_(cals.keys()),
                CalendarAppointment.start_time < to_utc_naive(range_end),
            )
        )
        out: list[OccurrenceOut] = []
        rs, re = to_utc_naive(range_start), to_utc_naive(range_end)
        for appt in appt_result.scalars().all():
            exclude = parse_exclude_list(appt.recur_exclude)
            for occ in expand_occurrences(
                start_time=appt.start_time,
                end_time=appt.end_time,
                recur_type=appt.recur_type,
                recur_interval=appt.recur_interval,
                recur_count=appt.recur_count,
                recur_until=appt.recur_until,
                exclude=exclude,
                range_start=rs,
                range_end=re,
            ):
                out.append(
                    OccurrenceOut(
                        appointment_id=appt.id,
                        calendar_id=appt.calendar_id,
                        title=appt.title,
                        description=appt.description,
                        location=appt.location,
                        start_time=occ.start,
                        end_time=occ.end,
                        all_day=bool(appt.all_day),
                        is_recurring=occ.is_recurring,
                    )
                )
        out.sort(key=lambda o: o.start_time)
        return out

    # ── Ticket linking ───────────────────────────────────────────────────

    async def link_ticket(
        self, user_id: int, appointment_id: int, ticket_id: int
    ) -> CalendarAppointmentTicket:
        appt = await self._appointment_or_404(appointment_id)
        cal = await self._calendar_or_404(appt.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, "rw")

        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is None:
            raise AppointmentNotFound(f"ticket {ticket_id} not found")

        link = CalendarAppointmentTicket(
            calendar_id=appt.calendar_id,
            ticket_id=ticket_id,
            rule_id="manual",
            appointment_id=appointment_id,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def unlink_ticket(self, user_id: int, appointment_id: int, ticket_id: int) -> None:
        appt = await self._appointment_or_404(appointment_id)
        cal = await self._calendar_or_404(appt.calendar_id)
        await self._require_group_permission(user_id, cal.group_id, "rw")

        await self._session.execute(
            delete(CalendarAppointmentTicket).where(
                CalendarAppointmentTicket.appointment_id == appointment_id,
                CalendarAppointmentTicket.ticket_id == ticket_id,
            )
        )
        await self._session.flush()

    async def list_ticket_links(self, appointment_id: int) -> list[CalendarAppointmentTicket]:
        result = await self._session.execute(
            select(CalendarAppointmentTicket).where(
                CalendarAppointmentTicket.appointment_id == appointment_id
            )
        )
        return list(result.scalars().all())

    # ── ICS export / subscription feed ──────────────────────────────────

    async def export_appointments(
        self, calendar_id: int
    ) -> tuple[Calendar, list[CalendarAppointment]]:
        cal = await self._calendar_or_404(calendar_id)
        result = await self._session.execute(
            select(CalendarAppointment).where(CalendarAppointment.calendar_id == calendar_id)
        )
        return cal, list(result.scalars().all())

    async def feed_token(self, calendar_id: int, user_id: int) -> str:
        """Znuny-compatible access token: md5(f"{login}-{salt_string}").

        Matches ``Kernel::System::Calendar::GetAccessToken`` so a token minted
        by Tiqora also authenticates against a running Znuny's public feed
        endpoint (and vice versa).
        """
        cal = await self._calendar_or_404(calendar_id)
        user = await self._session.get(Users, user_id)
        if user is None:
            raise CalendarNotFound(f"user {user_id} not found")
        digest = hashlib.md5(f"{user.login}-{cal.salt_string}".encode()).hexdigest()  # noqa: S324
        return digest

    async def verify_feed_token(self, calendar_id: int, login: str, token: str) -> bool:
        cal = await self._session.get(Calendar, calendar_id)
        if cal is None:
            return False
        expected = hashlib.md5(f"{login}-{cal.salt_string}".encode()).hexdigest()  # noqa: S324
        return secrets.compare_digest(expected, token)
