"""Calendar/appointment REST API.

The token-based ``.../feed.ics`` route is intentionally *not* gated behind
``CurrentUser`` — it is a read-only iCalendar subscription URL meant to be
pasted into an external calendar client (Znuny-compatible: same
``login``/``md5(login-salt_string)`` token scheme as
``Kernel::System::Calendar::GetAccessToken``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.calendar.ics import build_ical
from tiqora.calendar.schemas import (
    AppointmentIn,
    AppointmentOut,
    AppointmentUpdateIn,
    CalendarOut,
    OccurrenceOut,
    TicketLinkOut,
)
from tiqora.calendar.service import (
    AppointmentNotFound,
    CalendarForbidden,
    CalendarNotFound,
    CalendarService,
)
from tiqora.db.legacy.calendar import CalendarAppointment

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, (CalendarNotFound, AppointmentNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, CalendarForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


def _appointment_out(row: CalendarAppointment) -> AppointmentOut:
    return AppointmentOut(
        id=row.id,
        parent_id=row.parent_id,
        calendar_id=row.calendar_id,
        unique_id=row.unique_id,
        title=row.title,
        description=row.description,
        location=row.location,
        start_time=row.start_time,
        end_time=row.end_time,
        all_day=bool(row.all_day),
        team_id=row.team_id,
        resource_id=row.resource_id,
        recurring=bool(row.recurring),
        recur_type=row.recur_type,
        recur_interval=row.recur_interval,
        recur_count=row.recur_count,
        recur_until=row.recur_until,
        create_time=row.create_time,
        change_time=row.change_time,
    )


# ── Calendars ────────────────────────────────────────────────────────────


@router.get("/calendars", response_model=list[CalendarOut])
async def list_calendars(user: CurrentUser, session: DbSession) -> list[CalendarOut]:
    svc = CalendarService(session)
    rows = await svc.list_calendars(user.id)
    return [CalendarOut.model_validate(r) for r in rows]


@router.get("/calendars/{calendar_id}/export.ics")
async def export_calendar_ics(calendar_id: int, user: CurrentUser, session: DbSession) -> Response:
    svc = CalendarService(session)
    try:
        await svc.get_calendar(calendar_id, user.id, "ro")
        cal, appointments = await svc.export_appointments(calendar_id)
    except (CalendarNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc
    body = build_ical(cal, appointments)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{cal.name}.ics"'},
    )


@router.get("/calendars/{calendar_id}/feed-token")
async def get_feed_token(calendar_id: int, user: CurrentUser, session: DbSession) -> dict[str, str]:
    """Mint the Znuny-compatible subscription token for the current user."""
    svc = CalendarService(session)
    try:
        await svc.get_calendar(calendar_id, user.id, "ro")
        token = await svc.feed_token(calendar_id, user.id)
    except (CalendarNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc
    return {"token": token, "login": user.login}


@router.get("/calendars/{calendar_id}/feed.ics")
async def calendar_feed(
    calendar_id: int,
    session: DbSession,
    login: str = Query(...),
    token: str = Query(...),
) -> Response:
    """Unauthenticated, token-gated read-only ICS subscription feed."""
    svc = CalendarService(session)
    if not await svc.verify_feed_token(calendar_id, login, token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid feed token")
    try:
        cal, appointments = await svc.export_appointments(calendar_id)
    except CalendarNotFound as exc:
        raise _map_exc(exc) from exc
    body = build_ical(cal, appointments)
    return Response(content=body, media_type="text/calendar; charset=utf-8")


# ── Appointments ─────────────────────────────────────────────────────────


@router.get("/appointments", response_model=list[OccurrenceOut])
async def list_appointments(
    user: CurrentUser,
    session: DbSession,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    calendar_id: Annotated[list[int] | None, Query()] = None,
) -> list[OccurrenceOut]:
    svc = CalendarService(session)
    return await svc.list_occurrences(
        user.id, range_start=start, range_end=end, calendar_ids=calendar_id
    )


@router.post("/appointments", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: AppointmentIn, user: CurrentUser, session: DbSession
) -> AppointmentOut:
    svc = CalendarService(session)
    try:
        async with session.begin():
            row = await svc.create_appointment(user.id, body)
        return _appointment_out(row)
    except (CalendarNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc


@router.get("/appointments/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: int, user: CurrentUser, session: DbSession
) -> AppointmentOut:
    svc = CalendarService(session)
    try:
        row = await svc.get_appointment(appointment_id, user.id)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc
    return _appointment_out(row)


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
async def update_appointment(
    appointment_id: int, body: AppointmentUpdateIn, user: CurrentUser, session: DbSession
) -> AppointmentOut:
    svc = CalendarService(session)
    try:
        async with session.begin():
            row = await svc.update_appointment(user.id, appointment_id, body)
        return _appointment_out(row)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc


@router.delete("/appointments/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_appointment(
    appointment_id: int,
    user: CurrentUser,
    session: DbSession,
    occurrence: Annotated[datetime | None, Query()] = None,
) -> None:
    svc = CalendarService(session)
    try:
        async with session.begin():
            await svc.delete_appointment(user.id, appointment_id, occurrence=occurrence)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc


@router.post(
    "/appointments/{appointment_id}/tickets/{ticket_id}",
    response_model=TicketLinkOut,
    status_code=status.HTTP_201_CREATED,
)
async def link_ticket(
    appointment_id: int, ticket_id: int, user: CurrentUser, session: DbSession
) -> TicketLinkOut:
    svc = CalendarService(session)
    try:
        async with session.begin():
            link = await svc.link_ticket(user.id, appointment_id, ticket_id)
        return TicketLinkOut.model_validate(link, from_attributes=True)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc


@router.delete(
    "/appointments/{appointment_id}/tickets/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlink_ticket(
    appointment_id: int, ticket_id: int, user: CurrentUser, session: DbSession
) -> None:
    svc = CalendarService(session)
    try:
        async with session.begin():
            await svc.unlink_ticket(user.id, appointment_id, ticket_id)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc


@router.get("/appointments/{appointment_id}/tickets", response_model=list[TicketLinkOut])
async def list_ticket_links(
    appointment_id: int, user: CurrentUser, session: DbSession
) -> list[TicketLinkOut]:
    svc = CalendarService(session)
    try:
        await svc.get_appointment(appointment_id, user.id)
    except (CalendarNotFound, AppointmentNotFound, CalendarForbidden) as exc:
        raise _map_exc(exc) from exc
    links = await svc.list_ticket_links(appointment_id)
    return [TicketLinkOut.model_validate(link, from_attributes=True) for link in links]
