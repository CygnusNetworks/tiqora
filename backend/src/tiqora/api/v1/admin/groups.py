"""Admin CRUD for permission groups (Znuny table `permission_groups`, a.k.a. "groups")."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import GroupCreate, GroupOut, GroupUpdate
from tiqora.db.legacy.user import PermissionGroups

router = APIRouter(prefix="/groups", tags=["admin:groups"])


@router.get("", response_model=Page[GroupOut])
async def list_groups(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[GroupOut]:
    _ = admin
    stmt = apply_valid_filter(
        select(PermissionGroups), PermissionGroups.valid_id, params.valid
    ).order_by(PermissionGroups.name)
    return await paginate(session, GroupOut, stmt, params)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(group_id: int, admin: AdminUser, session: DbSession) -> PermissionGroups:
    _ = admin
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupCreate, admin: AdminUser, session: DbSession) -> PermissionGroups:
    ts = now()
    group = PermissionGroups(
        name=body.name,
        comments=body.comments,
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int, body: GroupUpdate, admin: AdminUser, session: DbSession
) -> PermissionGroups:
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    group.change_time = now()
    group.change_by = admin.id
    await session.commit()
    await session.refresh(group)
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_group(group_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate (``valid_id = 2``)."""
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    group.valid_id = 2
    group.change_time = now()
    group.change_by = admin.id
    await session.commit()
