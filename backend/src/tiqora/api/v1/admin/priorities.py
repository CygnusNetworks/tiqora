"""Admin CRUD for ticket priorities."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    PRIORITY_CACHE_TYPES,
    invalidate_cache_for_priority,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import PriorityCreate, PriorityOut, PriorityUpdate
from tiqora.db.legacy.ticket import TicketPriority

router = APIRouter(prefix="/priorities", tags=["admin:priorities"])


@router.get("", response_model=Page[PriorityOut])
async def list_priorities(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[PriorityOut]:
    _ = admin
    stmt = apply_valid_filter(
        select(TicketPriority), TicketPriority.valid_id, params.valid
    ).order_by(TicketPriority.name)
    return await paginate(session, PriorityOut, stmt, params)


@router.get("/{priority_id}", response_model=PriorityOut)
async def get_priority(priority_id: int, admin: AdminUser, session: DbSession) -> TicketPriority:
    _ = admin
    priority = await session.get(TicketPriority, priority_id)
    if priority is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Priority not found")
    return priority


@router.post("", response_model=PriorityOut, status_code=status.HTTP_201_CREATED)
async def create_priority(
    body: PriorityCreate, admin: AdminUser, session: DbSession
) -> TicketPriority:
    from tiqora.db.legacy.profile import default_color_for_write, insert_row_with_color

    ts = now()
    data = body.model_dump()
    color = default_color_for_write()
    if color is None:
        priority = TicketPriority(
            **data,
            create_time=ts,
            create_by=admin.id,
            change_time=ts,
            change_by=admin.id,
        )
        session.add(priority)
    else:
        # Znuny 7.0+: ticket_priority.color is NOT NULL and not on the 6.5 ORM
        # baseline. Insert via shared Core helper (see DEFAULT_STATE_PRIORITY_COLOR).
        priority_id = await insert_row_with_color(
            session,
            table_name="ticket_priority",
            values={
                **data,
                "create_time": ts,
                "create_by": admin.id,
                "change_time": ts,
                "change_by": admin.id,
                "color": color,
            },
        )
        priority = await session.get(TicketPriority, priority_id)
        assert priority is not None
    await invalidate_znuny_cache_types(session, PRIORITY_CACHE_TYPES)
    await session.commit()
    await session.refresh(priority)
    return priority


@router.patch("/{priority_id}", response_model=PriorityOut)
async def update_priority(
    priority_id: int, body: PriorityUpdate, admin: AdminUser, session: DbSession
) -> TicketPriority:
    priority = await session.get(TicketPriority, priority_id)
    if priority is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Priority not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(priority, field, value)
    priority.change_time = now()
    priority.change_by = admin.id
    await invalidate_cache_for_priority(session, priority_id)
    await session.commit()
    await session.refresh(priority)
    return priority


@router.delete("/{priority_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_priority(priority_id: int, admin: AdminUser, session: DbSession) -> None:
    priority = await session.get(TicketPriority, priority_id)
    if priority is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Priority not found")
    priority.valid_id = 2
    priority.change_time = now()
    priority.change_by = admin.id
    await invalidate_cache_for_priority(session, priority_id)
    await session.commit()
