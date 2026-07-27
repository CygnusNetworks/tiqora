"""Per-run agent capabilities (plan C #5) — derived from queue autonomy
with optional JSON overrides on ``tiqora_ai_queue_policy.capabilities_json``.

Autonomy still drives the draft/send matrix in :mod:`tiqora.ai.runtime`.
Capabilities control **which tools and field mutations** the model may
invoke. Defaults implement least privilege:

- ``off``: propose + note + escalate + KB + read-only MCP; no ticket mutations
- ``clarify_only``: same + state updates (still constrained by
  ``allowed_state_types``); no priority/customer/mutating MCP
- ``full``: all local field updates + mutating MCP

Overrides never *add* auto-send (that stays in ``_map_customer_message``);
they only tighten or (where allowed) re-enable tool-side capabilities.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any

from tiqora.ai.models import AUTONOMY_CLARIFY_ONLY, AUTONOMY_FULL, AUTONOMY_OFF


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    propose_message: bool = True
    internal_note: bool = True
    escalate: bool = True
    kb_read: bool = True
    update_state: bool = False
    update_priority: bool = False
    set_customer: bool = False
    mcp_readonly: bool = True
    mcp_mutating: bool = False

    def allows_update_ticket_fields(self) -> bool:
        return self.update_state or self.update_priority or self.set_customer

    def to_public_dict(self) -> dict[str, bool]:
        return asdict(self)


_BOOL_FIELDS = frozenset(f.name for f in fields(AgentCapabilities))


def capabilities_for_autonomy(autonomy: str) -> AgentCapabilities:
    """Default capability set for a queue autonomy mode."""
    if autonomy == AUTONOMY_FULL:
        return AgentCapabilities(
            update_state=True,
            update_priority=True,
            set_customer=True,
            mcp_mutating=True,
        )
    if autonomy == AUTONOMY_CLARIFY_ONLY:
        return AgentCapabilities(
            update_state=True,
            update_priority=False,
            set_customer=False,
            mcp_mutating=False,
        )
    # off (default) and any unknown value: least privilege
    _ = autonomy if autonomy == AUTONOMY_OFF else autonomy
    return AgentCapabilities()


def parse_capabilities_override(raw: str | None) -> dict[str, bool]:
    """Parse ``capabilities_json`` into a partial bool map. Invalid JSON /
    non-object / non-bool values are ignored (fail open to defaults)."""
    if raw is None or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, bool] = {}
    for key, value in data.items():
        if key in _BOOL_FIELDS and isinstance(value, bool):
            out[key] = value
    return out


def resolve_capabilities(
    autonomy: str, *, capabilities_json: str | None = None
) -> AgentCapabilities:
    """Merge autonomy defaults with optional admin overrides."""
    base = capabilities_for_autonomy(autonomy)
    overrides = parse_capabilities_override(capabilities_json)
    if not overrides:
        return base
    merged: dict[str, Any] = asdict(base)
    merged.update(overrides)
    return AgentCapabilities(**merged)


__all__ = [
    "AgentCapabilities",
    "capabilities_for_autonomy",
    "parse_capabilities_override",
    "resolve_capabilities",
]
