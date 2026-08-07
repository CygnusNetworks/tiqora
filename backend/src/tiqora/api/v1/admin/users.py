"""Admin CRUD for users + group/role assignment."""

from __future__ import annotations

from typing import Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
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
    UserDeletableOut,
    UserLanguageOut,
    UserOut,
    UserReference,
    UserUpdate,
)
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.user import GroupRole, GroupUser, PermissionGroups, Roles, RoleUser, Users
from tiqora.domain.auth import normalize_language_code
from tiqora.domain.password_setup import unusable_password_hash
from tiqora.domain.schemas import UserLanguageUpdate
from tiqora.domain.user_delete import blocking_references, delete_user_rows
from tiqora.domain.user_preferences import bulk_get_preferences, get_preference, set_preference
from tiqora.domain.welcome_invite import send_setup_invite
from tiqora.domain.welcome_mail import WelcomeMailError
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
async def create_user(
    body: UserCreate, admin: AdminUser, session: DbSession, settings: AppSettings
) -> UserOut:
    ts = now()
    # No password supplied means "invite": the account gets a hash of a secret
    # nobody holds, and the agent chooses their own password through a
    # one-time link. UserCreate enforces that email is set in that case.
    invite = not body.password
    user = Users(
        login=body.login,
        pw=unusable_password_hash() if invite else hash_password(body.password or ""),
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

    if invite:
        assert body.email is not None  # noqa: S101 — enforced by UserCreate validator
        # Read the identifiers out before the try: a rollback in the handler
        # expires the ORM instance, and touching it afterwards would trigger a
        # lazy refresh from inside the error path.
        new_user_id, new_login = user.id, user.login
        try:
            await send_setup_invite(
                session,
                settings=settings,
                user_id=new_user_id,
                login=new_login,
                first_name=body.first_name,
                to_addr=body.email,
            )
        except (WelcomeMailError, OSError) as exc:
            # The account exists but has no usable password and no link out.
            # Nothing secret to hand back now (that was the point) — the admin
            # re-sends from the user list once mail works.
            await session.rollback()
            logger.warning(
                "admin.users.setup_invite_failed",
                user_id=new_user_id,
                login=new_login,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Benutzer wurde angelegt, aber die Einladungs-E-Mail konnte nicht "
                    f"gesendet werden ({exc}). Bitte den Setup-Link erneut senden."
                ),
            ) from exc
        await session.commit()

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


@router.post("/{user_id}/setup-link", status_code=status.HTTP_204_NO_CONTENT)
async def resend_setup_link(
    user_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> None:
    """Issue a fresh password-setup link and mail it.

    For the agent whose invite expired or never arrived. Supersedes any
    outstanding link for that account, so the previous mail stops working.
    """
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    email = await get_preference(session, user_id, "UserEmail")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user has no e-mail address to send the link to",
        )
    try:
        await send_setup_invite(
            session,
            settings=settings,
            user_id=user_id,
            login=user.login,
            first_name=user.first_name,
            to_addr=email,
        )
    except (WelcomeMailError, OSError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not send the setup link ({exc})",
        ) from exc
    await session.commit()


@router.get("/{user_id}/deletable", response_model=UserDeletableOut)
async def get_user_deletable(
    user_id: int, admin: AdminUser, session: DbSession
) -> UserDeletableOut:
    """Whether this agent can be hard-deleted, and what blocks it if not."""
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    blocking = await blocking_references(session, user_id)
    return UserDeletableOut(
        deletable=not blocking,
        blocking=[UserReference(table=r.table, column=r.column) for r in blocking],
    )


@router.delete("/{user_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_permanently(user_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete an agent that nothing references.

    Distinct from ``DELETE /{user_id}``, which soft-invalidates. Refuses with
    409 and the blocking tables when any row outside the agent's own settings
    still points at them — the FKs would reject the statement anyway, but a
    named list is more useful than an integrity error.
    """
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You cannot delete your own account"
        )
    blocking = await blocking_references(session, user_id)
    if blocking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "User is still referenced by: "
                + ", ".join(f"{r.table}.{r.column}" for r in blocking)
            ),
        )
    await delete_user_rows(session, user_id)
    await invalidate_znuny_cache_types(session, USER_CACHE_TYPES)
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

    # Invalid roles/groups are returned too, flagged rather than dropped: the
    # admin UI offers a valid/invalid/all filter over them, and a permission
    # that exists in the DB but grants nothing is exactly what an admin
    # debugging "why can't this agent see the queue" needs to see.
    roles = list(
        (
            await session.execute(
                select(Roles)
                .join(RoleUser, RoleUser.role_id == Roles.id)
                .where(RoleUser.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    role_ids = [r.id for r in roles]

    # group_id -> permission_key -> list of (source label, source valid_id)
    sources: dict[int, dict[PermissionKey, list[tuple[str, int]]]] = {}

    direct_rows = await session.execute(
        select(GroupUser.group_id, GroupUser.permission_key).where(GroupUser.user_id == user_id)
    )
    for group_id, key in direct_rows.all():
        if key in PERMISSION_KEYS:
            sources.setdefault(group_id, {}).setdefault(cast(PermissionKey, key), []).append(
                ("direct", 1)
            )

    if role_ids:
        roles_by_id = {r.id: r for r in roles}
        via_role_rows = await session.execute(
            select(GroupRole.group_id, GroupRole.permission_key, GroupRole.role_id).where(
                GroupRole.role_id.in_(role_ids), GroupRole.permission_value == 1
            )
        )
        for group_id, key, role_id in via_role_rows.all():
            if key in PERMISSION_KEYS:
                role = roles_by_id.get(role_id)
                label = f"Rolle: {role.name if role else role_id}"
                sources.setdefault(group_id, {}).setdefault(cast(PermissionKey, key), []).append(
                    (label, role.valid_id if role else 2)
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
        if group is None:
            continue
        flat_sources = [
            EffectivePermissionSource(key=key, via=via, valid_id=via_valid)
            for key, via_list in keys_map.items()
            for via, via_valid in via_list
        ]
        groups_out.append(
            EffectiveGroupPermission(
                group_id=group_id,
                group_name=group.name,
                valid_id=group.valid_id,
                keys=sorted(keys_map.keys()),
                sources=flat_sources,
            )
        )
    groups_out.sort(key=lambda g: g.group_name)

    queues_out: list[EffectiveQueuePermission] = []
    if group_ids:
        queue_rows = await session.execute(
            select(Queue.id, Queue.name, Queue.group_id, Queue.valid_id).where(
                Queue.group_id.in_(group_ids)
            )
        )
        for queue_id, queue_name, group_id, queue_valid_id in queue_rows.all():
            group = groups_by_id.get(group_id)
            keys_map = sources.get(group_id, {})
            if group is None or not keys_map:
                continue
            queues_out.append(
                EffectiveQueuePermission(
                    queue_id=queue_id,
                    queue_name=queue_name,
                    valid_id=queue_valid_id,
                    group_id=group_id,
                    group_name=group.name,
                    group_valid_id=group.valid_id,
                    keys=sorted(keys_map.keys()),
                )
            )
    queues_out.sort(key=lambda q: q.queue_name)

    return EffectivePermissionsOut(
        roles=[RoleOut.model_validate(r) for r in sorted(roles, key=lambda r: r.name)],
        groups=groups_out,
        queues=queues_out,
    )
