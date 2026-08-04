"""Admin CRUD for system addresses (Znuny ``system_address``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    SYSTEM_ADDRESS_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    SystemAddressCreate,
    SystemAddressOut,
    SystemAddressUpdate,
)
from tiqora.db.legacy.queue import SystemAddress

router = APIRouter(prefix="/system-addresses", tags=["admin:system-addresses"])

# List stays on readonly.list_system_addresses (unpaginated picker-friendly GET).


@router.get("/{address_id}", response_model=SystemAddressOut)
async def get_system_address(
    address_id: int, admin: AdminUser, session: DbSession
) -> SystemAddress:
    _ = admin
    row = await session.get(SystemAddress, address_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    return row


@router.post("", response_model=SystemAddressOut, status_code=status.HTTP_201_CREATED)
async def create_system_address(
    body: SystemAddressCreate, admin: AdminUser, session: DbSession
) -> SystemAddress:
    ts = now()
    row = SystemAddress(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, SYSTEM_ADDRESS_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{address_id}", response_model=SystemAddressOut)
async def update_system_address(
    address_id: int, body: SystemAddressUpdate, admin: AdminUser, session: DbSession
) -> SystemAddress:
    row = await session.get(SystemAddress, address_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SYSTEM_ADDRESS_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_system_address(
    address_id: int, admin: AdminUser, session: DbSession
) -> None:
    row = await session.get(SystemAddress, address_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SYSTEM_ADDRESS_CACHE_TYPES)
    await session.commit()
