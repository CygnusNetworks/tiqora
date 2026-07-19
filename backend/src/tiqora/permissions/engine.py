"""Znuny group/role permission engine.

Resolves an agent's effective queue-group permissions as the union of:

* direct ``group_user`` rows (permission_key per group)
* role-derived ``group_role`` rows (``permission_value = 1``) via ``role_user``

Only ``valid_id = 1`` users, groups, and roles contribute. Permission key
``rw`` implies every other key at check time (see ``PermissionUserGroupGet``
in ``Kernel/System/Group.pm``).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.user import (
    GroupRole,
    GroupUser,
    PermissionGroups,
    Roles,
    RoleUser,
    Users,
)

# Znuny System::Permission defaults plus rw (always present).
PERMISSION_KEYS: Final[frozenset[str]] = frozenset(
    {"ro", "move_into", "create", "note", "owner", "priority", "rw"}
)

# Znuny convention: membership in the group literally named "admin" with
# ``rw`` permission (direct group_user or via role → group_role) grants
# administrator rights. There is no separate "is_admin" flag on `users`.
ADMIN_GROUP_NAME: Final[str] = "admin"


class PermissionEngine:
    """Async permission resolution against a Znuny-compatible database."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue_permissions(self, user_id: int) -> dict[int, set[str]]:
        """Return ``{group_id: set(permission_keys)}`` for a valid user.

        Empty dict if the user is missing or invalid.
        """
        if not await self._user_is_valid(user_id):
            return {}

        valid_groups = await self._valid_group_ids()
        perms: dict[int, set[str]] = defaultdict(set)

        # Direct group_user memberships
        gu_result = await self._session.execute(
            select(GroupUser.group_id, GroupUser.permission_key).where(GroupUser.user_id == user_id)
        )
        for group_id, key in gu_result.all():
            if group_id in valid_groups and key in PERMISSION_KEYS:
                perms[group_id].add(key)

        # Roles → group_role
        role_result = await self._session.execute(
            select(RoleUser.role_id)
            .join(Roles, Roles.id == RoleUser.role_id)
            .where(RoleUser.user_id == user_id, Roles.valid_id == 1)
        )
        role_ids = [row[0] for row in role_result.all()]
        if role_ids:
            gr_result = await self._session.execute(
                select(GroupRole.group_id, GroupRole.permission_key).where(
                    GroupRole.role_id.in_(role_ids),
                    GroupRole.permission_value == 1,
                )
            )
            for group_id, key in gr_result.all():
                if group_id in valid_groups and key in PERMISSION_KEYS:
                    perms[group_id].add(key)

        return dict(perms)

    async def check(self, user_id: int, queue_id: int, perm: str) -> bool:
        """Return True if *user_id* has *perm* on the group owning *queue_id*.

        ``rw`` on the group satisfies any permission key.
        """
        if perm not in PERMISSION_KEYS:
            return False

        queue_result = await self._session.execute(
            select(Queue.group_id).where(Queue.id == queue_id)
        )
        group_id = queue_result.scalar_one_or_none()
        if group_id is None:
            return False

        perms = await self.queue_permissions(user_id)
        keys = perms.get(group_id)
        if not keys:
            return False
        if "rw" in keys:
            return True
        return perm in keys

    async def groups_for_permission(self, user_id: int, perm: str) -> set[int]:
        """Group IDs where the user holds *perm* (including via ``rw``)."""
        if perm not in PERMISSION_KEYS:
            return set()
        perms = await self.queue_permissions(user_id)
        out: set[int] = set()
        for group_id, keys in perms.items():
            if "rw" in keys or perm in keys:
                out.add(group_id)
        return out

    async def is_admin(self, user_id: int) -> bool:
        """Return True if *user_id* has ``rw`` on the ``admin`` group.

        Znuny semantics: no dedicated admin flag exists on ``users``; the
        "admin" role is membership (direct ``group_user`` or via
        ``role_user`` → ``group_role``) in the group literally named
        ``admin`` with the ``rw`` permission key (``rw`` implies all other
        keys, so no narrower key qualifies as "admin" here).
        """
        group_result = await self._session.execute(
            select(PermissionGroups.id).where(
                PermissionGroups.name == ADMIN_GROUP_NAME,
                PermissionGroups.valid_id == 1,
            )
        )
        admin_group_id = group_result.scalar_one_or_none()
        if admin_group_id is None:
            return False

        perms = await self.queue_permissions(user_id)
        return "rw" in perms.get(admin_group_id, set())

    async def _user_is_valid(self, user_id: int) -> bool:
        result = await self._session.execute(
            select(Users.id).where(Users.id == user_id, Users.valid_id == 1)
        )
        return result.scalar_one_or_none() is not None

    async def _valid_group_ids(self) -> set[int]:
        result = await self._session.execute(
            select(PermissionGroups.id).where(PermissionGroups.valid_id == 1)
        )
        return set(result.scalars().all())
