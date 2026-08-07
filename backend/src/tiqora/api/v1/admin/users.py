"""Admin CRUD for users + group/role assignment."""

from __future__ import annotations

import secrets
from typing import Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    USER_CACHE_TYPES,
    USER_GROUP_CACHE_TYPES,
    USER_ROLE_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    apply_valid_filter,
    bulk_grouped_counts,
    paginate,
)
from tiqora.api.v1.admin.schemas import (
    EffectiveGroupPermission,
    EffectivePermissionSource,
    EffectivePermissionsOut,
    EffectiveQueuePermission,
    GroupAssignment,
    GroupOut,
    RoleAssignment,
    RoleOut,
    UserCreate,
    UserLanguageOut,
    UserOut,
    UserUpdate,
)
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.user import GroupRole, GroupUser, PermissionGroups, Roles, RoleUser, Users
from tiqora.domain.auth import normalize_language_code
from tiqora.domain.schemas import UserLanguageUpdate
from tiqora.domain.user_preferences import bulk_get_preferences, get_preference, set_preference
from tiqora.domain.welcome_mail import WelcomeMailError, send_transactional_email
from tiqora.permissions.engine import PERMISSION_KEYS
from tiqora.znuny.password import hash_password

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/users", tags=["admin:users"])

# Matches EffectivePermissionSource.key / GroupAssignment.permission_key in
# admin/schemas.py — narrows the DB-returned `str` once it's been checked
# against PERMISSION_KEYS, instead of re-declaring the literal per call site.
PermissionKey = Literal["ro", "move_into", "create", "note", "owner", "priority", "rw"]


def _with_preferences(user: Users, email: str | None, mobile: str | None) -> UserOut:
    out = UserOut.model_validate(user)
    out.email = email
    out.mobile = mobile
    return out


@router.get("", response_model=Page[UserOut])
async def list_users(admin: AdminUser, session: DbSession, params: ListParamsDep) -> Page[UserOut]:
    _ = admin
    stmt = apply_valid_filter(select(Users), Users.valid_id, params.valid).order_by(Users.login)
    page = await paginate(session, UserOut, stmt, params)
    ids = [u.id for u in page.items]
    emails = await bulk_get_preferences(session, ids, "UserEmail")
    mobiles = await bulk_get_preferences(session, ids, "UserMobile")
    for u in page.items:
        u.email = emails.get(u.id)
        u.mobile = mobiles.get(u.id)
    return page


@router.get("/assignment-counts", response_model=dict[int, int])
async def user_assignment_counts(
    admin: AdminUser,
    session: DbSession,
    side: Literal["groups", "roles"] = Query(...),
) -> dict[int, int]:
    """Bulk assignment counts keyed by user id (for AssignmentEditor badges).

    Group counts use ``permission_key='rw'`` to match the Agent↔Groups editor.
    """
    _ = admin
    if side == "groups":
        return await bulk_grouped_counts(
            session,
            GroupUser.user_id,
            GroupUser.permission_key == "rw",
        )
    return await bulk_grouped_counts(session, RoleUser.user_id)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, admin: AdminUser, session: DbSession) -> UserOut:
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    email = await get_preference(session, user_id, "UserEmail")
    mobile = await get_preference(session, user_id, "UserMobile")
    return _with_preferences(user, email, mobile)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, session: DbSession) -> UserOut:
    ts = now()
    generated_password: str | None = None
    password = body.password
    if not password:
        # UserCreate enforces email is set whenever password is omitted.
        generated_password = secrets.token_urlsafe(12)
        password = generated_password

    user = Users(
        login=body.login,
        pw=hash_password(password),
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
    await session.flush()  # assigns user.id for the preference rows below

    if body.email:
        await set_preference(session, user.id, "UserEmail", body.email)
    if body.mobile:
        await set_preference(session, user.id, "UserMobile", body.mobile)

    await invalidate_znuny_cache_types(session, USER_CACHE_TYPES)
    await session.commit()
    await session.refresh(user)

    if generated_password is not None:
        assert body.email is not None  # noqa: S101 — enforced by UserCreate validator
        try:
            await send_transactional_email(
                session,
                to_addr=body.email,
                subject="Ihr Tiqora-Zugang wurde angelegt",
                body=(
                    f"Hallo {body.first_name},\n\n"
                    "für Sie wurde ein Tiqora-Zugang angelegt.\n\n"
                    f"Login: {body.login}\n"
                    f"Passwort: {generated_password}\n\n"
                    "Bitte melden Sie sich an und ändern Sie Ihr Passwort."
                ),
            )
        except (WelcomeMailError, OSError) as exc:
            logger.warning(
                "admin.users.welcome_mail_failed", user_id=user.id, login=user.login, error=str(exc)
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Benutzer wurde angelegt, aber die Willkommens-E-Mail konnte nicht "
                    f"gesendet werden ({exc}). Generiertes Passwort: {generated_password}"
                ),
            ) from exc

    return _with_preferences(user, body.email or None, body.mobile or None)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, body: UserUpdate, admin: AdminUser, session: DbSession
) -> UserOut:
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    data = body.model_dump(exclude_unset=True)
    email_set = "email" in data
    email = data.pop("email", None)
    mobile_set = "mobile" in data
    mobile = data.pop("mobile", None)
    if "password" in data:
        password = data.pop("password")
        if password:
            user.pw = hash_password(password)
    for field, value in data.items():
        setattr(user, field, value)
    user.change_time = now()
    user.change_by = admin.id

    if email_set:
        await set_preference(session, user_id, "UserEmail", email)
    if mobile_set:
        await set_preference(session, user_id, "UserMobile", mobile)

    await invalidate_znuny_cache_types(session, USER_CACHE_TYPES)
    await session.commit()
    await session.refresh(user)

    out_email = (
        (email or None) if email_set else await get_preference(session, user_id, "UserEmail")
    )
    out_mobile = (
        (mobile or None) if mobile_set else await get_preference(session, user_id, "UserMobile")
    )
    return _with_preferences(user, out_email, out_mobile)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(user_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate (``valid_id = 2``) — Znuny never hard-deletes users."""
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.valid_id = 2
    user.change_time = now()
    user.change_by = admin.id
    await invalidate_znuny_cache_types(session, USER_CACHE_TYPES)
    await session.commit()


@router.get("/{user_id}/groups", response_model=list[GroupOut])
async def get_user_groups(
    user_id: int, admin: AdminUser, session: DbSession
) -> list[PermissionGroups]:
    """Groups the user has full (``rw``) access to — the Agent↔Groups editor's
    read side. The editor toggles the ``rw`` permission only (see
    :func:`assign_group`), so the read set is filtered to that key to stay
    consistent with what the checkboxes write."""
    _ = admin
    result = await session.execute(
        select(PermissionGroups)
        .join(GroupUser, GroupUser.group_id == PermissionGroups.id)
        .where(GroupUser.user_id == user_id, GroupUser.permission_key == "rw")
    )
    return list(result.scalars().all())


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
    await invalidate_znuny_cache_types(session, USER_GROUP_CACHE_TYPES)
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
        await invalidate_znuny_cache_types(session, USER_GROUP_CACHE_TYPES)
        await session.commit()


@router.get("/{user_id}/roles", response_model=list[RoleOut])
async def get_user_roles(user_id: int, admin: AdminUser, session: DbSession) -> list[Roles]:
    """Roles currently granted to *user_id* (for the assignment editor)."""
    _ = admin
    result = await session.execute(
        select(Roles)
        .join(RoleUser, RoleUser.role_id == Roles.id)
        .where(RoleUser.user_id == user_id)
    )
    return list(result.scalars().all())


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
    await invalidate_znuny_cache_types(session, USER_ROLE_CACHE_TYPES)
    await session.commit()


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_role(user_id: int, role_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    existing = await session.get(RoleUser, (user_id, role_id))
    if existing is not None:
        await session.delete(existing)
        await invalidate_znuny_cache_types(session, USER_ROLE_CACHE_TYPES)
        await session.commit()


@router.get("/{user_id}/language", response_model=UserLanguageOut)
async def get_user_language(user_id: int, admin: AdminUser, session: DbSession) -> UserLanguageOut:
    """Admin-facing mirror of the agent's own 'Persönliche Einstellungen'
    language choice (``UserLanguage`` preference) — read side."""
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserLanguageOut(language=await get_preference(session, user_id, "UserLanguage"))


@router.put("/{user_id}/language", response_model=UserLanguageOut)
async def set_user_language(
    user_id: int, body: UserLanguageUpdate, admin: AdminUser, session: DbSession
) -> UserLanguageOut:
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    code = normalize_language_code(body.language)
    if code is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported language code: {body.language!r}",
        )
    await set_preference(session, user_id, "UserLanguage", code)
    await session.commit()
    return UserLanguageOut(language=code)


@router.get("/{user_id}/effective-permissions", response_model=EffectivePermissionsOut)
async def get_effective_permissions(
    user_id: int, admin: AdminUser, session: DbSession
) -> EffectivePermissionsOut:
    """Read-only breakdown of a user's resolved group/queue permissions.

    Union of direct ``group_user`` grants and role-derived ``group_role``
    grants (same rule as :class:`tiqora.permissions.engine.PermissionEngine`,
    reimplemented here to additionally track *where* each permission key
    came from for display)."""
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    roles = list(
        (
            await session.execute(
                select(Roles)
                .join(RoleUser, RoleUser.role_id == Roles.id)
                .where(RoleUser.user_id == user_id, Roles.valid_id == 1)
            )
        )
        .scalars()
        .all()
    )
    role_ids = [r.id for r in roles]

    # group_id -> permission_key -> list of source labels
    sources: dict[int, dict[PermissionKey, list[str]]] = {}

    direct_rows = await session.execute(
        select(GroupUser.group_id, GroupUser.permission_key).where(GroupUser.user_id == user_id)
    )
    for group_id, key in direct_rows.all():
        if key in PERMISSION_KEYS:
            sources.setdefault(group_id, {}).setdefault(cast(PermissionKey, key), []).append(
                "direct"
            )

    if role_ids:
        role_names = {r.id: r.name for r in roles}
        via_role_rows = await session.execute(
            select(GroupRole.group_id, GroupRole.permission_key, GroupRole.role_id).where(
                GroupRole.role_id.in_(role_ids), GroupRole.permission_value == 1
            )
        )
        for group_id, key, role_id in via_role_rows.all():
            if key in PERMISSION_KEYS:
                label = f"Rolle: {role_names.get(role_id, role_id)}"
                sources.setdefault(group_id, {}).setdefault(cast(PermissionKey, key), []).append(
                    label
                )

    group_ids = list(sources.keys())
    groups_by_id: dict[int, PermissionGroups] = {}
    if group_ids:
        group_rows = await session.execute(
            select(PermissionGroups).where(PermissionGroups.id.in_(group_ids))
        )
        groups_by_id = {g.id: g for g in group_rows.scalars().all()}

    groups_out: list[EffectiveGroupPermission] = []
    for group_id, keys_map in sources.items():
        group = groups_by_id.get(group_id)
        if group is None or group.valid_id != 1:
            continue
        flat_sources = [
            EffectivePermissionSource(key=key, via=via)
            for key, via_list in keys_map.items()
            for via in via_list
        ]
        groups_out.append(
            EffectiveGroupPermission(
                group_id=group_id,
                group_name=group.name,
                keys=sorted(keys_map.keys()),
                sources=flat_sources,
            )
        )
    groups_out.sort(key=lambda g: g.group_name)

    queues_out: list[EffectiveQueuePermission] = []
    if group_ids:
        queue_rows = await session.execute(
            select(Queue.id, Queue.name, Queue.group_id).where(Queue.group_id.in_(group_ids))
        )
        for queue_id, queue_name, group_id in queue_rows.all():
            group = groups_by_id.get(group_id)
            keys_map = sources.get(group_id, {})
            if group is None or not keys_map:
                continue
            queues_out.append(
                EffectiveQueuePermission(
                    queue_id=queue_id,
                    queue_name=queue_name,
                    group_id=group_id,
                    group_name=group.name,
                    keys=sorted(keys_map.keys()),
                )
            )
    queues_out.sort(key=lambda q: q.queue_name)

    return EffectivePermissionsOut(
        roles=[RoleOut.model_validate(r) for r in sorted(roles, key=lambda r: r.name)],
        groups=groups_out,
        queues=queues_out,
    )
