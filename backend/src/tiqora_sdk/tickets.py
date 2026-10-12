"""Names for the write-capable ``ctx.tickets`` and read-only ``ctx.customers`` services."""

from __future__ import annotations

from tiqora.plugins.customers import CustomerSummary
from tiqora.plugins.writes import PluginPermissionDenied, PluginWriteError, TicketDraft

__all__ = ["CustomerSummary", "PluginPermissionDenied", "PluginWriteError", "TicketDraft"]
