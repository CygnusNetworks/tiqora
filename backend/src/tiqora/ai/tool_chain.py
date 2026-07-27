"""Unusual tool-chain detection for a single agent run (plan #10).

Pure analysis over tool names executed in order — no I/O. The runtime logs
and optionally records hits; this module only classifies.
"""

from __future__ import annotations

from dataclasses import dataclass

from tiqora.ai.tools import (
    LOCAL_TOOL_NAMES,
    TOOL_ADD_INTERNAL_NOTE,
    TOOL_ESCALATE_TO_HUMAN,
    TOOL_PROPOSE_CUSTOMER_MESSAGE,
    TOOL_UPDATE_TICKET_FIELDS,
)


@dataclass(frozen=True, slots=True)
class ToolChainAlert:
    code: str
    message: str
    tools: tuple[str, ...]


def _is_mcp(name: str) -> bool:
    return name not in LOCAL_TOOL_NAMES and ":" in name


def analyze_tool_chain(tool_names: list[str]) -> list[ToolChainAlert]:
    """Return zero or more alerts for a completed tool-name sequence."""
    if not tool_names:
        return []
    names = tuple(tool_names)
    unique = set(names)
    alerts: list[ToolChainAlert] = []

    has_mcp = any(_is_mcp(n) for n in names)
    has_update = TOOL_UPDATE_TICKET_FIELDS in unique
    has_propose = TOOL_PROPOSE_CUSTOMER_MESSAGE in unique
    has_note = TOOL_ADD_INTERNAL_NOTE in unique
    has_escalate = TOOL_ESCALATE_TO_HUMAN in unique

    if has_mcp and has_update and has_propose:
        alerts.append(
            ToolChainAlert(
                code="mcp_mutate_and_propose",
                message=(
                    "Run combined MCP tool use, ticket field mutation, and a "
                    "customer-message proposal — review for prompt-injection impact."
                ),
                tools=names,
            )
        )
    elif has_mcp and has_propose:
        alerts.append(
            ToolChainAlert(
                code="mcp_and_propose",
                message=(
                    "Run combined MCP tool use with a customer-message proposal — "
                    "external data may have influenced the reply."
                ),
                tools=names,
            )
        )

    if has_update and has_propose and not has_mcp:
        alerts.append(
            ToolChainAlert(
                code="mutate_and_propose",
                message="Run mutated ticket fields and proposed a customer message.",
                tools=names,
            )
        )

    if has_escalate and has_propose:
        alerts.append(
            ToolChainAlert(
                code="escalate_and_propose",
                message="Run both escalated to human and proposed a customer message.",
                tools=names,
            )
        )

    if has_note and has_propose and len(names) >= 4:
        alerts.append(
            ToolChainAlert(
                code="long_chain_with_note_and_propose",
                message="Unusually long tool chain with internal note and customer proposal.",
                tools=names,
            )
        )

    return alerts


__all__ = ["ToolChainAlert", "analyze_tool_chain"]
