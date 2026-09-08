"""Unit tests for Tiqora's notification-template compatibility layer."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.channels.email.placeholder import PlaceholderContext, expand_placeholders
from tiqora.config import Settings
from tiqora.worker.notification_templates import (
    configure_notification_context,
    has_untranslated_legacy_link,
    normalize_notification_template,
    resolve_notification_sender,
)
from tiqora.znuny.sysconfig import SysConfig


def _sysconfig(values: dict[str, Any] | None = None) -> SysConfig:
    values = values or {}

    async def fetch(name: str) -> Any | None:
        return values.get(name)

    return SysConfig(fetch=fetch)


async def _expand_notification_template(
    template: str,
    *,
    recipient_kind: str = "Agent",
    settings: Settings | None = None,
    sender_name: str | None = None,
    escape_html: bool = False,
    customer_body: str | None = None,
) -> str:
    context = PlaceholderContext(
        ticket={"ticketid": "45", "ticketnumber": "2026090810000045"},
        notification_recipient={"userfullname": "Ada Lovelace"},
        customer_body=customer_body,
    )
    sysconfig = _sysconfig(
        {"NotificationSenderName": sender_name} if sender_name is not None else {}
    )
    await configure_notification_context(
        context,
        sysconfig,
        settings or Settings(public_base_url="https://help.example.test/support/"),
        ticket_id=45,
        recipient_kind=recipient_kind,
    )
    return await expand_placeholders(
        None,
        sysconfig,
        normalize_notification_template(template),
        context=PlaceholderContext(**{**context.__dict__, "escape_html": escape_html}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template",
    [
        "<OTRS_CONFIG_HttpType>://<OTRS_CONFIG_FQDN>/<OTRS_CONFIG_ScriptAlias>"
        "index.pl?Action=AgentTicketZoom;TicketID=<OTRS_TICKET_TicketID>",
        "&lt;OTRS_CONFIG_HttpType&gt;://&lt;OTRS_CONFIG_FQDN&gt;/"
        "&lt;OTRS_CONFIG_ScriptAlias&gt;index.pl?Action=AgentTicketZoom&amp;"
        "TicketID=&lt;OTRS_TICKET_TicketID&gt;",
        "<TIQORA_CONFIG_HttpType>://<TIQORA_CONFIG_FQDN>/<TIQORA_CONFIG_ScriptAlias>"
        "customer.pl?Action=CustomerTicketZoom&TicketID=<TIQORA_TICKET_TicketID>",
        r"<OTRS_CONFIG_HttpType>://<OTRS_CONFIG_FQDN>/<OTRS_CONFIG_ScriptAlias>"
        r"index.pl?Action=AgentTicketZoom\;TicketID=<OTRS_TICKET_TicketID>"
        r"\;ArticleID=<OTRS_TICKET_LAST_ARTICLE_ID>",
    ],
)
async def test_inherited_otrs_ticket_links_normalize_before_expansion(template: str) -> None:
    assert (
        await _expand_notification_template(template)
        == "https://help.example.test/support/agent/tickets/45"
    )


@pytest.mark.asyncio
async def test_tiqora_ticket_url_uses_portal_route_for_customer() -> None:
    assert (
        await _expand_notification_template("Open <TIQORA_TICKET_URL>", recipient_kind="Customer")
        == "Open https://help.example.test/support/portal/tickets/45"
    )


@pytest.mark.asyncio
async def test_ticket_url_uses_first_http_cors_origin_when_public_base_url_is_unset() -> None:
    # A non-absolute entry (e.g. "*") must be skipped, not turned into a dead link.
    settings = Settings(
        public_base_url="",
        cors_origins="*,https://fallback.example.test/tiqora/,http://localhost:5173",
    )
    assert (
        await _expand_notification_template("<OTRS_TICKET_URL>", settings=settings)
        == "https://fallback.example.test/tiqora/agent/tickets/45"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_name", ["OTRS Notifications", "Znuny Notifications", None])
async def test_default_branding_is_tiqora_but_custom_sender_name_is_preserved(
    legacy_name: str | None,
) -> None:
    assert (
        await _expand_notification_template(
            "-- <OTRS_CONFIG_NotificationSenderName>", sender_name=legacy_name
        )
        == "-- Tiqora Notifications"
    )
    assert (
        await _expand_notification_template(
            "-- <OTRS_CONFIG_NotificationSenderName>", sender_name="Support Team"
        )
        == "-- Support Team"
    )


@pytest.mark.asyncio
async def test_notification_values_are_html_escaped_without_recursive_expansion() -> None:
    context = PlaceholderContext(
        ticket={"ticketid": "45"},
        notification_recipient={"userfullname": "<b><OTRS_TICKET_TicketID></b>"},
        escape_html=True,
    )
    sysconfig = _sysconfig()
    await configure_notification_context(
        context,
        sysconfig,
        Settings(public_base_url="https://help.example.test"),
        ticket_id=45,
        recipient_kind="Agent",
    )
    result = await expand_placeholders(
        None,
        sysconfig,
        "<p><OTRS_NOTIFICATION_RECIPIENT_UserFullname></p>",
        context=context,
    )
    assert result == "<p>&lt;b&gt;&lt;OTRS_TICKET_TicketID&gt;&lt;/b&gt;</p>"


@pytest.mark.asyncio
async def test_html_mode_keeps_line_breaks_of_quoted_article() -> None:
    """A quoted article is plain text; in an HTML notification its newlines must
    survive as ``<br />`` instead of collapsing into one paragraph."""
    result = await _expand_notification_template(
        "<p><OTRS_CUSTOMER_BODY></p>",
        escape_html=True,
        customer_body="Erste Zeile\nZweite Zeile",
    )
    assert result == "<p>Erste Zeile<br />\nZweite Zeile</p>"


@pytest.mark.parametrize(
    ("rendered", "expected"),
    [
        ("Open https://help.example.test/agent/tickets/45", False),
        ("Open https://help.example.test/otrs/index.pl?Action=AgentTicketZoom;TicketID=45", True),
        ("Open https://help.example.test/otrs/customer.pl?Action=CustomerTicketZoom;TID=45", True),
    ],
)
def test_untranslated_legacy_link_is_detectable(rendered: str, expected: bool) -> None:
    """A customized template whose ticket URL we could not translate would point at
    the Tiqora host with a Znuny path — detectable so the worker can warn."""
    assert has_untranslated_legacy_link(rendered) is expected


@pytest.mark.asyncio
async def test_notification_sender_prefers_configured_address() -> None:
    context = PlaceholderContext(
        queue={"email": "queue@example.test", "real_name": "Queue"},
        config_overrides={"notificationsendername": "Support Team"},
    )
    sysconfig = _sysconfig({"NotificationSenderEmail": "notifications@example.test"})
    assert (
        await resolve_notification_sender(sysconfig, context)
        == "Support Team <notifications@example.test>"
    )


@pytest.mark.asyncio
async def test_notification_sender_falls_back_to_queue_system_address() -> None:
    """Znuny ships the setting as ``znuny@<OTRS_CONFIG_FQDN>``; expanding that against
    the Tiqora host would invent a mailbox nobody reads."""
    context = PlaceholderContext(
        queue={"email": "queue@example.test"},
        config_overrides={"notificationsendername": "Tiqora Notifications"},
    )
    sysconfig = _sysconfig({"NotificationSenderEmail": "znuny@<OTRS_CONFIG_FQDN>"})
    assert (
        await resolve_notification_sender(sysconfig, context)
        == "Tiqora Notifications <queue@example.test>"
    )


@pytest.mark.asyncio
async def test_notification_sender_is_none_without_any_usable_address() -> None:
    sysconfig = _sysconfig({"NotificationSenderEmail": ""})
    assert await resolve_notification_sender(sysconfig, PlaceholderContext()) is None
