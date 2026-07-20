"""Serve the built frontend SPA from the api process.

The Docker image bakes the vite build into ``frontend_dist_dir`` (default
``/app/frontend/dist``) so a single image ships backend + UI — a plain image
pull updates everything, with no separate static-file deployment. Host nginx
only needs to terminate TLS and proxy ``/`` to the api.

``mount_spa`` is a no-op when serving is disabled or no build is present (local
api-dev and tests run the frontend separately via ``pnpm dev``), so those
environments keep the JSON ``GET /`` root untouched.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tiqora.config import Settings

logger = structlog.get_logger(__name__)

#: Path prefixes owned by the API — never shadowed by the SPA fallback, so
#: unknown API routes keep returning JSON 404s instead of ``index.html``.
_RESERVED_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/znuny-compat",
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/mcp",
)


def spa_is_available(cfg: Settings) -> bool:
    """True when a built SPA should be served from this process."""
    if not cfg.serve_frontend:
        return False
    return (Path(cfg.frontend_dist_dir) / "index.html").is_file()


def mount_spa(app: FastAPI, cfg: Settings) -> bool:
    """Mount the SPA (static assets + client-side-routing fallback).

    Returns True if mounted. Call LAST, after all routers/ops routes, because
    the catch-all route matches any remaining GET path.
    """
    if not spa_is_available(cfg):
        return False

    dist = Path(cfg.frontend_dist_dir).resolve()
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        path = "/" + full_path
        # Keep API namespaces JSON-404 rather than serving the SPA shell.
        if any(path == p or path.startswith(p) for p in _RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found")
        # Serve a real static file if one exists (favicon, robots, …),
        # guarding against path traversal; otherwise the SPA entrypoint.
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
        return FileResponse(index)

    logger.info("spa_mounted", dist=str(dist))
    return True
