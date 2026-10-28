"""Re-export of :mod:`tiqora.domain.ticket_access` (moved there so the approval engine can use it
without importing ``tiqora.itsm``). Either import path works."""

from __future__ import annotations

from tiqora.domain.ticket_access import (
    CHUNK,
    TicketAccessDenied,
    TicketFacts,
    readable_tickets,
    require_ticket,
)

__all__ = ["CHUNK", "TicketAccessDenied", "TicketFacts", "readable_tickets", "require_ticket"]
