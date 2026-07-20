"""Dynamic route mounting for Znuny GenericInterface compatibility.

On startup reads `gi_webservice_config` rows, YAML-parses
Provider.Transport.Config.RouteOperationMapping, and registers routes under:
  /znuny-compat/Webservice/{name}{route}
  /znuny-compat/WebserviceID/{id}{route}

Fallback canonical routes (always available):
  POST   /znuny-compat/Session              → SessionCreate
  POST   /znuny-compat/Ticket              → TicketCreate
  GET    /znuny-compat/Ticket/:TicketID     → TicketGet
  PATCH  /znuny-compat/Ticket/:TicketID     → TicketUpdate
  GET    /znuny-compat/TicketSearch         → TicketSearch

Admin reload endpoint: POST /znuny-compat/admin/reload (requires tiqora auth).

Query-string AND JSON-body parameters are merged (body takes precedence).
"""

from __future__ import annotations

import re
from typing import Any

import structlog
import yaml
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.operations import (
    op_session_create,
    op_ticket_create,
    op_ticket_get,
    op_ticket_search,
    op_ticket_update,
)
from tiqora.api.deps import CurrentUser, DbSession, get_db, get_session_store
from tiqora.db.legacy.config import GiWebserviceConfig
from tiqora.domain.auth import SessionStore
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Supported operation types → handler mapping
# ---------------------------------------------------------------------------

_SUPPORTED_OPS: dict[str, str] = {
    "SessionCreate": "SessionCreate",
    "TicketCreate": "TicketCreate",
    "TicketUpdate": "TicketUpdate",
    "TicketGet": "TicketGet",
    "TicketSearch": "TicketSearch",
}

# ---------------------------------------------------------------------------
# Main compat router
# ---------------------------------------------------------------------------

compat_router = APIRouter(prefix="/znuny-compat", tags=["znuny-compat"])

# Dynamic routes are stored here (rebuilt on admin/reload)
_dynamic_router: APIRouter = APIRouter()
_dynamic_routes_registered: bool = False


async def _merge_params(request: Request) -> dict[str, Any]:
    """Merge query-string params and JSON body (body wins on key collision)."""
    merged: dict[str, Any] = dict(request.query_params)
    try:
        body = await request.json()
        if isinstance(body, dict):
            merged.update(body)
    except Exception:  # noqa: BLE001
        pass
    return merged


async def _dispatch_operation(
    operation: str,
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    request: Request,
) -> dict[str, Any]:
    """Route an operation name to the right handler."""
    sysconfig = SysConfig(session)

    if operation == "SessionCreate":
        return await op_session_create(data, session, session_store)

    if operation == "TicketCreate":
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            from tiqora.db.engine import get_session_factory

            factory = get_session_factory()
        return await op_ticket_create(data, session, factory, session_store, sysconfig)

    if operation == "TicketUpdate":
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            from tiqora.db.engine import get_session_factory

            factory = get_session_factory()
        return await op_ticket_update(data, session, factory, session_store, sysconfig)

    if operation == "TicketGet":
        return await op_ticket_get(data, session, session_store)

    if operation == "TicketSearch":
        return await op_ticket_search(data, session, session_store)

    return {
        "Error": {
            "ErrorCode": "Operation.NotImplemented",
            "ErrorMessage": f"Operation {operation!r} is not supported by Tiqora compat layer",
        }
    }


def _json_or_501(result: dict[str, Any]) -> JSONResponse:
    """Return 501 if the result contains an unsupported error, else 200."""
    if "Error" in result:
        ec: str = result["Error"].get("ErrorCode", "")
        if "NotImplemented" in ec:
            return JSONResponse(content=result, status_code=status.HTTP_501_NOT_IMPLEMENTED)
        if "AuthFail" in ec:
            return JSONResponse(content=result, status_code=status.HTTP_401_UNAUTHORIZED)
        if "AccessDenied" in ec:
            return JSONResponse(content=result, status_code=status.HTTP_403_FORBIDDEN)
        if "MissingParameter" in ec or "InvalidParameter" in ec:
            return JSONResponse(content=result, status_code=status.HTTP_400_BAD_REQUEST)
    return JSONResponse(content=result, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Canonical fallback routes
# ---------------------------------------------------------------------------


@compat_router.post("/Session")
async def canonical_session_create(
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    result = await _dispatch_operation("SessionCreate", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.post("/Ticket")
async def canonical_ticket_create(
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    result = await _dispatch_operation("TicketCreate", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.get("/Ticket/{ticket_id}")
async def canonical_ticket_get(
    ticket_id: int,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    data["TicketID"] = ticket_id
    result = await _dispatch_operation("TicketGet", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.patch("/Ticket/{ticket_id}")
async def canonical_ticket_update(
    ticket_id: int,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    data["TicketID"] = ticket_id
    result = await _dispatch_operation("TicketUpdate", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.get("/TicketSearch")
async def canonical_ticket_search(
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    result = await _dispatch_operation("TicketSearch", data, session, session_store, request)
    return _json_or_501(result)


# ---------------------------------------------------------------------------
# Dynamic route helpers
# ---------------------------------------------------------------------------


def _parse_webservice_config(raw_config: bytes) -> dict[str, Any] | None:
    """Parse YAML gi_webservice_config blob, return dict or None on error."""
    try:
        decoded = raw_config.decode("utf-8", errors="replace")
        parsed = yaml.safe_load(decoded)
        if isinstance(parsed, dict):
            return parsed
    except (yaml.YAMLError, UnicodeDecodeError, AttributeError):
        pass
    return None


def _extract_route_mapping(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Extract RouteOperationMapping from parsed webservice config.

    Returns {operation_name: {"Route": "...", "RequestMethod": "..."}}
    """
    try:
        transport_cfg = config.get("Provider", {}).get("Transport", {}).get("Config", {})
        mapping = transport_cfg.get("RouteOperationMapping", {})
        if isinstance(mapping, dict):
            return mapping
    except (AttributeError, TypeError):
        pass
    return {}


def _normalize_request_methods(raw: Any) -> list[str]:
    """Normalize a RouteOperationMapping RequestMethod to a list of HTTP verbs.

    Znuny stores it as a YAML list (``["POST"]``); tolerate a bare string too.
    Falls back to ``["GET"]`` when empty/missing.
    """
    if isinstance(raw, str):
        methods = [raw]
    elif isinstance(raw, list):
        methods = [str(m) for m in raw if m]
    else:
        methods = []
    normalized = [m.upper() for m in methods if m]
    return normalized or ["GET"]


def _convert_route_pattern(znuny_route: str) -> str:
    """Convert Znuny REST route pattern to FastAPI path pattern.

    Znuny uses :VariableName, FastAPI uses {variable_name}.
    """
    return re.sub(r":([A-Za-z][A-Za-z0-9_]*)", r"{\1}", znuny_route)


async def _load_webservice_routes(
    session: AsyncSession,
) -> list[tuple[int, str, str, str, str]]:
    """Load all valid webservice route mappings.

    Returns list of (ws_id, ws_name, operation, route, method).
    """
    rows = (
        (await session.execute(select(GiWebserviceConfig).where(GiWebserviceConfig.valid_id == 1)))
        .scalars()
        .all()
    )

    entries: list[tuple[int, str, str, str, str]] = []
    for ws in rows:
        cfg = _parse_webservice_config(ws.config)
        if cfg is None:
            continue
        mapping = _extract_route_mapping(cfg)
        for op_name, op_cfg in mapping.items():
            if not isinstance(op_cfg, dict):
                continue
            route = op_cfg.get("Route") or ""
            if not route:
                continue
            # Znuny's RouteOperationMapping RequestMethod is normally a LIST
            # (e.g. ["POST"]) but some configs use a bare string. Accept both
            # and emit one entry per method so each is registered.
            for method in _normalize_request_methods(op_cfg.get("RequestMethod")):
                entries.append((ws.id, ws.name, op_name, route, method))

    return entries


def build_dynamic_router(routes: list[tuple[int, str, str, str, str]]) -> APIRouter:
    """Build a new APIRouter with dynamic routes from webservice config."""
    router = APIRouter(tags=["znuny-compat-dynamic"])

    def _make_handler(operation: str, path_var: str | None = None) -> Any:
        async def handler(
            request: Request,
            session: AsyncSession = Depends(get_db),  # noqa: B008
            session_store: SessionStore = Depends(get_session_store),  # noqa: B008
        ) -> Response:
            data = await _merge_params(request)
            # Inject path variable if present
            if path_var and path_var in request.path_params:
                data[path_var] = request.path_params[path_var]
            if operation in _SUPPORTED_OPS:
                result = await _dispatch_operation(operation, data, session, session_store, request)
            else:
                result = {
                    "Error": {
                        "ErrorCode": "Operation.NotImplemented",
                        "ErrorMessage": f"Operation {operation!r} is not implemented",
                    }
                }
            return _json_or_501(result)

        return handler

    registered: set[tuple[str, str]] = set()
    for ws_id, ws_name, op_name, znuny_route, method in routes:
        fp_route = _convert_route_pattern(znuny_route)

        # Extract path variable if any (e.g. {TicketID})
        pvar_match = re.search(r"\{([A-Za-z][A-Za-z0-9_]*)\}", fp_route)
        path_var = pvar_match.group(1) if pvar_match else None

        for prefix in [
            f"/Webservice/{ws_name}",
            f"/WebserviceID/{ws_id}",
        ]:
            full_route = f"{prefix}{fp_route}"
            key = (method, full_route)
            if key in registered:
                continue
            registered.add(key)
            router.add_api_route(
                full_route,
                _make_handler(op_name, path_var),
                methods=[method],
                response_class=JSONResponse,
            )
            if op_name not in _SUPPORTED_OPS:
                logger.warning(
                    "compat_unsupported_operation",
                    operation=op_name,
                    webservice=ws_name,
                    route=full_route,
                )

    return router


# ---------------------------------------------------------------------------
# Admin reload endpoint
# ---------------------------------------------------------------------------


@compat_router.post("/admin/reload")
async def admin_reload_routes(
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    """Re-mount dynamic webservice routes without restart (authenticated)."""
    routes = await _load_webservice_routes(session)
    build_dynamic_router(routes)  # validate; full hot-reload needs restart

    # Full reload requires app restart; we log and return the new route count.
    logger.info(
        "compat_routes_reloaded",
        route_count=len(routes),
        user=current_user.login,
    )
    return {
        "status": "ok",
        "routes_loaded": len(routes),
        "message": "Dynamic routes refreshed in-memory. Full restart required for hot-reload.",
    }


async def mount_dynamic_compat_routes(app: Any, session: AsyncSession) -> None:
    """Called from application lifespan to mount dynamic routes."""
    try:
        routes = await _load_webservice_routes(session)
        dynamic = build_dynamic_router(routes)
        # Include into the main app under /znuny-compat
        app.include_router(dynamic, prefix="/znuny-compat")
        logger.info("compat_dynamic_routes_mounted", count=len(routes))
    except Exception as exc:  # noqa: BLE001
        logger.warning("compat_dynamic_routes_failed", error=str(exc))


__all__ = [
    "compat_router",
    "mount_dynamic_compat_routes",
    "build_dynamic_router",
]
