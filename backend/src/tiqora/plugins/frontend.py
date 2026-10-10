"""What the browser may know about plugins: the list of active frontends (P11)."""

from __future__ import annotations

from pydantic import BaseModel

from tiqora.plugins.discovery import DiscoveredPlugin, discovered_plugins
from tiqora.plugins.manifest import (
    FrontendContribution,
    NavContribution,
    RouteContribution,
)
from tiqora.plugins.state import is_active, loaded_plugins


class PluginFrontendOut(BaseModel):
    id: str
    name: str
    version: str
    asset_base: str  # f"/api/v1/plugins/{id}/assets/{version}/"
    entry: str | None  # "web/" + web manifest entry; None without web/manifest.json
    styles: list[str]  # "web/" + each style
    locales: list[str]
    contributions: list[FrontendContribution]


def asset_base(plugin_id: str, version: str) -> str:
    return f"/api/v1/plugins/{plugin_id}/assets/{version}/"


def _visible(plugin: DiscoveredPlugin, *, is_admin: bool) -> list[FrontendContribution]:
    web = plugin.web_manifest
    if web is None:
        return []
    if is_admin:
        return list(web.contributions)
    hidden_routes = {
        c.key for c in web.contributions if isinstance(c, RouteContribution) and c.admin_only
    }
    out: list[FrontendContribution] = []
    for c in web.contributions:
        if isinstance(c, RouteContribution | NavContribution) and c.admin_only:
            continue
        if isinstance(c, NavContribution) and c.route in hidden_routes:
            continue  # a menu entry must not point at a page the agent cannot open
        out.append(c)
    return out


def frontend_plugins_for(*, is_admin: bool) -> list[PluginFrontendOut]:
    """Plugins ACTIVE in this process that bring a web part or translations, sorted by id.

    Empty in safe mode (nothing is loaded then). Contributions marked ``admin_only`` are
    left out for other agents.
    """
    loaded = loaded_plugins()
    out: list[PluginFrontendOut] = []
    for plugin in discovered_plugins():
        if plugin.manifest is None or not is_active(plugin.plugin_id):
            continue
        result = loaded[plugin.plugin_id]
        if plugin.web_manifest is None and not plugin.locales:
            continue
        web = plugin.web_manifest
        out.append(
            PluginFrontendOut(
                id=plugin.plugin_id,
                name=plugin.manifest.name,
                version=result.version,
                asset_base=asset_base(plugin.plugin_id, result.version),
                entry=f"web/{web.entry}" if web is not None else None,
                styles=[f"web/{s}" for s in web.styles] if web is not None else [],
                locales=list(plugin.locales),
                contributions=_visible(plugin, is_admin=is_admin),
            )
        )
    return sorted(out, key=lambda p: p.id)
