"""Admin CRUD for ticket priorities."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import invalidate_cache_for_priority, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import PriorityCreate, PriorityOut, PriorityUpdate
from tiqora.db.legacy.ticket import TicketPriority

router = APIRouter(prefix="/priorities", tags=["admin:priorities"])


@router.get("", response_model=list[PriorityOut])
async def list_priorities(admin: AdminUser, session: DbSession) -> list[TicketPriority]:
    _ = admin
    result = await session.execute(select(TicketPriority).order_by(TicketPriority.name))
    return list(result.scalars().all())


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
    ts = now()
    priority = TicketPriority(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(priority)
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
