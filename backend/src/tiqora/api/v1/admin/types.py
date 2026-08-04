"""Admin CRUD for ticket types (Znuny ``ticket_type``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import TYPE_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import TicketTypeCreate, TicketTypeOut, TicketTypeUpdate
from tiqora.db.legacy.ticket import TicketType

router = APIRouter(prefix="/types", tags=["admin:types"])


@router.get("", response_model=Page[TicketTypeOut])
async def list_types(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[TicketTypeOut]:
    _ = admin
    stmt = apply_valid_filter(select(TicketType), TicketType.valid_id, params.valid).order_by(
        TicketType.name
    )
    return await paginate(session, TicketTypeOut, stmt, params)


@router.get("/{type_id}", response_model=TicketTypeOut)
async def get_type(type_id: int, admin: AdminUser, session: DbSession) -> TicketType:
    _ = admin
    row = await session.get(TicketType, type_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Type not found")
    return row


@router.post("", response_model=TicketTypeOut, status_code=status.HTTP_201_CREATED)
async def create_type(body: TicketTypeCreate, admin: AdminUser, session: DbSession) -> TicketType:
    ts = now()
    row = TicketType(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, TYPE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{type_id}", response_model=TicketTypeOut)
async def update_type(
    type_id: int, body: TicketTypeUpdate, admin: AdminUser, session: DbSession
) -> TicketType:
    row = await session.get(TicketType, type_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Type not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, TYPE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_type(type_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(TicketType, type_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Type not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, TYPE_CACHE_TYPES)
    await session.commit()
