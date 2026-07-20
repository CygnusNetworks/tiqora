"""Admin CRUD for users + group/role assignment."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import (
    GroupAssignment,
    RoleAssignment,
    UserCreate,
    UserOut,
    UserUpdate,
)
from tiqora.db.legacy.user import GroupUser, RoleUser, Users
from tiqora.znuny.password import hash_password

router = APIRouter(prefix="/users", tags=["admin:users"])


@router.get("", response_model=Page[UserOut])
async def list_users(admin: AdminUser, session: DbSession, params: ListParamsDep) -> Page[UserOut]:
    _ = admin
    stmt = apply_valid_filter(select(Users), Users.valid_id, params.valid).order_by(Users.login)
    return await paginate(session, UserOut, stmt, params)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, admin: AdminUser, session: DbSession) -> Users:
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, session: DbSession) -> Users:
    ts = now()
    user = Users(
        login=body.login,
        pw=hash_password(body.password),
        title=body.title,
        first_name=body.first_name,
        last_name=body.last_name,
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, body: UserUpdate, admin: AdminUser, session: DbSession
) -> Users:
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        password = data.pop("password")
        if password:
            user.pw = hash_password(password)
    for field, value in data.items():
        setattr(user, field, value)
    user.change_time = now()
    user.change_by = admin.id
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate (``valid_id = 2``) — Znuny never hard-deletes users."""
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.valid_id = 2
    user.change_time = now()
    user.change_by = admin.id
    await session.commit()


@router.put("/{user_id}/groups", status_code=status.HTTP_204_NO_CONTENT)
async def assign_group(
    user_id: int, body: GroupAssignment, admin: AdminUser, session: DbSession
) -> None:
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = await session.get(GroupUser, (user_id, body.group_id, body.permission_key))
    ts = now()
    if existing is None:
        session.add(
            GroupUser(
                user_id=user_id,
                group_id=body.group_id,
                permission_key=body.permission_key,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
    await session.commit()


@router.delete(
    "/{user_id}/groups/{group_id}/{permission_key}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_group(
    user_id: int,
    group_id: int,
    permission_key: str,
    admin: AdminUser,
    session: DbSession,
) -> None:
    _ = admin
    existing = await session.get(GroupUser, (user_id, group_id, permission_key))
    if existing is not None:
        await session.delete(existing)
        await session.commit()


@router.put("/{user_id}/roles", status_code=status.HTTP_204_NO_CONTENT)
async def assign_role(
    user_id: int, body: RoleAssignment, admin: AdminUser, session: DbSession
) -> None:
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = await session.get(RoleUser, (user_id, body.role_id))
    ts = now()
    if existing is None:
        session.add(
            RoleUser(
                user_id=user_id,
                role_id=body.role_id,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
    await session.commit()


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_role(user_id: int, role_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    existing = await session.get(RoleUser, (user_id, role_id))
    if existing is not None:
        await session.delete(existing)
        await session.commit()
