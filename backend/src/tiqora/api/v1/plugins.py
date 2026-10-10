"""Plugin frontends for the SPA: the list of active plugins and their static files (P11)."""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.discovery import discovered_plugins, plugin_asset_path
from tiqora.plugins.frontend import PluginFrontendOut, frontend_plugins_for
from tiqora.plugins.state import is_active, loaded_plugins

router = APIRouter(prefix="/plugins", tags=["plugins"])

_MEDIA_TYPES: Final = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


@router.get("/frontend", response_model=list[PluginFrontendOut])
async def list_frontend_plugins(user: CurrentUser, session: DbSession) -> list[PluginFrontendOut]:
    if not any(is_active(pid) for pid in loaded_plugins()):
        return []
    is_admin = await PermissionEngine(session).is_admin(user.id)
    return frontend_plugins_for(is_admin=is_admin)


@router.get("/{plugin_id}/assets/{version}/{path:path}", response_class=FileResponse)
async def plugin_asset(plugin_id: str, version: str, path: str, user: CurrentUser) -> FileResponse:
    _ = user
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, "Datei nicht gefunden")
    result = loaded_plugins().get(plugin_id)
    if result is None or not is_active(plugin_id) or result.version != version:
        raise not_found
    plugin = next((p for p in discovered_plugins() if p.plugin_id == plugin_id), None)
    if plugin is None:
        raise not_found
    file = plugin_asset_path(plugin, path)
    if file is None:
        raise not_found
    media_type = _MEDIA_TYPES.get(file.suffix.lower())
    if media_type is None:
        raise not_found
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if file.suffix.lower() == ".svg":
        headers["Content-Security-Policy"] = "sandbox"
    return FileResponse(file, media_type=media_type, headers=headers)
