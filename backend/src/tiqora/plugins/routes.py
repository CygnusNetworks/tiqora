"""HTTP routes of plugins, mounted under ``/api/plugins/<plugin id>/``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import structlog
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Router
from starlette.types import Receive, Scope, Send

from tiqora.extensions.registry import ExtensionPoint, Registry
from tiqora.plugins.context import PluginContext

logger = structlog.get_logger(__name__)

ROUTES_REFUSED_PREFIX: Final = "Schnittstellen nicht eingebunden: "
MAX_REFUSED_LEN: Final = 500


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


async def _not_found(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"detail": "Erweiterung nicht gefunden"}, status_code=404)(
        scope, receive, send
    )


def _assert_authenticated(routes: list[BaseRoute]) -> None:
    """Only APIRoutes receive the ``get_current_user`` dependency from ``include_router``;
    plain Starlette routes, mounts and websockets would be served without it."""
    for r in routes:
        if not isinstance(r, APIRoute):
            raise RuntimeError(f"route {getattr(r, 'path', r)!r} would be served unauthenticated")


def _snapshot(source: APIRouter) -> APIRouter:
    """A private copy of the validated routes: the plugin keeps no reference to the router the
    sub-app serves, so routes it adds to its own router after mount are never exposed."""
    fresh = APIRouter(
        dependencies=list(source.dependencies),
        tags=list(source.tags),
        responses=dict(source.responses),
    )
    fresh.routes.extend(list(source.routes))
    return fresh


def create_plugin_router() -> Router:
    """Empty router for ``/api/plugins``; unknown or inactive plugins answer a JSON 404."""
    return Router(routes=[], default=_not_found)


def mount_plugin_routes(app: FastAPI, registry: Registry) -> list[str]:
    """Mount one sub-app per ACTIVE plugin with routes; returns the ids mounted by this call.

    Authenticated routes depend on ``get_current_user`` (session or API key with the
    ``plugins`` area). Only routes registered with ``public=True`` (which needs the manifest
    capability ``core.http.public_routes``) skip it. Idempotent per app. A plugin whose
    routes are refused is marked FAILED in this process and listed in
    ``app.state.plugin_routes_refused`` (``plugin id -> reason``).
    """
    from tiqora.api.deps import get_current_user
    from tiqora.plugins.state import is_active, mark_failed

    router: Router = app.state.plugin_router
    done: set[str] = getattr(app.state, "plugin_routes_mounted", set())
    app.state.plugin_routes_mounted = done
    refused: dict[str, str] = getattr(app.state, "plugin_routes_refused", {})
    app.state.plugin_routes_refused = refused
    by_plugin: dict[str, list[PluginRoute]] = {}
    for route in registry.items(PLUGIN_ROUTES):
        by_plugin.setdefault(route.plugin_id, []).append(route)

    mounted: list[str] = []
    for plugin_id in sorted(by_plugin):
        if plugin_id in done or not is_active(plugin_id):
            continue
        sub = FastAPI(
            title=f"Erweiterung {plugin_id}",
            docs_url=None,
            redoc_url=None,
            openapi_url=None if app.state.settings.is_production else "/openapi.json",
        )
        sub.state = app.state
        sub.dependency_overrides = app.dependency_overrides
        try:
            for route in by_plugin[plugin_id]:
                if not route.public:
                    _assert_authenticated(list(route.router.routes))
                sub.include_router(
                    _snapshot(route.router),
                    dependencies=[] if route.public else [Depends(get_current_user)],
                )
        except RuntimeError as exc:
            # Defence in depth behind the registrar check: never serve an unauthenticated route.
            logger.error("plugin_routes_refused", plugin=plugin_id, error=str(exc))
            # Not "Aktiv" while every route answers 404: failed in this process; the api
            # runtime row is booked by ``record_refused_plugin_routes``.
            reason = f"{ROUTES_REFUSED_PREFIX}{exc}"[:MAX_REFUSED_LEN]
            refused[plugin_id] = reason
            mark_failed(plugin_id, reason)
            continue
        router.routes.append(Mount(f"/{plugin_id}", app=sub))
        done.add(plugin_id)
        mounted.append(plugin_id)
    logger.info("plugin_routes_mounted", plugins=mounted)
    return mounted


async def record_refused_plugin_routes(
    app: FastAPI, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Book the api runtime row of each plugin whose routes were refused as FAILED."""
    from tiqora.jobs.service import utc_now
    from tiqora.plugins import store
    from tiqora.plugins.state import RuntimeState, loaded_plugins

    refused: dict[str, str] = getattr(app.state, "plugin_routes_refused", {})
    for plugin_id, reason in refused.items():
        if not store.storable(plugin_id):
            continue
        loaded = loaded_plugins().get(plugin_id)
        try:
            await store.mark_result(
                session_factory,
                plugin_id,
                "api",
                state=RuntimeState.FAILED,
                version=loaded.version if loaded is not None else None,
                error=reason,
                now=utc_now(),
                keep_attempts=True,
            )
        except Exception as exc:  # noqa: BLE001 - the row only feeds the admin page
            logger.warning(
                "plugin_state_write_failed",
                plugin=plugin_id,
                role="api",
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
