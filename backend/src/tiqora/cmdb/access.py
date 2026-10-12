"""Class-group based CMDB access (D7): a CI is as visible as its class's Znuny group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.db.tiqora.models import TiqoraCmdbClass
from tiqora.permissions.engine import PermissionEngine


@dataclass(frozen=True, slots=True)
class CmdbAccess:
    user_id: int
    is_admin: bool
    ro_group_ids: frozenset[int]
    rw_group_ids: frozenset[int]

    def can_read_group(self, group_id: int) -> bool:
        return self.is_admin or group_id in self.ro_group_ids or group_id in self.rw_group_ids

    def can_write_group(self, group_id: int) -> bool:
        return self.is_admin or group_id in self.rw_group_ids


async def load_access(session: AsyncSession, user_id: int) -> CmdbAccess:
    """``ro``/``rw`` group sets of the user (``groups_for_permission`` counts ``rw`` as ``ro``).

    With the optional ``access_group`` setting, a non-admin without ``rw`` in that group gets
    empty sets: the CMDB behaves as if no class were readable.
    """
    engine = PermissionEngine(session)
    is_admin = await engine.is_admin(user_id)
    if is_admin:
        return CmdbAccess(user_id, True, frozenset(), frozenset())
    group = (await load_cmdb_settings(session)).access_group.strip()
    if group and not await engine.has_rw_in_any_group(user_id, (group,)):
        return CmdbAccess(user_id, False, frozenset(), frozenset())
    ro = await engine.groups_for_permission(user_id, "ro")
    rw = await engine.groups_for_permission(user_id, "rw")
    return CmdbAccess(user_id, False, frozenset(ro), frozenset(rw))


async def _class_ids(session: AsyncSession, groups: frozenset[int] | None) -> list[int]:
    stmt = select(TiqoraCmdbClass.id).order_by(TiqoraCmdbClass.id)
    if groups is not None:
        if not groups:
            return []
        stmt = stmt.where(TiqoraCmdbClass.group_id.in_(sorted(groups)))
    return [int(i) for i in (await session.execute(stmt)).scalars().all()]


async def readable_class_ids(session: AsyncSession, access: CmdbAccess) -> list[int]:
    """Ids of every class (valid or not: its CIs stay readable) the user may read."""
    if access.is_admin:
        return await _class_ids(session, None)
    return await _class_ids(session, access.ro_group_ids | access.rw_group_ids)


async def writable_class_ids(session: AsyncSession, access: CmdbAccess) -> list[int]:
    if access.is_admin:
        return await _class_ids(session, None)
    return await _class_ids(session, access.rw_group_ids)


async def require_class_access(
    session: AsyncSession, access: CmdbAccess, class_id: int, perm: Literal["ro", "rw"]
) -> TiqoraCmdbClass:
    """Unknown or unreadable class: ``CmdbNotFound`` (same answer); readable but ``rw`` missing:
    ``CmdbForbidden``."""
    cls = await session.get(TiqoraCmdbClass, class_id)
    if cls is None or not access.can_read_group(cls.group_id):
        raise CmdbNotFound(f"class {class_id}")
    if perm == "rw" and not access.can_write_group(cls.group_id):
        raise CmdbForbidden(f"class {class_id}")
    return cls


__all__ = [
    "CmdbAccess",
    "load_access",
    "readable_class_ids",
    "require_class_access",
    "writable_class_ids",
]
