"""HTTP route extension point for plugins (mounting follows in Task 5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fastapi import APIRouter

from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.context import PluginContext


@dataclass(frozen=True, slots=True)
class PluginRoute:
    key: str  # "<plugin_id>:<n>"
    plugin_id: str
    router: APIRouter
    public: bool  # False: every route depends on get_current_user
    context: PluginContext | None = None


PLUGIN_ROUTES: Final[ExtensionPoint[PluginRoute]] = ExtensionPoint(
    key="core.http.routes",
    item_key=lambda r: r.key,
    description="HTTP routes served under /api/plugins/<id>/.",
    sdk_stable=True,
)
