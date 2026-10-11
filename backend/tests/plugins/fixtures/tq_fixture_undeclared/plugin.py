"""Fixture plugin that uses an extension point its manifest does not declare."""

from __future__ import annotations

from typing import Any

from tiqora_sdk import PluginRegistry


async def fn(ctx: Any) -> dict[str, Any]:
    return {}


class UndeclaredPlugin:
    def register(self, registry: PluginRegistry) -> None:
        registry.mcp_tool("xx", fn, description="xx")


plugin = UndeclaredPlugin()
