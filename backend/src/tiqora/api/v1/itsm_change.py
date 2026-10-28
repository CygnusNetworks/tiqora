"""Change-management endpoints for agents — ``/api/v1/itsm/changes`` (API-key area ``tickets``).

``ChangeError`` is an ``HTTPException``: services raise it and the answer is
``{"detail": {"code", ...}}`` with its status. Static paths (``/changes/status``) are declared
before ``/changes/{ticket_id:int}``. Handlers commit; the services do not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import AppSettings, CurrentUser, DbSession, get_session_factory_dep
from tiqora.api.v1.itsm_change_schemas import (
    AdoptIn,
    AgendaIn,
    CabChangeItemOut,
    CabOut,
    ChangeApprovalOut,
    ChangeCalendarOut,
    ChangeCreatedOut,
    ChangeEventOut,
    ChangeOut,
    ChangePageOut,
    ChangeStatusOut,
    ChangeTemplateSummaryOut,
    FreezeOccurrencesOut,
    LegacyChangeOut,
    LegacyChangePageOut,
    MeetingOut,
    RelatedLinkIn,
    RelatedTicketOut,
    ServiceAvailabilityOut,
    TaskListItemOut,
    TaskOrderIn,
    TaskOut,
    approval_out,
    cab_change_item_out,
    cab_out,
    change_calendar_out,
    change_out,
    change_page_out,
    event_out,
    freeze_occurrences_out,
    legacy_change_out,
    legacy_page_out,
    meeting_out,
    related_out,
    service_availability_out,
    task_list_item_out,
    task_out,
    template_summary,
)
from tiqora.calendar.recurrence import to_utc_naive
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.ticket_access import readable_tickets
from tiqora.itsm.change.cab import (
    MeetingIn,
    MeetingUpdateIn,
    create_meeting,
    get_meeting,
    list_cabs,
    list_meetings,
    meeting_cab_id,
    set_agenda,
    suggest_agenda,
    update_meeting,
)
from tiqora.itsm.change.conflicts import ConflictOut, read_conflicts
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import occurrences
from tiqora.itsm.change.gate import can_see_legacy, change_availability, require_change_mode
from tiqora.itsm.change.impact import (
    ChangeImpactOut,
    enqueue_impact,
    read_impact,
)
from tiqora.itsm.change.legacy.reader import LegacySearchTooBroad
from tiqora.itsm.change.legacy.sources import (
    LegacyChangeSource,
    get_legacy_source,
    visible_links,
)
from tiqora.itsm.change.phases import FINAL_PHASES, ChangeType, Phase
from tiqora.itsm.change.read import (
    MAX_RANGE,
    ChangeQuery,
    calendar,
    list_changes,
    my_tasks,
    service_availability,
)
from tiqora.itsm.change.related import (
    create_from_ticket,
    link_related,
    list_related,
    unlink_related,
)
from tiqora.itsm.change.risk import (
    ChangeRiskOut,
    evaluate_risk,
    override_level,
    read_risk,
    set_answers,
)
from tiqora.itsm.change.service import (
    ChangeCreateIn,
    ChangeUpdateIn,
    _row,
    _ticket,
    adopt_ticket,
    create_change,
    get_change,
    list_events,
    update_change,
)
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.change.tasks import (
    TaskIn,
    TaskUpdateIn,
    add_task,
    list_tasks,
    reorder_tasks,
    update_task,
)
from tiqora.itsm.change.templates import list_templates
from tiqora.itsm.change.transitions import TransitionIn, read_approval, transition
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.deeplink import CHANGE_ZOOM, znuny_agent_url
from tiqora.itsm.mode import get_itsm_mode
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.jobs.schemas import JobAccepted
from tiqora.znuny.sysconfig import SysConfig


class ChangeRiskAnswersIn(BaseModel):
    #: ``question_key -> answer_key``; replaces all answers (a missing question is unanswered)
    answers: dict[str, str] = Field(default_factory=dict, max_length=200)


class ChangeRiskOverrideIn(BaseModel):
    #: ``low|medium|high|very_high``; ``null`` removes the override
    level: str | None = None
    reason: str = Field(..., max_length=1000)


router = APIRouter(prefix="/itsm", tags=["itsm-change"])
#: ticket sub-resources (``/tickets/{id}/change``, …)
ticket_router = APIRouter(prefix="/tickets", tags=["itsm-change"])


@router.get("/changes/status", response_model=ChangeStatusOut)
async def get_change_status(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeStatusOut:
    """Whether changes are available and what the caller may do. Answers in every mode."""
    availability = await change_availability(session, settings, user_id=user.id)
    return ChangeStatusOut(
        mode=availability.mode,
        available=availability.available,
        reason=availability.reason,
        legacy=availability.legacy,
        can_manage=availability.can_manage,
        can_see_legacy=availability.can_see_legacy,
        setup_complete_change=availability.setup_complete_change,
    )


@router.get("/change-templates", response_model=list[ChangeTemplateSummaryOut])
async def get_change_templates(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[ChangeTemplateSummaryOut]:
    """Valid change templates for the create dialog (Mode B)."""
    _ = user
    await require_change_mode(session, settings)
    return [template_summary(v) for v in await list_templates(session, include_invalid=False)]


@router.get("/freeze-windows", response_model=FreezeOccurrencesOut)
async def get_freeze_windows(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> FreezeOccurrencesOut:
    """Occurrences of the valid freeze windows that overlap ``[start, end)``; times are UTC."""
    _ = user
    await require_change_mode(session, settings)
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    if hi <= lo:
        raise ChangeError("range_invalid", 422)
    if hi - lo > MAX_RANGE:
        raise ChangeError("range_too_large", 422)
    return freeze_occurrences_out(await occurrences(session, start=lo, end=hi))


@router.get("/cabs", response_model=list[CabOut])
async def get_cabs(user: CurrentUser, session: DbSession, settings: AppSettings) -> list[CabOut]:
    """Valid CABs with their members (for the change's CAB choice and the meeting pages)."""
    _ = user
    await require_change_mode(session, settings)
    return [cab_out(v) for v in await list_cabs(session, include_invalid=False)]


@router.get("/cab-meetings", response_model=list[MeetingOut])
async def get_cab_meetings(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> list[MeetingOut]:
    """Meetings that overlap ``[start, end)`` (without their agenda lines); times are UTC."""
    await require_change_mode(session, settings)
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    if hi <= lo:
        raise ChangeError("range_invalid", 422)
    if hi - lo > MAX_RANGE:
        raise ChangeError("range_too_large", 422)
    return [meeting_out(v) for v in await list_meetings(session, start=lo, end=hi, user_id=user.id)]


@router.post("/cab-meetings", response_model=MeetingOut, status_code=status.HTTP_201_CREATED)
async def post_cab_meeting(
    body: MeetingIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> MeetingOut:
    """Plan a meeting (change managers)."""
    await require_change_mode(session, settings)
    out = meeting_out(await create_meeting(session, settings, data=body, user_id=user.id))
    await session.commit()
    return out


@router.get("/cab-meetings/{meeting_id:int}", response_model=MeetingOut)
async def get_cab_meeting(
    meeting_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> MeetingOut:
    """Meeting with agenda and minutes; lines of unreadable changes are ``hidden``."""
    await require_change_mode(session, settings)
    return meeting_out(await get_meeting(session, meeting_id=meeting_id, user_id=user.id))


@router.patch("/cab-meetings/{meeting_id:int}", response_model=MeetingOut)
async def patch_cab_meeting(
    meeting_id: int,
    body: MeetingUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MeetingOut:
    """Title, time, place, status and minutes of a meeting (change managers)."""
    await require_change_mode(session, settings)
    out = meeting_out(
        await update_meeting(session, settings, meeting_id=meeting_id, data=body, user_id=user.id)
    )
    await session.commit()
    return out


@router.put("/cab-meetings/{meeting_id:int}/agenda", response_model=MeetingOut)
async def put_cab_meeting_agenda(
    meeting_id: int, body: AgendaIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> MeetingOut:
    """Replace the agenda of a planned meeting (change managers)."""
    await require_change_mode(session, settings)
    out = meeting_out(
        await set_agenda(
            session, settings, meeting_id=meeting_id, items=body.items, user_id=user.id
        )
    )
    await session.commit()
    return out


@router.get("/cab-meetings/{meeting_id:int}/suggestions", response_model=list[CabChangeItemOut])
async def get_cab_meeting_suggestions(
    meeting_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[CabChangeItemOut]:
    """Readable changes waiting for this meeting's CAB to decide."""
    await require_change_mode(session, settings)
    cab_id = await meeting_cab_id(session, meeting_id)
    items = await suggest_agenda(
        session, settings, cab_id=cab_id, user_id=user.id, meeting_id=meeting_id
    )
    return [cab_change_item_out(i) for i in items]


@router.get("/changes", response_model=ChangePageOut)
async def get_changes(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    q: Annotated[str | None, Query(max_length=200)] = None,
    phase: Annotated[list[Phase] | None, Query()] = None,
    change_type: Annotated[list[ChangeType] | None, Query()] = None,
    service_id: Annotated[int | None, Query()] = None,
    ci_id: Annotated[int | None, Query()] = None,
    window_start: Annotated[datetime | None, Query()] = None,
    window_end: Annotated[datetime | None, Query()] = None,
    mine: Annotated[Literal["", "owned", "created"], Query()] = "",
    order: Annotated[Literal["planned_start", "updated"], Query()] = "updated",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> ChangePageOut:
    """Changes the caller may read; ``q`` matches the number or the title. Times are UTC."""
    await require_change_mode(session, settings)
    query = ChangeQuery(
        text=q,
        phases=tuple(phase or ()),
        change_types=tuple(change_type or ()),
        service_id=service_id,
        ci_id=ci_id,
        window_start=None if window_start is None else to_utc_naive(window_start),
        window_end=None if window_end is None else to_utc_naive(window_end),
        mine=mine,
        order=order,
        limit=limit,
        offset=offset,
    )
    return change_page_out(await list_changes(session, query, user_id=user.id))


@router.get("/change-tasks/mine", response_model=list[TaskListItemOut])
async def get_my_change_tasks(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    open_only: Annotated[bool, Query()] = True,
) -> list[TaskListItemOut]:
    """Change tasks the caller owns (by default only unfinished ones of live changes)."""
    await require_change_mode(session, settings)
    items = await my_tasks(session, user_id=user.id, open_only=open_only)
    return [task_list_item_out(i) for i in items]


@router.get("/change-calendar", response_model=ChangeCalendarOut)
async def get_change_calendar(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    service_id: Annotated[list[int] | None, Query(max_length=200)] = None,
    include_tasks: Annotated[bool, Query()] = True,
) -> ChangeCalendarOut:
    """Changes, tasks and freeze windows of a range of at most 92 days, with conflict flags."""
    await require_change_mode(session, settings)
    cal = await calendar(
        session,
        user_id=user.id,
        start=start,
        end=end,
        service_ids=service_id or (),
        include_tasks=include_tasks,
    )
    return change_calendar_out(cal)


@router.get("/change-calendar/services", response_model=list[ServiceAvailabilityOut])
async def get_change_calendar_services(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> list[ServiceAvailabilityOut]:
    """Projected service availability: planned change and freeze windows per service."""
    await require_change_mode(session, settings)
    rows = await service_availability(session, user_id=user.id, start=start, end=end)
    return [service_availability_out(r) for r in rows]


@router.post("/changes", response_model=ChangeCreatedOut, status_code=status.HTTP_201_CREATED)
async def post_change(
    body: ChangeCreateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> ChangeCreatedOut:
    """Create a change ticket with its record in phase ``draft``."""
    view, tasks_job_id = await create_change(
        session, settings, SysConfig(session), data=body, user_id=user.id, session_factory=factory
    )
    out = change_out(view)
    await session.commit()
    return ChangeCreatedOut(change=out, tasks_job_id=tasks_job_id, warnings=list(view.warnings))


@router.get("/changes/{ticket_id:int}", response_model=ChangeOut)
async def get_change_detail(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeOut:
    """A change the caller may read (404 without ``ro`` on the ticket)."""
    return change_out(await get_change(session, settings, ticket_id=ticket_id, user_id=user.id))


@router.patch("/changes/{ticket_id:int}", response_model=ChangeOut)
async def patch_change(
    ticket_id: int,
    body: ChangeUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ChangeOut:
    """Edit fields of the change. The phase is not a field: it moves only by transition."""
    view = await update_change(
        session, settings, SysConfig(session), ticket_id=ticket_id, data=body, user_id=user.id
    )
    out = change_out(view)
    await session.commit()
    return out


@router.post("/changes/{ticket_id:int}/transition", response_model=ChangeOut)
async def post_change_transition(
    ticket_id: int,
    body: TransitionIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> ChangeOut:
    """Move the change to another phase (needs ``rw``). Guard failures answer 409
    ``transition_refused`` with ``failures: [{code, overridable}]``; a manager passes block
    conflicts with ``override_reason`` and open tasks with ``force`` plus ``reason``."""
    view = await transition(
        session,
        settings,
        SysConfig(session),
        ticket_id=ticket_id,
        data=body,
        user_id=user.id,
        session_factory=factory,
    )
    out = change_out(view)
    await session.commit()
    return out


@router.get("/changes/{ticket_id:int}/approval", response_model=ChangeApprovalOut)
async def get_change_approval(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeApprovalOut:
    """The CAB approval of the current round as the approval engine sees it; votes are cast in
    the approvals section. ``history_missing`` when the approval set was erased."""
    return approval_out(
        await read_approval(session, settings, ticket_id=ticket_id, user_id=user.id)
    )


@router.get("/changes/{ticket_id:int}/events", response_model=list[ChangeEventOut])
async def get_change_events(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[ChangeEventOut]:
    """Timeline of the change, newest first; ``before_id`` pages backwards."""
    rows = await list_events(
        session, settings, ticket_id=ticket_id, user_id=user.id, limit=limit, before_id=before_id
    )
    return [event_out(r) for r in rows]


@router.get("/changes/{ticket_id:int}/impact", response_model=ChangeImpactOut)
async def get_change_impact(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeImpactOut:
    """The stored impact set: CIs and services of the change and the open incidents on them.

    Only what the caller may read: CIs of classes without ``ro`` are counted, not named.
    """
    return await read_impact(
        session, settings, ticket_id=ticket_id, user_id=user.id, sysconfig=SysConfig(session)
    )


@router.get("/changes/{ticket_id:int}/conflicts", response_model=list[ConflictOut])
async def get_change_conflicts(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[ConflictOut]:
    """Freeze windows, overlapping changes and tasks off the window, from stored data only.

    An overlapping change the caller cannot read is listed as ``hidden`` with its window and
    counts, without a reference.
    """
    return await read_conflicts(
        session, settings, ticket_id=ticket_id, user_id=user.id, sysconfig=SysConfig(session)
    )


@router.get("/changes/{ticket_id:int}/risk", response_model=ChangeRiskOut)
async def get_change_risk(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeRiskOut:
    """Questionnaire, answers and the stored risk evaluation; references the caller cannot read
    are left out of the findings and only counted."""
    return await read_risk(session, settings, ticket_id=ticket_id, user_id=user.id)


@router.put("/changes/{ticket_id:int}/risk/answers", response_model=ChangeRiskOut)
async def put_change_risk_answers(
    ticket_id: int,
    body: ChangeRiskAnswersIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ChangeRiskOut:
    """Replace the questionnaire answers (needs ``rw``) and evaluate the risk again."""
    await set_answers(session, settings, ticket_id=ticket_id, answers=body.answers, user_id=user.id)
    out = await read_risk(session, settings, ticket_id=ticket_id, user_id=user.id)
    await session.commit()
    return out


@router.post("/changes/{ticket_id:int}/risk/evaluate", response_model=ChangeRiskOut)
async def post_change_risk_evaluate(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeRiskOut:
    """Evaluate the risk again from stored data only (needs ``rw``)."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user.id, write=True)
    await evaluate_risk(session, settings, ticket_id=ticket_id, user_id=user.id)
    out = await read_risk(session, settings, ticket_id=ticket_id, user_id=user.id)
    await session.commit()
    return out


@router.put("/changes/{ticket_id:int}/risk/override", response_model=ChangeRiskOut)
async def put_change_risk_override(
    ticket_id: int,
    body: ChangeRiskOverrideIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ChangeRiskOut:
    """A change manager sets or removes the risk level by hand; the reason is required."""
    await override_level(
        session,
        settings,
        ticket_id=ticket_id,
        level=body.level,
        reason=body.reason,
        user_id=user.id,
    )
    out = await read_risk(session, settings, ticket_id=ticket_id, user_id=user.id)
    await session.commit()
    return out


@router.post(
    "/changes/{ticket_id:int}/impact/recompute",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_change_impact_recompute(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> JobAccepted:
    """Compute the impact set again in the background; poll ``GET /api/v1/jobs/{job_id}``."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user.id, write=True)
    row = await _row(session, ticket_id)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)
    job_id = await enqueue_impact(session, ticket_id=ticket_id, user_id=user.id)
    if job_id is None:  # pragma: no cover - the phase was checked above
        raise ChangeError("change_final", 409)
    job = await session.get(TiqoraJob, job_id)
    out = JobAccepted(
        job_id=job_id, status="running" if job is not None and job.status == "running" else "queued"
    )
    await session.commit()
    return out


@router.get("/changes/{ticket_id:int}/tasks", response_model=list[TaskOut])
async def get_change_tasks(ticket_id: int, user: CurrentUser, session: DbSession) -> list[TaskOut]:
    """The change's tasks in order; task tickets the caller cannot read are left out."""
    tasks = await list_tasks(session, change_ticket_id=ticket_id, user_id=user.id)
    return [task_out(t) for t in tasks]


@router.post(
    "/changes/{ticket_id:int}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED
)
async def post_change_task(
    ticket_id: int,
    body: TaskIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> TaskOut:
    """Add a task ticket under the change (child ticket, position last unless given)."""
    view = await add_task(
        session,
        settings,
        SysConfig(session),
        change_ticket_id=ticket_id,
        data=body,
        user_id=user.id,
        session_factory=factory,
    )
    out = task_out(view)
    await session.commit()
    return out


@router.put("/changes/{ticket_id:int}/tasks/order", response_model=list[TaskOut])
async def put_change_task_order(
    ticket_id: int,
    body: TaskOrderIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[TaskOut]:
    """Put the caller's tasks in the given order (every readable task exactly once)."""
    tasks = await reorder_tasks(
        session, settings, change_ticket_id=ticket_id, order=body.order, user_id=user.id
    )
    out = [task_out(t) for t in tasks]
    await session.commit()
    return out


@router.patch("/changes/{ticket_id:int}/tasks/{task_ticket_id:int}", response_model=TaskOut)
async def patch_change_task(
    ticket_id: int,
    task_ticket_id: int,
    body: TaskUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> TaskOut:
    """Kind, planned/actual window, effort and owner of one task."""
    view = await update_task(
        session,
        settings,
        SysConfig(session),
        change_ticket_id=ticket_id,
        task_ticket_id=task_ticket_id,
        data=body,
        user_id=user.id,
        session_factory=factory,
    )
    out = task_out(view)
    await session.commit()
    return out


@ticket_router.post("/{ticket_id}/change", response_model=ChangeOut)
async def post_ticket_change(
    ticket_id: int, body: AdoptIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ChangeOut:
    """Make an existing ticket of the change type a change (phase ``draft``)."""
    view = await adopt_ticket(
        session,
        settings,
        SysConfig(session),
        ticket_id=ticket_id,
        change_type=body.change_type,
        user_id=user.id,
    )
    out = change_out(view)
    await session.commit()
    return out


@ticket_router.post(
    "/{ticket_id}/change/create-from",
    response_model=ChangeCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_ticket_change_create_from(
    ticket_id: int,
    body: ChangeCreateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> ChangeCreatedOut:
    """A new change from this ticket (``ro``): service, CIs and customer are prefilled and the
    two are linked."""
    if body.from_ticket_id not in (None, ticket_id):
        raise ChangeError("from_ticket_mismatch", 422)
    view, tasks_job_id = await create_from_ticket(
        session,
        settings,
        SysConfig(session),
        source_ticket_id=ticket_id,
        data=body,
        user_id=user.id,
        session_factory=factory,
    )
    out = change_out(view)
    await session.commit()
    return ChangeCreatedOut(change=out, tasks_job_id=tasks_job_id, warnings=list(view.warnings))


@router.get("/changes/{ticket_id:int}/related", response_model=list[RelatedTicketOut])
async def get_change_related(
    ticket_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[RelatedTicketOut]:
    """Incidents, problems and other tickets linked to the change (tasks and unreadable
    tickets left out)."""
    items = await list_related(session, settings, ticket_id=ticket_id, user_id=user.id)
    return [related_out(i) for i in items]


@router.post(
    "/changes/{ticket_id:int}/related",
    response_model=list[RelatedTicketOut],
    status_code=status.HTTP_201_CREATED,
)
async def post_change_related(
    ticket_id: int,
    body: RelatedLinkIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[RelatedTicketOut]:
    """Link another ticket to the change (``rw`` on the change, ``ro`` on the other)."""
    items = await link_related(
        session, settings, ticket_id=ticket_id, other_ticket_id=body.ticket_id, user_id=user.id
    )
    out = [related_out(i) for i in items]
    await session.commit()
    return out


@router.delete(
    "/changes/{ticket_id:int}/related/{other_ticket_id:int}",
    response_model=list[RelatedTicketOut],
)
async def delete_change_related(
    ticket_id: int,
    other_ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[RelatedTicketOut]:
    """Remove the link between the change and another ticket."""
    items = await unlink_related(
        session, settings, ticket_id=ticket_id, other_ticket_id=other_ticket_id, user_id=user.id
    )
    out = [related_out(i) for i in items]
    await session.commit()
    return out


# --- legacy (Znuny) changes: read-only ----------------------------------------------------------


async def _legacy_source(
    session: AsyncSession, settings: AppSettings, *, user_id: int
) -> LegacyChangeSource:
    """The source first (404 without one), then the legacy change group (403)."""
    source = await get_legacy_source(session, settings)
    if source is None:
        raise ChangeError("legacy_unavailable", 404)
    if not await can_see_legacy(session, user_id, await load_change_settings(session, settings)):
        raise ChangeError("legacy_forbidden", 403)
    return source


@router.get("/legacy-changes", response_model=LegacyChangePageOut)
async def get_legacy_changes(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    q: Annotated[str | None, Query(max_length=200)] = None,
    ticket_id: Annotated[int | None, Query()] = None,
    ci: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> LegacyChangePageOut:
    """Znuny changes (Mode A: live from Znuny), newest change first, for members of the legacy
    change group. ``q`` matches number, title, description and justification; ``ticket_id`` /
    ``ci`` (``znuny:<id>``) keep the changes linked to that ticket / CI and answer an empty page
    when the caller cannot read it; a text search over its time limit answers 409
    ``search_too_broad``. Times are UTC."""
    source = await _legacy_source(session, settings, user_id=user.id)
    ci_ref: CiRef | None = None
    if ci:
        try:
            ci_ref = CiRef.parse(ci)
        except ValueError as exc:
            raise ChangeError("ci_ref_invalid", 422) from exc
    if ticket_id is not None and not await readable_tickets(session, [ticket_id], user_id=user.id):
        return legacy_page_out(source.kind, [], 0)
    if ci_ref is not None:
        mode = (await get_itsm_mode(session)).mode
        provider = get_ci_provider(mode, SysConfig(session))
        if provider is None or (
            ci_ref.source == provider.source
            and ci_ref not in await provider.get_many(session, [ci_ref], user_id=user.id)
        ):
            return legacy_page_out(source.kind, [], 0)
    try:
        items, total = await source.search(
            session, text=q, ticket_id=ticket_id, ci_ref=ci_ref, limit=limit, offset=offset
        )
    except LegacySearchTooBroad as exc:
        raise ChangeError("search_too_broad", 409) from exc
    return legacy_page_out(source.kind, items, total)


@router.get("/legacy-changes/{znuny_change_id:int}", response_model=LegacyChangeOut)
async def get_legacy_change(
    znuny_change_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> LegacyChangeOut:
    """One Znuny change with work orders, CAB, history and links; linked tickets and CIs the
    caller cannot read are left out (``hidden_links`` counts them)."""
    source = await _legacy_source(session, settings, user_id=user.id)
    change = await source.get(session, znuny_change_id)
    if change is None:
        raise ChangeError("legacy_change_not_found", 404)
    sysconfig = SysConfig(session)
    provider = get_ci_provider((await get_itsm_mode(session)).mode, sysconfig)
    links = await visible_links(session, change, user_id=user.id, provider=provider)
    url = None
    if source.kind == "live":
        url = await znuny_agent_url(
            sysconfig,
            await load_itsm_settings(session, settings),
            CHANGE_ZOOM,
            ChangeID=change.znuny_change_id,
        )
    return legacy_change_out(source.kind, change, links, znuny_url=url)
