"""Tiqora URLs and branding for inherited notification templates.

Normalize template syntax before expansion, never customer-provided values.
Database templates remain untouched, including when shared with Znuny.
"""

from __future__ import annotations

import re
from email.utils import formataddr
from urllib.parse import SplitResult, urlsplit

from tiqora.channels.email.placeholder import PlaceholderContext
from tiqora.config import Settings
from tiqora.znuny.sysconfig import SysConfig

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
    sysconfig: SysConfig, context: PlaceholderContext
) -> str | None:
    """``From:`` for a notification mail, or ``None`` when nothing is deliverable.

    ``NotificationSenderEmail`` when it holds a usable address, else the ticket
    queue's system address -- the mailbox this install already sends from.
    Znuny ships the setting as ``znuny@<OTRS_CONFIG_FQDN>``; resolving that
    against the Tiqora host would invent a mailbox nobody reads, and a sender on
    a host that does not accept mail is what relays reject.
    """
    name = context.config_overrides.get("notificationsendername") or _TIQORA_SENDER_NAME
    configured = (await sysconfig.get_str("NotificationSenderEmail")).strip()
    email = configured if configured and not _UNRESOLVED_TAG.search(configured) else ""
    if not email:
        email = (context.queue.get("email") or "").strip()
    return formataddr((name, email)) if email else None
