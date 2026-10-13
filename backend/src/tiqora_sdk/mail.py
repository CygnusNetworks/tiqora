"""Names for mail filters (``registry.mail_filter``).

``stage="pre"``: ``async def (ctx: MailFilterContext, mail: InboundMail) -> None`` runs for an
incoming mail after Znuny's postmaster filters and before the ticket is matched. Set
``X-OTRS-*`` keys in ``mail.headers`` (``X-OTRS-Ignore: yes`` drops the mail). Filters are
fail-open: one that raises or hangs is skipped and its header changes are undone.

``stage="post"``: ``async def (ctx, result: InboundMailResult) -> None`` runs after the mail's
transaction committed, so ``ctx.plugin.tickets.get(result.ticket_id)`` sees the ticket.

``stage="outbound"``: ``async def (ctx, mail: OutboundMail) -> None`` may add ``X-`` headers
(``mail.add_header``) or raise ``MailVetoed("<German reason>")`` to stop the send.
"""

from __future__ import annotations

from tiqora.plugins.mail_filters import (
    InboundMail,
    InboundMailResult,
    MailFilterContext,
    MailFilterStage,
    MailVetoed,
    OutboundMail,
)

__all__ = [
    "InboundMail",
    "InboundMailResult",
    "MailFilterContext",
    "MailFilterStage",
    "MailVetoed",
    "OutboundMail",
]
