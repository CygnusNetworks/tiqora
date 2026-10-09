"""Who may run reports (``reporting.access``).

``all_agents`` (default): every agent; what a report shows is still limited to
the agent's ``ro`` queues by the compiler. ``stats_group``: Znuny parity —
admins, or agents with ``ro`` (``rw`` implies it) on the valid group named
``stats``.

The plan's ``require_report_access(session, user)`` takes the user id and the
loaded settings instead: the background jobs only have a user id, and the
settings section needs the app settings to load.
"""

from __future__ import annotations

from typing import Final

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.user import PermissionGroups
from tiqora.permissions.engine import PermissionEngine
from tiqora.reporting.settings import ReportingSettings

STATS_GROUP_NAME: Final = "stats"
NO_REPORT_ACCESS_MESSAGE: Final = "Für Berichte fehlt die Berechtigung (Gruppe „stats“)."


async def has_report_access(
    session: AsyncSession, user_id: int, reporting: ReportingSettings
) -> bool:
    if reporting.access == "all_agents":
        return True
    engine = PermissionEngine(session)
    if await engine.is_admin(user_id):
        return True
    stats_id = (
        await session.execute(
            select(PermissionGroups.id).where(
                PermissionGroups.name == STATS_GROUP_NAME, PermissionGroups.valid_id == 1
            )
        )
    ).scalar_one_or_none()
    if stats_id is None:
        return False
    return stats_id in await engine.groups_for_permission(user_id, "ro")


async def require_report_access(
    session: AsyncSession, user_id: int, reporting: ReportingSettings
) -> None:
    """Raise 403 ``{"message": ...}`` when the user may not run reports."""
    if not await has_report_access(session, user_id, reporting):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"message": NO_REPORT_ACCESS_MESSAGE}
        )
