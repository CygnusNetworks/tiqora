"""The bare address system mails (reports, approvals) are sent from."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.mail_address import normalise_address, usable_sender_email
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, get_setting
from tiqora.znuny.sysconfig import SysConfig


async def resolve_notification_sender_address(session: AsyncSession) -> str | None:
    """Bare sender address: ``notification.sender_email``, else Znuny's
    ``NotificationSenderEmail`` when it is a real address; ``None`` when neither is."""
    override = usable_sender_email(await get_setting(session, KEY_NOTIFICATION_SENDER_EMAIL))
    configured = usable_sender_email(await SysConfig(session).get_str("NotificationSenderEmail"))
    for candidate in (override, configured):
        if candidate:
            address = normalise_address(candidate)
            if address:
                return address
    return None
