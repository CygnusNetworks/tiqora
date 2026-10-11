"""What an agent may do with reports: view, save private ones, share, administer.

Deviation 1 of the reporting-builder plan: with ``reporting.access = all_agents`` every
agent may save *private* reports; sharing (groups / everyone) always needs ``rw`` on the
Znuny ``stats`` group or admin. With ``stats_group`` viewing needs ``ro`` and every save
needs ``rw`` on that group.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.user import PermissionGroups
from tiqora.permissions.engine import ADMIN_GROUP_NAME, PermissionEngine
from tiqora.reporting.access import STATS_GROUP_NAME
from tiqora.reporting.settings import ReportingSettings


@dataclass(frozen=True, slots=True)
class ReportRights:
    view: bool  # has_report_access (unchanged wave-1 rule)
    create: bool  # save PRIVATE reports (builder "Speichern")
    share: bool  # visibility "groups" / "all"
    admin: bool  # PermissionEngine.is_admin


async def report_rights(
    session: AsyncSession, user_id: int, reporting: ReportingSettings
) -> ReportRights:
    """All rights from ONE ``queue_permissions`` evaluation (``/auth/me`` runs on every page)."""
    perms = await PermissionEngine(session).queue_permissions(user_id)
    rows = await session.execute(
        select(PermissionGroups.id, PermissionGroups.name).where(
            PermissionGroups.name.in_((ADMIN_GROUP_NAME, STATS_GROUP_NAME)),
            PermissionGroups.valid_id == 1,
        )
    )
    ids = {name: gid for gid, name in rows.all()}
    admin_id = ids.get(ADMIN_GROUP_NAME)
    stats_id = ids.get(STATS_GROUP_NAME)
    admin = admin_id is not None and "rw" in perms.get(admin_id, set())
    stats_keys = perms.get(stats_id, set()) if stats_id is not None else set()
    ro = bool(stats_keys & {"ro", "rw"})
    rw = "rw" in stats_keys
    view = reporting.access == "all_agents" or admin or ro
    create = view and (reporting.access == "all_agents" or admin or rw)
    share = view and (admin or rw)
    return ReportRights(view=view, create=create, share=share, admin=admin)
