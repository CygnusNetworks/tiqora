"""Notification transports from plugins (D10).

A notification event's ``Transports`` item lists ``Email`` and/or qualified plugin names
(``Plugin_<short_id>_<Name>``). For every recipient the engine renders the message exactly as
for the mail and hands it to the plugin, which decides how to reach the recipient (a chat id,
a phone number, ...). A plugin transport writes no ticket history (assumption 13) and is not
signed or encrypted: those settings apply to the Email transport only.

A transport that raises or hangs counts as a failed send for that recipient and never stops
the other recipients or transports. The engine itself stays behind
``daemon.notifications.enabled``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Final, Literal

import structlog

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import call_plugin
from tiqora.plugins.context import PluginContext, TicketSummary, minimal_context
from tiqora.plugins.state import is_active

logger = structlog.get_logger(__name__)

__all__ = [
    "MAX_TRANSPORT_TIMEOUT",
    "MIN_TRANSPORT_TIMEOUT",
    "NOTIFICATION_TRANSPORTS",
    "NotificationMessage",
    "NotificationRecipient",
    "NotificationTransport",
    "TransportContext",
    "TransportSend",
    "active_transports",
    "send_via_plugin_transport",
]

MIN_TRANSPORT_TIMEOUT: Final = 1
MAX_TRANSPORT_TIMEOUT: Final = 60


@dataclass(frozen=True, slots=True)
class NotificationRecipient:
    kind: Literal["agent", "customer"]
    user_id: int | None  # agents only
    customer_login: str | None  # ticket.customer_user_id for customer recipients
    email: str
    full_name: str
    language: str


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    notification_id: int
    notification_name: str
    event: str
    ticket: TicketSummary
    recipient: NotificationRecipient
    subject: str
    body: str
    content_type: str  # rendered exactly as for the Email transport


@dataclass(frozen=True, slots=True)
class TransportContext:
    plugin: PluginContext


TransportSend = Callable[[TransportContext, NotificationMessage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class NotificationTransport:
    key: str  # qualified, e.g. "Plugin_points_Chat"
    plugin_id: str
    label: str
    send: TransportSend
    timeout_seconds: int  # 1..60
    context: PluginContext | None = None


NOTIFICATION_TRANSPORTS: Final[ExtensionPoint[NotificationTransport]] = ExtensionPoint(
    key="core.notifications.transports",
    item_key=lambda t: t.key,
    description="Ways a plugin delivers event notifications (chat, SMS, ...), next to e-mail.",
    sdk_stable=True,
)


def active_transports(keys: Iterable[str]) -> list[NotificationTransport]:
    """The registered transports of loaded plugins among *keys*, ordered by key."""
    wanted = set(keys)
    return sorted(
        (
            t
            for t in get_registry().items(NOTIFICATION_TRANSPORTS)
            if t.key in wanted and is_active(t.plugin_id)
        ),
        key=lambda t: t.key,
    )


async def send_via_plugin_transport(
    transport: NotificationTransport, message: NotificationMessage
) -> bool:
    """Deliver one rendered message; False when the plugin failed or ran out of time."""
    ctx = TransportContext(plugin=transport.context or minimal_context(transport.plugin_id))
    outcome = await call_plugin(
        transport.plugin_id,
        f"notification transport {transport.key}",
        lambda: transport.send(ctx, message),
        timeout=float(transport.timeout_seconds),
    )
    return outcome.ok
