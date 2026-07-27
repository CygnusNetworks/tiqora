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
    from tiqora.db.legacy.profile import default_color_for_write

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
        # baseline. Insert via Core with an explicit default color.
        from sqlalchemy import text

        params = {
            **data,
            "create_time": ts,
            "create_by": admin.id,
            "change_time": ts,
            "change_by": admin.id,
            "color": color,
        }
        try:
            dialect = session.get_bind().dialect.name
        except Exception:  # noqa: BLE001
            dialect = ""
        if dialect in {"postgresql", "postgres"}:
            priority_id = (
                await session.execute(
                    text(
                        "INSERT INTO ticket_priority "
                        "(name, valid_id, create_time, create_by, change_time, change_by, color) "
                        "VALUES (:name, :valid_id, :create_time, :create_by, :change_time, "
                        ":change_by, :color) RETURNING id"
                    ),
                    params,
                )
            ).scalar_one()
        else:
            await session.execute(
                text(
                    "INSERT INTO ticket_priority "
                    "(name, valid_id, create_time, create_by, change_time, change_by, color) "
                    "VALUES (:name, :valid_id, :create_time, :create_by, :change_time, "
                    ":change_by, :color)"
                ),
                params,
            )
            priority_id = (await session.execute(text("SELECT LAST_INSERT_ID()"))).scalar_one()
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
