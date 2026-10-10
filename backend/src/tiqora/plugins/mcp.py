"""MCP tool extension point for plugins and its binding to the MCP server."""

from __future__ import annotations

import asyncio
import functools
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastmcp.exceptions import ToolError

from tiqora.extensions.registry import ExtensionPoint, Registry
from tiqora.plugins.context import PluginContext
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from fastmcp import FastMCP

log = structlog.get_logger(__name__)


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


# Names already bound per server instance (idempotent registration).
_BOUND: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()
# Consecutive failures per plugin since its last successful tool call (process local).
_CONSECUTIVE_FAILURES: dict[str, int] = {}


def plugin_mcp_failures(plugin_id: str) -> int:
    """Consecutive failed tool calls of a plugin in this MCP process (0 after a success)."""
    return _CONSECUTIVE_FAILURES.get(plugin_id, 0)


def _record_failure(tool: McpTool, kind: str, exc: BaseException | None) -> None:
    count = _CONSECUTIVE_FAILURES.get(tool.plugin_id, 0) + 1
    _CONSECUTIVE_FAILURES[tool.plugin_id] = count
    log.warning(
        "plugin_mcp_tool_failed",
        plugin_id=tool.plugin_id,
        tool=tool.name,
        kind=kind,
        consecutive_failures=count,
        exc_info=exc,
    )


def _make_wrapper(registry: Registry, tool: McpTool) -> Callable[..., Awaitable[Any]]:
    """Wrapper that applies the core checks and then runs the plugin function.

    The tool is looked up in the LIVE registry on every call (a late plugin boot swaps the
    registry content in place), and only while its plugin is active.

    ``write=False`` is declared by the plugin and not trusted: the wrapper cannot see what a
    plugin function does. Plugin tools that read tickets must go through
    ``PluginTickets.get(..., as_agent=current_agent_id(ctx))``.
    """
    from tiqora.mcp_server.server import _assert_mcp_write_scope, _assert_tool_allowed

    name = tool.name

    @functools.wraps(tool.fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        live = registry.get(MCP_TOOLS, name)
        if live is None or not is_active(live.plugin_id):
            raise ToolError("Dieses Werkzeug ist nicht verfügbar")
        # Same gates as the core tools; their errors propagate unchanged.
        _assert_tool_allowed(name)
        if live.write:
            _assert_mcp_write_scope()
        try:
            async with asyncio.timeout(live.timeout_seconds):
                result = await live.fn(*args, **kwargs)
        except TimeoutError:
            _record_failure(live, "timeout", None)
            raise ToolError(
                f"Zeitlimit von {live.timeout_seconds} Sekunden überschritten"
            ) from None
        except ToolError:
            raise
        except Exception as exc:
            # No traceback, message or settings reach the client; details only in the log.
            _record_failure(live, "error", exc)
            raise ToolError("Das Werkzeug ist fehlgeschlagen") from None
        _CONSECUTIVE_FAILURES.pop(live.plugin_id, None)
        return result

    return wrapper


def register_plugin_mcp_tools(server: FastMCP, registry: Registry) -> list[str]:
    """Bind the MCP tools of all active plugins to ``server``; returns the new names."""
    bound = _BOUND.setdefault(server, set())
    added: list[str] = []
    for tool in registry.items(MCP_TOOLS):
        if tool.name in bound or not is_active(tool.plugin_id):
            continue
        if tool.name in CORE_MCP_TOOL_NAMES:
            log.error("plugin_mcp_tool_name_reserved", plugin_id=tool.plugin_id, tool=tool.name)
            continue
        server.tool(
            name=tool.name,
            description=tool.description,
            annotations={"readOnlyHint": not tool.write},
        )(_make_wrapper(registry, tool))
        bound.add(tool.name)
        added.append(tool.name)
    if added:
        log.info("plugin_mcp_tools_bound", tools=added)
    return added
