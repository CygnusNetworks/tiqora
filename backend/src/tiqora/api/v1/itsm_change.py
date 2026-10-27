"""Change-management endpoints for agents — ``/api/v1/itsm/changes`` (API-key area ``tickets``).

``ChangeError`` is an ``HTTPException``: services raise it and the answer is
``{"detail": {"code", ...}}`` with its status. Static paths (``/changes/status``) are declared
before ``/changes/{ticket_id:int}``. Handlers commit; the services do not.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import AppSettings, CurrentUser, DbSession, get_session_factory_dep
from tiqora.api.v1.itsm_change_schemas import (
    AdoptIn,
    ChangeCreatedOut,
    ChangeEventOut,
    ChangeOut,
    ChangeStatusOut,
    ChangeTemplateSummaryOut,
    FreezeOccurrencesOut,
    TaskOrderIn,
    TaskOut,
    change_out,
    event_out,
    freeze_occurrences_out,
    task_out,
    template_summary,
)
from tiqora.calendar.recurrence import to_utc_naive
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import occurrences
from tiqora.itsm.change.gate import change_availability, require_change_mode
from tiqora.itsm.change.impact import (
    ChangeImpactOut,
    enqueue_impact,
    read_impact,
)
from tiqora.itsm.change.phases import FINAL_PHASES
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
from tiqora.itsm.change.tasks import (
    TaskIn,
    TaskUpdateIn,
    add_task,
    list_tasks,
    reorder_tasks,
    update_task,
)
from tiqora.itsm.change.templates import list_templates
from tiqora.jobs.schemas import JobAccepted
from tiqora.znuny.sysconfig import SysConfig

#: longest range one read covers (the calendar's bound)
MAX_RANGE = timedelta(days=92)

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
    return ChangeCreatedOut(change=out, tasks_job_id=tasks_job_id)


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
