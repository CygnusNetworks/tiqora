"""Tiqora URLs and branding for inherited notification templates.

Normalize template syntax before expansion, never customer-provided values.
Database templates remain untouched, including when shared with Znuny.
"""

from __future__ import annotations

import re
from email.utils import formataddr
from urllib.parse import SplitResult, urlsplit

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.placeholder import PlaceholderContext
from tiqora.config import Settings
from tiqora.domain.settings_store import (
    KEY_NOTIFICATION_SENDER_EMAIL,
    KEY_NOTIFICATION_SENDER_NAME,
    get_setting,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_ENCODED_TAG = re.compile(r"&lt;((?:OTRS|TIQORA)_[A-Za-z0-9_:]+(?:\[\d+\])?)&gt;", re.IGNORECASE)
_PREFIX = r"<(?:OTRS|TIQORA)_CONFIG_"
_UNRESOLVED_TAG = re.compile(r"<(?:OTRS|TIQORA)_", re.IGNORECASE)
_TIQORA_SENDER_NAME = "Tiqora Notifications"
_LEGACY_SEPARATOR = r"(?:\\?;|&amp;|&)"
_LEGACY_TICKET_URL = re.compile(
    _PREFIX
    + r"HttpType>://"
    + _PREFIX
    + r"FQDN>/"
    + _PREFIX
    + r"ScriptAlias>(?:customer|index)\.pl\?Action=(?:AgentTicketZoom|CustomerTicketZoom)"
    + _LEGACY_SEPARATOR
    + r"TicketID=<(?:OTRS|TIQORA)_TICKET_TicketID>"
    + r"(?:"
    + _LEGACY_SEPARATOR
    + r"ArticleID=<(?:OTRS|TIQORA)_TICKET_LAST_ARTICLE_ID>)?",
    re.IGNORECASE,
)


# A Znuny CGI path left in a rendered notification: the template's ticket URL
# was a variant :data:`_LEGACY_TICKET_URL` does not know, so the link now carries
# the Tiqora host with a Znuny path and 404s.
_LEGACY_CGI_PATH = re.compile(r"(?:index|customer)\.pl\?Action=", re.IGNORECASE)


def has_untranslated_legacy_link(rendered: str) -> bool:
    """True when a rendered notification still contains a Znuny CGI link."""
    return _LEGACY_CGI_PATH.search(rendered) is not None


def normalize_notification_template(template: str) -> str:
    template = _ENCODED_TAG.sub(lambda m: f"<{m[1]}>", template)
    return _LEGACY_TICKET_URL.sub("<TIQORA_TICKET_URL>", template)


def _public_base(settings: Settings) -> tuple[str, SplitResult]:
    """Public browser origin for ticket links: ``TIQORA_PUBLIC_BASE_URL``, else the
    first HTTP(S) CORS origin (same convention as password-setup links).

    Raises when neither yields an absolute http(s) URL — a notification with a
    dead link is worse than a loudly failed (and counted) send.
    """
    candidates = [settings.public_base_url, *settings.cors_origin_list]
    for candidate in candidates:
        base = (candidate or "").strip().rstrip("/")
        url = urlsplit(base)
        if url.scheme in {"http", "https"} and url.netloc:
            return base, url
    raise ValueError("Notifications require TIQORA_PUBLIC_BASE_URL or an HTTP(S) CORS origin")


async def configure_notification_context(
    context: PlaceholderContext,
    sysconfig: SysConfig,
    settings: Settings,
    *,
    ticket_id: int,
    recipient_kind: str,
) -> None:
    base, url = _public_base(settings)
    route = "agent" if recipient_kind == "Agent" else "portal"
    context.ticket["url"] = f"{base}/{route}/tickets/{ticket_id}"
    sender_name = await sysconfig.get_str("NotificationSenderName")
    if not sender_name or sender_name.strip() in {"OTRS Notifications", "Znuny Notifications"}:
        sender_name = _TIQORA_SENDER_NAME
    context.config_overrides.update(
        {
            "notificationsendername": sender_name,
            "fqdn": url.netloc,
            "httptype": url.scheme,
        }
    )


async def resolve_notification_sender(
    session: AsyncSession, sysconfig: SysConfig, context: PlaceholderContext
) -> str | None:
    """``From:`` for a notification mail, or ``None`` when nothing is deliverable.

    In order: the ``notification.sender_email`` override, then Znuny's
    ``NotificationSenderEmail`` when it holds a usable address, then the ticket
    queue's system address. Znuny ships the setting as
    ``znuny@<OTRS_CONFIG_FQDN>``; resolving that against the Tiqora host would
    invent a mailbox nobody reads, and a sender on a host that does not accept
    mail is what relays reject.

    The queue address is a last resort, not a good default: it is the address
    ordinary ticket correspondence goes out as, so falling back to it makes
    notifications indistinguishable from replies for every mail filter sorting
    by sender. Hence the warning -- an install landing here wants an override.
    """
    override_name = (await get_setting(session, KEY_NOTIFICATION_SENDER_NAME) or "").strip()
    name = (
        override_name
        or context.config_overrides.get("notificationsendername")
        or _TIQORA_SENDER_NAME
    )
    override_email = (await get_setting(session, KEY_NOTIFICATION_SENDER_EMAIL) or "").strip()
    if override_email:
        return formataddr((name, override_email))

    configured = (await sysconfig.get_str("NotificationSenderEmail")).strip()
    email = configured if configured and not _UNRESOLVED_TAG.search(configured) else ""
    if not email:
        email = (context.queue.get("email") or "").strip()
        if email:
            logger.warning(
                "notification_sender_falls_back_to_queue_address",
                queue_address=email,
                setting=KEY_NOTIFICATION_SENDER_EMAIL,
            )
    return formataddr((name, email)) if email else None
