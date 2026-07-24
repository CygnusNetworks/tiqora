"""Per-template edit ACL for Standard Templates.

Znuny's ``standard_template`` has no permission model, so edit rights for
non-admins are expressed entirely in the tiqora-owned tables
``tiqora_standard_template_group`` (grant to a permission group) and
``tiqora_standard_template_user`` (grant to an individual agent).

An agent may edit a template iff:

* they are an admin (``rw`` on the ``admin`` group), OR
* they are in the template's user-grant set, OR
* they hold ``rw`` on any group in the template's group-grant set.

Group grants require ``rw`` on the group (same key the KB write-ACL uses) —
membership alone is not enough. A template with no ACL rows stays admin-only,
preserving today's behaviour.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import (
    TiqoraStandardTemplateGroup,
    TiqoraStandardTemplateUser,
)
from tiqora.permissions.engine import PermissionEngine


class TemplatePermissionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)

    async def may_edit(self, user_id: int, template_id: int) -> bool:
        """True if *user_id* may edit the template *template_id*."""
        if await self._perms.is_admin(user_id):
            return True
        user_granted = await self._session.scalar(
            select(TiqoraStandardTemplateUser.user_id).where(
                TiqoraStandardTemplateUser.standard_template_id == template_id,
                TiqoraStandardTemplateUser.user_id == user_id,
            )
        )
        if user_granted is not None:
            return True
        group_ids = await self._template_group_ids(template_id)
        if not group_ids:
            return False
        writable = await self._perms.groups_for_permission(user_id, "rw")
        return bool(group_ids & writable)

    async def editable_template_ids(self, user_id: int) -> set[int] | None:
        """Template ids *user_id* may edit, or ``None`` for admins (= all)."""
        if await self._perms.is_admin(user_id):
            return None
        ids: set[int] = set()
        user_rows = await self._session.execute(
            select(TiqoraStandardTemplateUser.standard_template_id).where(
                TiqoraStandardTemplateUser.user_id == user_id
            )
        )
        ids.update(r[0] for r in user_rows.all())
        writable = await self._perms.groups_for_permission(user_id, "rw")
        if writable:
            group_rows = await self._session.execute(
                select(TiqoraStandardTemplateGroup.standard_template_id).where(
                    TiqoraStandardTemplateGroup.permission_group_id.in_(writable)
                )
            )
            ids.update(r[0] for r in group_rows.all())
        return ids

    async def can_edit_any(self, user_id: int) -> bool:
        """True if *user_id* may edit at least one template (drives the /me flag)."""
        if await self._perms.is_admin(user_id):
            return True
        has_user_grant = await self._session.scalar(
            select(TiqoraStandardTemplateUser.standard_template_id)
            .where(TiqoraStandardTemplateUser.user_id == user_id)
            .limit(1)
        )
        if has_user_grant is not None:
            return True
        writable = await self._perms.groups_for_permission(user_id, "rw")
        if not writable:
            return False
        has_group_grant = await self._session.scalar(
            select(TiqoraStandardTemplateGroup.standard_template_id)
            .where(TiqoraStandardTemplateGroup.permission_group_id.in_(writable))
            .limit(1)
        )
        return has_group_grant is not None

    async def get_editors(self, template_id: int) -> tuple[list[int], list[int]]:
        """Return ``(group_ids, user_ids)`` currently granted edit on *template_id*."""
        group_rows = await self._session.execute(
            select(TiqoraStandardTemplateGroup.permission_group_id)
            .where(TiqoraStandardTemplateGroup.standard_template_id == template_id)
            .order_by(TiqoraStandardTemplateGroup.permission_group_id)
        )
        user_rows = await self._session.execute(
            select(TiqoraStandardTemplateUser.user_id)
            .where(TiqoraStandardTemplateUser.standard_template_id == template_id)
            .order_by(TiqoraStandardTemplateUser.user_id)
        )
        return [r[0] for r in group_rows.all()], [r[0] for r in user_rows.all()]

    async def set_editors(
        self, template_id: int, group_ids: list[int], user_ids: list[int]
    ) -> None:
        """Replace the edit-ACL of *template_id* (admin action)."""
        await self._session.execute(
            delete(TiqoraStandardTemplateGroup).where(
                TiqoraStandardTemplateGroup.standard_template_id == template_id
            )
        )
        await self._session.execute(
            delete(TiqoraStandardTemplateUser).where(
                TiqoraStandardTemplateUser.standard_template_id == template_id
            )
        )
        for gid in dict.fromkeys(group_ids):
            self._session.add(
                TiqoraStandardTemplateGroup(
                    standard_template_id=template_id, permission_group_id=gid
                )
            )
        for uid in dict.fromkeys(user_ids):
            self._session.add(
                TiqoraStandardTemplateUser(standard_template_id=template_id, user_id=uid)
            )
        await self._session.flush()

    async def delete_all(self, template_id: int) -> None:
        """Drop every ACL row for *template_id* (call when a template is deleted)."""
        await self.set_editors(template_id, [], [])

    async def _template_group_ids(self, template_id: int) -> set[int]:
        rows = await self._session.execute(
            select(TiqoraStandardTemplateGroup.permission_group_id).where(
                TiqoraStandardTemplateGroup.standard_template_id == template_id
            )
        )
        return {r[0] for r in rows.all()}
