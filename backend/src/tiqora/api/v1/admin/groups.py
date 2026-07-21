"""Admin CRUD for permission groups (Znuny table `permission_groups`, a.k.a. "groups")."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import GROUP_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    apply_valid_filter,
    bulk_grouped_counts,
    paginate,
)
from tiqora.api.v1.admin.schemas import (
    CustomerUserAdminOut,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    RoleOut,
    UserOut,
)
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import (
    GroupCustomerUser,
    GroupRole,
    GroupUser,
    PermissionGroups,
    Roles,
    Users,
)

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


@router.get("/assignment-counts", response_model=dict[int, int])
async def group_assignment_counts(
    admin: AdminUser,
    session: DbSession,
    side: Literal["users", "roles"] = Query(...),
) -> dict[int, int]:
    """Bulk assignment counts keyed by group id (for AssignmentEditor badges).

    Mirrors the editor filters: agent memberships use ``permission_key='rw'``;
    role grants use ``permission_key='rw'`` and ``permission_value=1``.
    """
    _ = admin
    if side == "users":
        return await bulk_grouped_counts(
            session,
            GroupUser.group_id,
            GroupUser.permission_key == "rw",
        )
    return await bulk_grouped_counts(
        session,
        GroupRole.group_id,
        GroupRole.permission_key == "rw",
        GroupRole.permission_value == 1,
    )


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
    await invalidate_znuny_cache_types(session, GROUP_CACHE_TYPES)
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
    await invalidate_znuny_cache_types(session, GROUP_CACHE_TYPES)
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
    await invalidate_znuny_cache_types(session, GROUP_CACHE_TYPES)
    await session.commit()


# --- Reverse relation reads (group as anchor) --------------------------------


@router.get("/{group_id}/users", response_model=list[UserOut])
async def get_group_users(group_id: int, admin: AdminUser, session: DbSession) -> list[Users]:
    """Agents with full (``rw``) access to *group_id* — reverse of user↔groups."""
    _ = admin
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(Users)
        .join(GroupUser, GroupUser.user_id == Users.id)
        .where(GroupUser.group_id == group_id, GroupUser.permission_key == "rw")
        .order_by(Users.login)
    )
    return list(result.scalars().all())


@router.get("/{group_id}/roles", response_model=list[RoleOut])
async def get_group_roles(group_id: int, admin: AdminUser, session: DbSession) -> list[Roles]:
    """Roles that grant full (``rw``) access to *group_id* — reverse of role↔groups."""
    _ = admin
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(Roles)
        .join(GroupRole, GroupRole.role_id == Roles.id)
        .where(
            GroupRole.group_id == group_id,
            GroupRole.permission_key == "rw",
            GroupRole.permission_value == 1,
        )
        .order_by(Roles.name)
    )
    return list(result.scalars().all())


@router.get("/{group_id}/customer-users", response_model=list[CustomerUserAdminOut])
async def get_group_customer_users(
    group_id: int, admin: AdminUser, session: DbSession
) -> list[CustomerUser]:
    """Customer users with full (``rw``) access to *group_id* — reverse of
    customer-user↔groups.

    Znuny stores the customer-user identity as the *login string* in
    ``group_customer_user.user_id`` (not the numeric ``customer_user.id``).
    """
    _ = admin
    group = await session.get(PermissionGroups, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    result = await session.execute(
        select(CustomerUser)
        .join(GroupCustomerUser, GroupCustomerUser.user_id == CustomerUser.login)
        .where(
            GroupCustomerUser.group_id == group_id,
            GroupCustomerUser.permission_key == "rw",
            GroupCustomerUser.permission_value == 1,
        )
        .order_by(CustomerUser.login)
    )
    return list(result.scalars().all())
