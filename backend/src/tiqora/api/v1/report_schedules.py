"""Report schedules API (plan D8, D11): CRUD and a recurrence preview.

Every route needs report access and the create right (``rights.create``); a schedule is
visible to its owner and to admins only (404 otherwise). ``/run``, the run history and the
run files are added with the delivery job.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.timezones import resolve_user_time_zone, valid_time_zone
from tiqora.jobs.service import utc_now
from tiqora.reporting.access import require_report_access
from tiqora.reporting.recurrence import describe, upcoming
from tiqora.reporting.rights import ReportRights, report_rights
from tiqora.reporting.schedule_schemas import (
    RecurrencePreviewIn,
    RecurrencePreviewOut,
    ReportScheduleIn,
    ReportScheduleListItemOut,
    ReportScheduleOut,
    ReportScheduleUpdateIn,
)
from tiqora.reporting.schedules import (
    CREATE_RIGHT_MESSAGE,
    NOT_FOUND_MESSAGE,
    ScheduleConflict,
    ScheduleForbidden,
    ScheduleInvalid,
    ScheduleNotFound,
    create_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    schedule_list_out,
    schedule_out,
    update_schedule,
)
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.texts import load_texts
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/report-schedules", tags=["report-schedules"])

#: schedule id in a path: bounded to the database's BIGINT so huge ids are a 422, not a 500
ScheduleId = Annotated[int, Path(ge=1, le=2**63 - 1)]

_ADMIN_ONLY_MESSAGE = "Alle Zeitpläne dürfen nur Administratoren sehen."


async def _access(
    session: AsyncSession, user: AuthenticatedUser, settings: AppSettings
) -> tuple[ReportingSettings, ReportRights]:
    reporting = await load_reporting_settings(session, settings)
    await require_report_access(session, user.id, reporting)
    rights = await report_rights(session, user.id, reporting)
    if not rights.create:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"message": CREATE_RIGHT_MESSAGE}
        )
    return reporting, rights


def _store_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ScheduleNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"message": NOT_FOUND_MESSAGE}
        ) from None
    if isinstance(exc, ScheduleForbidden):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"message": exc.message}
        ) from None
    if isinstance(exc, ScheduleConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Der Zeitplan wurde inzwischen geändert. Bitte laden Sie die Seite neu.",
                "current_version": exc.current_version,
            },
        ) from None
    if isinstance(exc, ScheduleInvalid):
        raise HTTPException(
            status_code=422,
            detail={"message": exc.message, "path": exc.path},
        ) from None
    raise exc


_STORE_ERRORS = (ScheduleNotFound, ScheduleForbidden, ScheduleConflict, ScheduleInvalid)


@router.get("", response_model=list[ReportScheduleListItemOut])
async def list_report_schedules(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    all_: Annotated[bool, Query(alias="all")] = False,
) -> list[ReportScheduleListItemOut]:
    """Own schedules (lightweight rows); ``all=true`` lists everybody's, admins only."""
    _, rights = await _access(session, user, settings)
    if all_ and not rights.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"message": _ADMIN_ONLY_MESSAGE}
        )
    schedules = await list_schedules(session, user.id, admin=rights.admin, include_all=all_)
    return await schedule_list_out(session, schedules, viewer_id=user.id, rights=rights)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ReportScheduleOut)
async def create_report_schedule(
    body: ReportScheduleIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportScheduleOut:
    reporting, rights = await _access(session, user, settings)
    try:
        created = await create_schedule(
            session, user.id, rights, body, reporting=reporting, now=utc_now()
        )
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = await schedule_out(
        session, created, viewer_id=user.id, rights=rights, reporting=reporting
    )
    await session.commit()
    return out


@router.post("/preview-recurrence", response_model=RecurrencePreviewOut)
async def preview_recurrence(
    body: RecurrencePreviewIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> RecurrencePreviewOut:
    """The next five occurrences and the German sentence (pure, bounded)."""
    await _access(session, user, settings)
    if body.time_zone is None or not body.time_zone.strip():
        zone = await resolve_user_time_zone(session, SysConfig(session), user.id)
    else:
        zone = valid_time_zone(body.time_zone) or ""
        if not zone:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Unbekannte Zeitzone: {body.time_zone.strip()[:64]}",
                    "path": "time_zone",
                },
            )
    return RecurrencePreviewOut(
        next_runs=upcoming(body.recurrence, zone, utc_now(), 5),
        text=describe(body.recurrence, load_texts("de")),
    )


@router.get("/{schedule_id}", response_model=ReportScheduleOut)
async def get_report_schedule(
    schedule_id: ScheduleId, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ReportScheduleOut:
    reporting, rights = await _access(session, user, settings)
    try:
        found = await get_schedule(session, schedule_id, user.id, admin=rights.admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    return await schedule_out(session, found, viewer_id=user.id, rights=rights, reporting=reporting)


@router.put("/{schedule_id}", response_model=ReportScheduleOut)
async def update_report_schedule(
    schedule_id: ScheduleId,
    body: ReportScheduleUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ReportScheduleOut:
    reporting, rights = await _access(session, user, settings)
    try:
        updated = await update_schedule(
            session, schedule_id, user.id, rights, body, reporting=reporting, now=utc_now()
        )
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = await schedule_out(
        session, updated, viewer_id=user.id, rights=rights, reporting=reporting
    )
    await session.commit()
    return out


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_schedule(
    schedule_id: ScheduleId, user: CurrentUser, session: DbSession, settings: AppSettings
) -> Response:
    _, rights = await _access(session, user, settings)
    try:
        await delete_schedule(session, schedule_id, user.id, admin=rights.admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
