"""Names for customer channels (``registry.channel``).

A plugin channel carries customer conversations the core does not know (a chat, a messenger).
``registry.channel("Chat", label=..., capabilities=ChannelCapabilities(max_length=500),
send=send)`` returns the channel key; inbound messages are stored with
``ctx.channels.ingest("Chat", InboundChannelMessage(...))``, normally from a public route of the
plugin. **An inbound route is public: verify a signature or shared secret before ingesting**
(``verify_shared_secret``), since an ingested message opens tickets as the system user.

``send`` is ``async def (ctx: ChannelContext, message: OutboundChannelMessage) ->
DeliveryResult`` and runs when an agent answers on the channel; raise to report that the message
was not delivered (nothing is stored then). ``InboundChannelMessage.sender`` is the address or
handle the channel knows and becomes the reply address of the agent's answer.
"""

from __future__ import annotations

from tiqora.channels.common import verify_shared_secret
from tiqora.plugins.channels import (
    ChannelCapabilities,
    ChannelContext,
    ChannelSend,
    DeliveryResult,
    InboundChannelMessage,
    IngestResult,
    OutboundChannelMessage,
)

__all__ = [
    "ChannelCapabilities",
    "ChannelContext",
    "ChannelSend",
    "DeliveryResult",
    "InboundChannelMessage",
    "IngestResult",
    "OutboundChannelMessage",
    "verify_shared_secret",
]
