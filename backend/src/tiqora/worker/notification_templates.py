"""Tiqora URLs and branding for inherited notification templates.

Normalize template syntax before expansion, never customer-provided values.
Database templates remain untouched, including when shared with Znuny.
"""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit

from tiqora.channels.email.placeholder import PlaceholderContext
from tiqora.config import Settings
from tiqora.znuny.sysconfig import SysConfig

_ENCODED_TAG = re.compile(r"&lt;((?:OTRS|TIQORA)_[A-Za-z0-9_:]+(?:\[\d+\])?)&gt;", re.IGNORECASE)
_PREFIX = r"<(?:OTRS|TIQORA)_CONFIG_"
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
        sender_name = "Tiqora Notifications"
    context.config_overrides.update(
        {
            "notificationsendername": sender_name,
            "fqdn": url.netloc,
            "httptype": url.scheme,
        }
    )
