"""Names for notification transports (``registry.notification_transport``).

``async def send(ctx: TransportContext, message: NotificationMessage) -> None`` is called once
per recipient of a notification event whose ``Transports`` list contains the transport's
qualified name (``Plugin_<short_id>_<Name>``, returned by the registration). ``message``
carries the subject and body rendered exactly as for the mail, the ticket and the recipient;
how to address the recipient (a chat id, a phone number) is the plugin's job. Raise to report
a failed delivery. A transport writes no ticket history and is not signed or encrypted.
"""

from __future__ import annotations

from tiqora.plugins.notification_transports import (
    NotificationMessage,
    NotificationRecipient,
    TransportContext,
    TransportSend,
)

__all__ = [
    "NotificationMessage",
    "NotificationRecipient",
    "TransportContext",
    "TransportSend",
]
