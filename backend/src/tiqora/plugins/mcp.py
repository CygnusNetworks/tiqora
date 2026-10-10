"""MCP tool extension point for plugins (binding to the server follows in Task 9)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.context import PluginContext


@dataclass(frozen=True, slots=True)
class McpTool:
    name: str  # "<short_id>_<name>"
    plugin_id: str
    fn: Callable[..., Awaitable[Any]]  # first parameter ``ctx: fastmcp.Context``
    description: str
    write: bool
    timeout_seconds: int
    context: PluginContext | None = None


MCP_TOOLS: Final[ExtensionPoint[McpTool]] = ExtensionPoint(
    key="core.mcp.tools",
    item_key=lambda t: t.name,
    description="MCP tools provided by plugins.",
    sdk_stable=True,
)

# Every ``@mcp.tool`` name in tiqora/mcp_server/server.py (Task 9 asserts equality with the server).
CORE_MCP_TOOL_NAMES: Final = frozenset(
    {
        "customer_lookup",
        "get_attachment_meta",
        "kb_get_article",
        "kb_list",
        "kb_publish_article",
        "kb_search",
        "kb_upsert_article",
        "list_agents",
        "list_attachments",
        "list_priorities",
        "list_queues",
        "list_states",
        "ticket_archive",
        "ticket_bounce",
        "ticket_create",
        "ticket_forward",
        "ticket_get",
        "ticket_get_by_number",
        "ticket_history",
        "ticket_link",
        "ticket_lock",
        "ticket_merge",
        "ticket_note",
        "ticket_reply",
        "ticket_search",
        "ticket_set_customer",
        "ticket_set_dynamic_field",
        "ticket_set_responsible",
        "ticket_set_service",
        "ticket_set_sla",
        "ticket_set_title",
        "ticket_set_type",
        "ticket_unarchive",
        "ticket_unlock",
        "ticket_unwatch",
        "ticket_update_owner",
        "ticket_update_priority",
        "ticket_update_queue",
        "ticket_update_state",
        "ticket_watch",
    }
)
