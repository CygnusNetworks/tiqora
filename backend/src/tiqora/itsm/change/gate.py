"""Where the change module is available and who may do what (everything is a read).

``available`` needs Mode B (``tiqora``) and the setup group ``change`` complete (the two ticket
types and the two fixed dynamic fields). In Mode A the Znuny changes are only shown (``legacy``
"live"); in Mode B a legacy archive is shown when the importer has stored one ("archive").

The module switches beat all of that: ``itsm_change.enabled`` off (``change_off``) or
``approvals.enabled`` off (``approvals_off``: the approval phases need the engine) makes the module
unavailable in every mode, the legacy views included.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.settings import approvals_enabled
from tiqora.config import Settings
from tiqora.db.legacy.user import PermissionGroups
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import TiqoraItsmLegacyChange
from tiqora.itsm.change.settings import ItsmChangeSettings, change_enabled, load_change_settings
from tiqora.itsm.mode import ItsmMode, get_itsm_mode
from tiqora.itsm.settings import load_itsm_settings
from tiqora.itsm.setup import setup_group_complete
from tiqora.permissions.engine import PermissionEngine

SwitchReason = Literal["change_off", "approvals_off"]
Reason = Literal["ok", "itsm_off", "mode_a", "setup_missing", "change_off", "approvals_off"]
Legacy = Literal["none", "live", "archive"]


@dataclass(frozen=True, slots=True)
class ChangeAvailability:
    mode: ItsmMode
    available: bool  # mode == "tiqora" and setup group "change" complete
    reason: Reason
    legacy: Legacy  # Mode A + change tables -> live; Mode B + archive rows -> archive
    can_manage: bool  # is_change_manager
    can_see_legacy: bool
    setup_complete_change: bool


async def change_switched_off(session: AsyncSession) -> SwitchReason | None:
    """Why a module switch keeps change management off, ``None`` while both are on."""
    if not await change_enabled(session):
        return "change_off"
    if not await approvals_enabled(session):
        return "approvals_off"
    return None


async def is_change_manager(session: AsyncSession, user_id: int, cfg: ItsmChangeSettings) -> bool:
    """Admin, or ``rw`` in the configured manager group (empty setting: admins only)."""
    engine = PermissionEngine(session)
    if await engine.is_admin(user_id):
        return True
    group = cfg.manager_group.strip()
    return bool(group) and await engine.has_rw_in_any_group(user_id, (group,))


async def can_see_legacy(session: AsyncSession, user_id: int, cfg: ItsmChangeSettings) -> bool:
    """Admin, or ``ro`` in the legacy group.

    When that group does not exist (not set, or removed from Znuny after the cutover) the change
    managers may read the legacy changes as well, so the archive does not become admin-only.
    """
    engine = PermissionEngine(session)
    if await engine.is_admin(user_id):
        return True
    name = cfg.legacy_change_group.strip()
    group_id = (
        (
            await session.execute(
                select(PermissionGroups.id).where(
                    PermissionGroups.name == name, PermissionGroups.valid_id == 1
                )
            )
        ).scalar_one_or_none()
        if name
        else None
    )
    if group_id is None:
        return await is_change_manager(session, user_id, cfg)
    return int(group_id) in await engine.groups_for_permission(user_id, "ro")


async def change_availability(
    session: AsyncSession, settings: Settings, *, user_id: int
) -> ChangeAvailability:
    state = await get_itsm_mode(session)
    cfg = await load_change_settings(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)
    complete = state.mode == "tiqora" and await setup_group_complete(session, itsm_cfg, "change")
    switched_off = await change_switched_off(session)
    reason: Reason
    if switched_off is not None:
        reason = switched_off
    elif state.mode == "off":
        reason = "itsm_off"
    elif state.mode == "znuny_readthrough":
        reason = "mode_a"
    else:
        reason = "ok" if complete else "setup_missing"
    legacy: Legacy = "none"
    if switched_off is not None:
        pass  # the stored archive stays, only the views go
    elif state.mode == "znuny_readthrough" and state.detected.change_installed:
        legacy = "live"
    elif state.mode == "tiqora":
        first = (await session.execute(select(TiqoraItsmLegacyChange.id).limit(1))).first()
        if first is not None:
            legacy = "archive"
    return ChangeAvailability(
        mode=state.mode,
        available=reason == "ok",
        reason=reason,
        legacy=legacy,
        can_manage=await is_change_manager(session, user_id, cfg),
        can_see_legacy=switched_off is None and await can_see_legacy(session, user_id, cfg),
        setup_complete_change=complete,
    )


async def require_change_mode(session: AsyncSession, settings: Settings) -> None:
    """404 while a module switch or ITSM is off, 409 in Mode A and while the change setup is
    incomplete."""
    if await change_switched_off(session) is not None:
        raise ChangeError("change_unavailable", 404)
    state = await get_itsm_mode(session)
    if state.mode == "off":
        raise ChangeError("change_unavailable", 404)
    if state.mode == "znuny_readthrough":
        raise ChangeError("change_mode_a", 409)
    itsm_cfg = await load_itsm_settings(session, settings)
    if not await setup_group_complete(session, itsm_cfg, "change"):
        raise ChangeError("change_setup_missing", 409)
