"""Groups an agent may share a saved report / search with."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.user import PermissionGroups
from tiqora.permissions.engine import PermissionEngine


@dataclass(frozen=True, slots=True)
class ShareGroup:
    id: int
    name: str


class ShareGroupError(ValueError):
    """A share-group choice is not allowed (German message, API path ``group_ids``)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = "group_ids"


async def share_groups_for(session: AsyncSession, user_id: int) -> list[ShareGroup]:
    """Admin: every valid group; otherwise the valid groups the user holds ``ro`` (or ``rw``) on."""
    engine = PermissionEngine(session)
    stmt = select(PermissionGroups.id, PermissionGroups.name).where(PermissionGroups.valid_id == 1)
    if not await engine.is_admin(user_id):
        ids = await engine.groups_for_permission(user_id, "ro")
        if not ids:
            return []
        stmt = stmt.where(PermissionGroups.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    return sorted((ShareGroup(int(i), str(n)) for i, n in rows), key=lambda g: (g.name, g.id))


async def check_share_groups(
    session: AsyncSession, user_id: int, group_ids: Sequence[int]
) -> tuple[int, ...]:
    """Deduplicated sorted ids; any group the user may not share with raises."""
    wanted = sorted(set(group_ids))
    allowed = {g.id for g in await share_groups_for(session, user_id)}
    for gid in wanted:
        if gid not in allowed:
            raise ShareGroupError(f"Mit der Gruppe {gid} kann nicht geteilt werden.")
    return tuple(wanted)
