"""The takeover switch between Znuny's event handler and Tiqora's follower (G12).

Znuny can switch its GenericInterface event handler off only globally
(``Ticket::EventModulePost###9900-GenericInterface``), and a webservice YAML is shared, so the
flag ``daemon.gi_requester.enabled`` is global too. The "Dienste" page warns when the two sides
disagree, in both directions:

* flag off while webservices with event invokers exist: nobody sends Tiqora-made changes
  (Znuny only sees what happens in Znuny);
* flag on while Znuny's handler is still valid: every call is sent twice.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto.config import setting_is_valid
from tiqora.domain.settings_store import KEY_GI_REQUESTER_ENABLED, get_setting_bool
from tiqora.gi.events import invoker_can_fire
from tiqora.gi.loader import GiWebservices
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "NOTICE_DOUBLE_SEND",
    "NOTICE_NOT_TRIGGERED",
    "ZNUNY_GI_EVENT_HANDLER",
    "daemon_notices",
    "webservices_with_event_invokers",
    "znuny_gi_handler_active",
]

ZNUNY_GI_EVENT_HANDLER: Final = "Ticket::EventModulePost###9900-GenericInterface"

#: i18n keys of the notices on the "gi_requester" row of the Daemons page
NOTICE_NOT_TRIGGERED: Final = "admin.daemons.notices.giRequesterOff"
NOTICE_DOUBLE_SEND: Final = "admin.daemons.notices.giRequesterDouble"


async def znuny_gi_handler_active(session: AsyncSession) -> bool:
    """Whether Znuny's own event handler is switched on.

    The latest system-wide ``sysconfig_modified`` row wins (``is_valid = 0`` is how an admin
    turns the registration off), else the ``sysconfig_default`` row. A database without the
    setting at all is no Znuny that could send, so it counts as inactive.
    """
    return await setting_is_valid(session, ZNUNY_GI_EVENT_HANDLER, default=False)


async def webservices_with_event_invokers(
    session: AsyncSession, sysconfig: SysConfig, webservices: GiWebservices | None = None
) -> list[tuple[int, str]]:
    """``(id, name)`` of the valid webservices that have an invoker connected to an event.

    Only invokers the follower could fire count (supported type, usable mapping)."""
    snapshot = webservices if webservices is not None else GiWebservices()
    await snapshot.refresh(session, sysconfig)
    return [
        (ws.id, ws.name)
        for ws in snapshot.all()
        if any(invoker_can_fire(i) and i.events for i in ws.requester.invokers.values())
    ]


async def daemon_notices(
    session: AsyncSession, sysconfig: SysConfig, webservices: GiWebservices | None = None
) -> list[str]:
    """i18n keys of the warnings for the "gi_requester" row (empty when all is consistent)."""
    enabled = await get_setting_bool(session, KEY_GI_REQUESTER_ENABLED, False)
    if enabled:
        return [NOTICE_DOUBLE_SEND] if await znuny_gi_handler_active(session) else []
    if await webservices_with_event_invokers(session, sysconfig, webservices):
        return [NOTICE_NOT_TRIGGERED]
    return []
