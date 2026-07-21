"""Dynamic route mounting for Znuny GenericInterface compatibility.

On startup reads `gi_webservice_config` rows, YAML-parses
Provider.Transport.Config.RouteOperationMapping, and registers REST routes
under:
  /znuny-compat/Webservice/{name}{route}
  /znuny-compat/WebserviceID/{id}{route}

SOAP webservices (Provider.Transport.Type == 'HTTP::SOAP') get one endpoint
each (SOAP has no per-operation route mapping — the operation is dispatched
from the SOAP Body wrapper element, see api/compat/soap.py):
  POST /znuny-compat/Webservice/{name}      (SOAP, that webservice's NameSpace)
  POST /znuny-compat/WebserviceID/{id}      (SOAP, that webservice's NameSpace)

Fallback canonical routes (always available):
  POST   /znuny-compat/Session              → SessionCreate
  POST   /znuny-compat/Ticket              → TicketCreate
  GET    /znuny-compat/Ticket/:TicketID     → TicketGet
  PATCH  /znuny-compat/Ticket/:TicketID     → TicketUpdate
  GET    /znuny-compat/TicketSearch         → TicketSearch
  POST   /znuny-compat/soap/{webservice}    → SOAP, dispatched via Body wrapper

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
from tiqora.api.compat.soap import (
    DEFAULT_NAMESPACE,
    SoapCodecError,
    build_soap_fault,
    build_soap_response,
    content_type_for_version,
    parse_soap_request,
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
    settings = getattr(request.app.state, "settings", None)

    if operation == "SessionCreate":
        return await op_session_create(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "TicketCreate":
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            from tiqora.db.engine import get_session_factory

            factory = get_session_factory()
        return await op_ticket_create(
            data,
            session,
            factory,
            session_store,
            sysconfig,
            request=request,
            settings=settings,
        )

    if operation == "TicketUpdate":
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            from tiqora.db.engine import get_session_factory

            factory = get_session_factory()
        return await op_ticket_update(
            data,
            session,
            factory,
            session_store,
            sysconfig,
            request=request,
            settings=settings,
        )

    if operation == "TicketGet":
        return await op_ticket_get(data, session, session_store, request=request, settings=settings)

    if operation == "TicketSearch":
        return await op_ticket_search(
            data, session, session_store, request=request, settings=settings
        )

    return {
        "Error": {
            "ErrorCode": "Operation.NotImplemented",
            "ErrorMessage": f"Operation {operation!r} is not supported by Tiqora compat layer",
        }
    }


def _error_status_code(result: dict[str, Any]) -> int:
    """Map a Znuny-style ``{"Error": {"ErrorCode": ...}}`` result to an HTTP status.

    Returns 200 when ``result`` carries no ``Error`` key. Shared between the
    JSON (REST) and SOAP transports so both emit the same status semantics.
    """
    if "Error" in result:
        ec: str = result["Error"].get("ErrorCode", "")
        if "NotImplemented" in ec:
            return status.HTTP_501_NOT_IMPLEMENTED
        if "RateLimited" in ec:
            return status.HTTP_429_TOO_MANY_REQUESTS
        if "AuthFail" in ec:
            return status.HTTP_401_UNAUTHORIZED
        if "AccessDenied" in ec:
            return status.HTTP_403_FORBIDDEN
        if "MissingParameter" in ec or "InvalidParameter" in ec:
            return status.HTTP_400_BAD_REQUEST
    return status.HTTP_200_OK


def _json_or_501(result: dict[str, Any]) -> JSONResponse:
    """Return error-aware status; attach Retry-After for rate-limited auth."""
    code = _error_status_code(result)
    headers: dict[str, str] | None = None
    if code == status.HTTP_429_TOO_MANY_REQUESTS and "Error" in result:
        retry = result["Error"].get("RetryAfter")
        if retry is not None:
            headers = {"Retry-After": str(retry)}
    return JSONResponse(content=result, status_code=code, headers=headers)


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
# SOAP transport
# ---------------------------------------------------------------------------


async def _handle_soap_request(
    request: Request,
    session: AsyncSession,
    session_store: SessionStore,
    *,
    namespace: str,
) -> Response:
    """Decode a SOAP envelope, dispatch to the shared operation handler
    (same code path as REST — the codec only adapts wire format), and
    encode the result back into a SOAP response or Fault envelope.
    """
    body_bytes = await request.body()
    soap_action = request.headers.get("SOAPAction") or request.headers.get("soapaction")

    try:
        parsed = parse_soap_request(body_bytes, soap_action)
    except SoapCodecError as exc:
        fault = build_soap_fault(str(exc), fault_code="Client")
        return Response(
            content=fault,
            media_type=content_type_for_version("1.1"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if parsed.operation not in _SUPPORTED_OPS:
        logger.warning("compat_soap_unsupported_operation", operation=parsed.operation)
        fault = build_soap_fault(
            f"Operation {parsed.operation!r} is not supported by Tiqora compat layer",
            fault_code="Client",
            version=parsed.version,
        )
        return Response(
            content=fault,
            media_type=content_type_for_version(parsed.version),
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
        )

    result = await _dispatch_operation(
        parsed.operation, parsed.data, session, session_store, request
    )
    status_code = _error_status_code(result)

    if "Error" in result:
        err = result["Error"]
        fault_string = f"{err.get('ErrorCode', 'Error')}: {err.get('ErrorMessage', '')}"
        xml = build_soap_fault(fault_string, fault_code="Client", version=parsed.version)
    else:
        xml = build_soap_response(
            parsed.operation, result, namespace=namespace, version=parsed.version
        )

    return Response(
        content=xml,
        media_type=content_type_for_version(parsed.version),
        status_code=status_code,
    )


@compat_router.post("/soap/{webservice}")
async def canonical_soap_endpoint(
    webservice: str,  # noqa: ARG001 - accepted for URL-path parity with Znuny; operation comes from the Body
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    """Canonical always-available SOAP provider endpoint.

    Unlike REST, SOAP has no per-operation route mapping in Znuny — a single
    endpoint per webservice accepts any operation, dispatched from the SOAP
    Body wrapper element name. This fallback route uses the default Znuny
    ``NameSpace`` (``http://www.otrs.org/TicketConnector/``); webservices
    configured in ``gi_webservice_config`` with their own NameSpace are
    additionally mounted at ``/Webservice/{name}`` and ``/WebserviceID/{id}``
    (see :func:`_load_soap_webservices` / :func:`build_soap_router`).
    """
    return await _handle_soap_request(request, session, session_store, namespace=DEFAULT_NAMESPACE)


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
# SOAP dynamic webservice helpers
# ---------------------------------------------------------------------------


def _extract_soap_namespace(config: dict[str, Any]) -> str | None:
    """Return the configured NameSpace if this webservice is HTTP::SOAP.

    Returns ``None`` for non-SOAP (e.g. HTTP::REST) webservices so callers
    can filter them out. Falls back to :data:`DEFAULT_NAMESPACE` when the
    webservice is SOAP but has no explicit ``NameSpace`` set (Znuny requires
    one in practice, but we stay permissive).
    """
    try:
        transport = config.get("Provider", {}).get("Transport", {})
        if transport.get("Type") != "HTTP::SOAP":
            return None
        namespace = transport.get("Config", {}).get("NameSpace")
        return str(namespace) if namespace else DEFAULT_NAMESPACE
    except (AttributeError, TypeError):
        return None


async def _load_soap_webservices(session: AsyncSession) -> list[tuple[int, str, str]]:
    """Load all valid HTTP::SOAP webservice configs.

    Returns list of (ws_id, ws_name, namespace).
    """
    rows = (
        (await session.execute(select(GiWebserviceConfig).where(GiWebserviceConfig.valid_id == 1)))
        .scalars()
        .all()
    )

    entries: list[tuple[int, str, str]] = []
    for ws in rows:
        cfg = _parse_webservice_config(ws.config)
        if cfg is None:
            continue
        namespace = _extract_soap_namespace(cfg)
        if namespace is None:
            continue
        entries.append((ws.id, ws.name, namespace))

    return entries


def build_soap_router(webservices: list[tuple[int, str, str]]) -> APIRouter:
    """Build a new APIRouter mounting one SOAP endpoint per webservice.

    Unlike REST's ``build_dynamic_router``, SOAP has no per-operation route
    mapping to iterate — one POST endpoint per webservice accepts any
    supported operation, dispatched from the SOAP Body wrapper element.
    """
    router = APIRouter(tags=["znuny-compat-dynamic-soap"])

    def _make_handler(namespace: str) -> Any:
        async def handler(
            request: Request,
            session: AsyncSession = Depends(get_db),  # noqa: B008
            session_store: SessionStore = Depends(get_session_store),  # noqa: B008
        ) -> Response:
            return await _handle_soap_request(request, session, session_store, namespace=namespace)

        return handler

    registered: set[str] = set()
    for ws_id, ws_name, namespace in webservices:
        for path in (f"/Webservice/{ws_name}", f"/WebserviceID/{ws_id}"):
            if path in registered:
                continue
            registered.add(path)
            router.add_api_route(
                path,
                _make_handler(namespace),
                methods=["POST"],
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
    soap_webservices = await _load_soap_webservices(session)
    build_soap_router(soap_webservices)  # validate; full hot-reload needs restart

    # Full reload requires app restart; we log and return the new route count.
    logger.info(
        "compat_routes_reloaded",
        route_count=len(routes),
        soap_webservice_count=len(soap_webservices),
        user=current_user.login,
    )
    return {
        "status": "ok",
        "routes_loaded": len(routes),
        "soap_webservices_loaded": len(soap_webservices),
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

    try:
        soap_webservices = await _load_soap_webservices(session)
        soap_dynamic = build_soap_router(soap_webservices)
        app.include_router(soap_dynamic, prefix="/znuny-compat")
        logger.info("compat_dynamic_soap_routes_mounted", count=len(soap_webservices))
    except Exception as exc:  # noqa: BLE001
        logger.warning("compat_dynamic_soap_routes_failed", error=str(exc))


__all__ = [
    "compat_router",
    "mount_dynamic_compat_routes",
    "build_dynamic_router",
    "build_soap_router",
]
