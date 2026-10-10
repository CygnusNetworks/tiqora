"""Chat session service (grows in later tasks); constants only for now."""

from __future__ import annotations

from typing import Final

#: ``communication_channel.name`` of webchat articles (row created on demand,
#: unregistered, ``package_name`` ``Tiqora``; see ``ensure_channel_row``).
COMM_CHANNEL_NAME: Final = "WebChat"
#: Znuny reads a webchat article through its Internal backend (plain MIME
#: article, no mail transport), so the golden test finds the right class.
COMM_CHANNEL_MODULE: Final = "Kernel::System::CommunicationChannel::Internal"
#: ``Article.channel`` value used by the ticket write service.
ARTICLE_CHANNEL: Final = "webchat"
