"""Admin CRUD for roles + role_user / group_role assignment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    GROUP_ROLE_CACHE_TYPES,
    ROLE_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import (
    GroupOut,
    GroupRoleAssignment,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from tiqora.db.legacy.user import GroupRole, PermissionGroups, Roles

router = APIRouter(prefix="/roles", tags=["admin:roles"])


@router.get("", response_model=Page[RoleOut])
async def list_roles(admin: AdminUser, session: DbSession, params: ListParamsDep) -> Page[RoleOut]:
    _ = admin
    stmt = apply_valid_filter(select(Roles), Roles.valid_id, params.valid).order_by(Roles.name)
    return await paginate(session, RoleOut, stmt, params)


@router.get("/{role_id}", response_model=RoleOut)
async def get_role(role_id: int, admin: AdminUser, session: DbSession) -> Roles:
    _ = admin
    role = await session.get(Roles, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


@router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreate, admin: AdminUser, session: DbSession) -> Roles:
    ts = now()
    role = Roles(
        name=body.name,
        comments=body.comments,
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(role)
    await invalidate_znuny_cache_types(session, ROLE_CACHE_TYPES)
    await session.commit()
    await session.refresh(role)
    return role


@router.patch("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int, body: RoleUpdate, admin: AdminUser, session: DbSession
) -> Roles:
    role = await session.get(Roles, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(role, field, value)
    role.change_time = now()
    role.change_by = admin.id
    await invalidate_znuny_cache_types(session, ROLE_CACHE_TYPES)
    await session.commit()
    await session.refresh(role)
    return role


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_role(role_id: int, admin: AdminUser, session: DbSession) -> None:
    role = await session.get(Roles, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    role.valid_id = 2
    role.change_time = now()
    role.change_by = admin.id
    await invalidate_znuny_cache_types(session, ROLE_CACHE_TYPES)
    await session.commit()


@router.get("/{role_id}/groups", response_model=list[GroupOut])
async def get_role_groups(
    role_id: int, admin: AdminUser, session: DbSession
) -> list[PermissionGroups]:
    """Groups the role grants full (``rw``) access to — the Role↔Groups editor's
    read side. The editor toggles the ``rw`` permission only (see
    :func:`assign_group_role`), so the read set is filtered to that key."""
    _ = admin
    result = await session.execute(
        select(PermissionGroups)
        .join(GroupRole, GroupRole.group_id == PermissionGroups.id)
        .where(GroupRole.role_id == role_id, GroupRole.permission_key == "rw")
    )
    return list(result.scalars().all())


@router.put("/{role_id}/groups", status_code=status.HTTP_204_NO_CONTENT)
async def assign_group_role(
    role_id: int, body: GroupRoleAssignment, admin: AdminUser, session: DbSession
) -> None:
    role = await session.get(Roles, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    existing = await session.get(GroupRole, (role_id, body.group_id, body.permission_key))
    ts = now()
    if existing is None:
        session.add(
            GroupRole(
                role_id=role_id,
                group_id=body.group_id,
                permission_key=body.permission_key,
                permission_value=body.permission_value,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
    else:
        existing.permission_value = body.permission_value
        existing.change_time = ts
        existing.change_by = admin.id
    await invalidate_znuny_cache_types(session, GROUP_ROLE_CACHE_TYPES)
    await session.commit()


@router.delete(
    "/{role_id}/groups/{group_id}/{permission_key}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_group_role(
    role_id: int,
    group_id: int,
    permission_key: str,
    admin: AdminUser,
    session: DbSession,
) -> None:
    _ = admin
    existing = await session.get(GroupRole, (role_id, group_id, permission_key))
    if existing is not None:
        await session.delete(existing)
        await invalidate_znuny_cache_types(session, GROUP_ROLE_CACHE_TYPES)
        await session.commit()
