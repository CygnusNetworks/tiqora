"""Change-management endpoints for agents — ``/api/v1/itsm/changes`` (API-key area ``tickets``).

``ChangeError`` is an ``HTTPException``: services raise it and the answer is
``{"detail": {"code", ...}}`` with its status. Static paths (``/changes/status``) are declared
before ``/changes/{ticket_id:int}``. Handlers commit; the services do not.
"""

from __future__ import annotations

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
    TaskOrderIn,
    TaskOut,
    change_out,
    event_out,
    task_out,
)
from tiqora.itsm.change.gate import change_availability
from tiqora.itsm.change.service import (
    ChangeCreateIn,
    ChangeUpdateIn,
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
from tiqora.znuny.sysconfig import SysConfig

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
