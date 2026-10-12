"""Znuny GenericInterface compatibility routes.

Canonical routes (always available, no webservice, no mapping):
  POST   /znuny-compat/Session              -> SessionCreate
  GET    /znuny-compat/Session/:SessionID   -> SessionGet
  DELETE /znuny-compat/Session/:SessionID   -> SessionRemove
  POST   /znuny-compat/Ticket               -> TicketCreate
  GET    /znuny-compat/Ticket/:TicketID     -> TicketGet
  PATCH  /znuny-compat/Ticket/:TicketID     -> TicketUpdate
  GET    /znuny-compat/TicketSearch         -> TicketSearch
  GET    /znuny-compat/Ticket/History/:id   -> TicketHistoryGet
  GET    /znuny-compat/TimeAccountingGet    -> TimeAccountingGet
  POST   /znuny-compat/OutOfOffice          -> OutOfOffice
  POST   /znuny-compat/soap/{webservice}    -> SOAP, dispatched via Body wrapper

Webservice routes are resolved per request from the in-memory
:class:`~tiqora.api.compat.webservices.WebserviceRegistry` (no route mounting at startup):
  /znuny-compat/Webservice/{name}[/{route}]
  /znuny-compat/WebserviceID/{id}[/{route}]

Admin reload endpoint: POST /znuny-compat/admin/reload (admin only).

On the canonical routes query-string AND JSON-body parameters are merged (body wins).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.operations import (
    op_out_of_office,
    op_session_create,
    op_session_get,
    op_session_remove,
    op_ticket_create,
    op_ticket_get,
    op_ticket_history_get,
    op_ticket_search,
    op_ticket_update,
    op_time_accounting_get,
)
from tiqora.api.compat.provider import (
    OperationFault,
    ProviderOutcome,
    error_status_code,
    provider_log,
    render_rest,
    rest_fault_body,
    run_provider,
)
from tiqora.api.compat.reload import publish_webservices_changed
from tiqora.api.compat.rest_request import (
    RestRequestError,
    build_rest_request_data,
    rest_request_path,
)
from tiqora.api.compat.soap import (
    DEFAULT_NAMESPACE,
    SoapCodecError,
    build_soap_fault,
    build_soap_response,
    content_type_for_version,
    parse_soap_request,
)
from tiqora.api.compat.webservices import (
    OPERATION_TYPES,
    OperationConfig,
    RouteMatchError,
    Webservice,
    get_registry,
    match_rest_route,
)
from tiqora.api.deps import DbSession, get_session_store
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.domain.auth import SessionStore
from tiqora.gi.config import parse_debugger
from tiqora.gi.debug_store import BufferedDebugSink
from tiqora.gi.debugger import GiDebugger
from tiqora.security.ratelimit import client_ip
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Handler keys the canonical SOAP route accepts. SessionDelete is an alias used by
# GenericTicketConnectorREST-style configs (Type Session::SessionRemove).
_OPERATION_ALIASES: dict[str, str] = {"SessionDelete": "SessionRemove"}
_CANONICAL_OPS: frozenset[str] = frozenset(OPERATION_TYPES.values()) | frozenset(_OPERATION_ALIASES)

compat_router = APIRouter(prefix="/znuny-compat", tags=["znuny-compat"])


async def _open_debugger(
    request: Request, webservice: Webservice, *, name: str | None
) -> GiDebugger:
    """The provider debugger of one webservice request (Provider.pm: ``Debugger->new`` with
    the webservice's ``Debugger`` config) plus its ``Communication sequence started`` line.

    Lines are buffered per request (:class:`BufferedDebugSink`) and written by
    :func:`_flush_debugger` after the request session released its connection, in their own
    session, so a rolled-back operation keeps its trail. The remote IP honours
    ``TIQORA_TRUSTED_PROXIES``.
    """
    sink: BufferedDebugSink | None = getattr(request.state, "gi_debug_sink", None)
    if sink is None:
        factory = getattr(request.app.state, "session_factory", None)
        if factory is None:
            from tiqora.db.engine import get_session_factory

            factory = get_session_factory()
        sink = BufferedDebugSink(factory)
        request.state.gi_debug_sink = sink
    settings = getattr(request.app.state, "settings", None)
    debugger = GiDebugger(
        sink=sink,
        config=parse_debugger(webservice.config),
        webservice_id=webservice.id,
        communication_type="Provider",
        remote_ip=client_ip(request, getattr(settings, "trusted_proxies", None)),
        name=name,
    )
    # Znuny logs %ENV here; the request line is what matters (no query string, no headers:
    # both may carry credentials)
    await provider_log(
        debugger,
        "debug",
        "Communication sequence started",
        {"RequestMethod": request.method, "RequestURI": request.url.path},
    )
    return debugger


async def _flush_debugger(request: Request, session: AsyncSession) -> None:
    """Write the buffered provider debugger lines of this request; never raises.

    The request session is closed first (it holds no uncommitted work the response needs:
    ``get_db`` would only roll it back later), so the flush never waits for a second pooled
    connection while the request still holds one.
    """
    sink: BufferedDebugSink | None = getattr(request.state, "gi_debug_sink", None)
    if sink is None or not sink.pending:
        return
    try:
        await session.close()
        await sink.flush()
    except Exception:  # noqa: BLE001
        logger.warning("gi_provider_debugger_failed", summary="flush", exc_info=True)


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
    operation_config: OperationConfig | None = None,
) -> dict[str, Any]:
    """Route a handler key (``OPERATION_TYPES`` value or alias) to the right ``op_*`` handler."""
    sysconfig = SysConfig(session)
    settings = getattr(request.app.state, "settings", None)
    # Normalize aliases (e.g. SessionDelete → SessionRemove).
    operation = _OPERATION_ALIASES.get(operation, operation)

    if operation == "SessionCreate":
        return await op_session_create(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "SessionGet":
        return await op_session_get(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "SessionRemove":
        return await op_session_remove(
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
            operation_config=operation_config,
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
            operation_config=operation_config,
        )

    if operation == "TicketGet":
        return await op_ticket_get(data, session, session_store, request=request, settings=settings)

    if operation == "TicketSearch":
        return await op_ticket_search(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "TicketHistoryGet":
        return await op_ticket_history_get(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "TimeAccountingGet":
        return await op_time_accounting_get(
            data, session, session_store, request=request, settings=settings
        )

    if operation == "OutOfOffice":
        return await op_out_of_office(
            data, session, session_store, request=request, settings=settings
        )

    return {
        "Error": {
            "ErrorCode": "Operation.NotImplemented",
            "ErrorMessage": f"Operation {operation!r} is not supported by Tiqora compat layer",
        }
    }


def _error_status_code(result: dict[str, Any]) -> int:
    """Status for a Znuny-style ``{"Error": ...}`` result (shared by REST and SOAP)."""
    return error_status_code(result)


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
    try:
        result = await _dispatch_operation("TicketUpdate", data, session, session_store, request)
    except OperationFault as exc:
        return _fault_response(exc.message, status.HTTP_500_INTERNAL_SERVER_ERROR)
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


@compat_router.get("/Session/{session_id}")
async def canonical_session_get(
    session_id: str,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    data["SessionID"] = session_id
    result = await _dispatch_operation("SessionGet", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.delete("/Session/{session_id}")
async def canonical_session_remove(
    session_id: str,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    data["SessionID"] = session_id
    result = await _dispatch_operation("SessionRemove", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.get("/Ticket/History/{ticket_id}")
async def canonical_ticket_history_get(
    ticket_id: int,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    data["TicketID"] = ticket_id
    result = await _dispatch_operation("TicketHistoryGet", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.get("/TimeAccountingGet")
async def canonical_time_accounting_get(
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    result = await _dispatch_operation("TimeAccountingGet", data, session, session_store, request)
    return _json_or_501(result)


@compat_router.post("/OutOfOffice")
async def canonical_out_of_office(
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    data = await _merge_params(request)
    try:
        result = await _dispatch_operation("OutOfOffice", data, session, session_store, request)
    except OperationFault as exc:
        return _fault_response(exc.message, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return _json_or_501(result)


# ---------------------------------------------------------------------------
# SOAP transport
# ---------------------------------------------------------------------------


def _soap_response(content: bytes, version: str, status_code: int) -> Response:
    return Response(
        content=content, media_type=content_type_for_version(version), status_code=status_code
    )


async def _read_body_capped(request: Request, max_length: int) -> bytes | None:
    """The request body, or ``None`` as soon as more than ``max_length`` bytes arrived.

    Consumes the request stream: ``request.body()`` cannot be called afterwards.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_length:
            return None
        chunks.append(chunk)
    # Starlette has no public way to hand a streamed body back to ``request.body()``; the
    # SOAP handler passes these bytes on itself, and nothing after it reads the request body
    return b"".join(chunks)


async def _handle_soap_request(
    request: Request,
    session: AsyncSession,
    session_store: SessionStore,
    *,
    namespace: str,
    webservice: Webservice | None = None,
) -> Response:
    """Decode a SOAP envelope, run it, and encode a SOAP response or Fault envelope.

    Without ``webservice`` (canonical ``/soap/{name}`` route) the operation is dispatched by
    its name with no mapping. With a webservice the operation name selects the configured
    operation (type + mapping) through the provider pipeline.
    """
    # SOAP.pm:160-165: a body longer than the transport's MaxLength is refused with 413
    # (text/plain, like every Znuny transport error) before it is parsed or mapped
    max_length = webservice.max_length if webservice is not None else None
    if max_length is not None:
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > max_length:
            return _text_error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request Entity Too Large")
        # a body without Content-Length (chunked) is read with a running count and refused
        # as soon as it passes the limit - never read completely first
        body = await _read_body_capped(request, max_length)
        if body is None:
            return _text_error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request Entity Too Large")
        body_bytes = body
    else:
        body_bytes = await request.body()
    soap_action = request.headers.get("SOAPAction") or request.headers.get("soapaction")

    try:
        parsed = parse_soap_request(body_bytes, soap_action)
    except SoapCodecError as exc:
        if webservice is not None:
            debugger = await _open_debugger(request, webservice, name=None)
            await provider_log(debugger, "error", str(exc))
        fault = build_soap_fault(str(exc), fault_code="Client")
        return _soap_response(fault, "1.1", status.HTTP_400_BAD_REQUEST)

    if webservice is not None:

        async def _run(handler: str, data: dict[str, Any], op: OperationConfig) -> dict[str, Any]:
            return await _dispatch_operation(
                handler, data, session, session_store, request, operation_config=op
            )

        debugger = await _open_debugger(request, webservice, name=parsed.operation)
        outcome = await run_provider(
            webservice, parsed.operation, parsed.data, run_operation=_run, debugger=debugger
        )
        return _render_soap(outcome, parsed.operation, parsed.version, namespace)

    if parsed.operation not in _CANONICAL_OPS:
        logger.warning("compat_soap_unsupported_operation", operation=parsed.operation)
        fault = build_soap_fault(
            f"Operation {parsed.operation!r} is not supported by Tiqora compat layer",
            fault_code="Client",
            version=parsed.version,
        )
        return _soap_response(fault, parsed.version, status.HTTP_501_NOT_IMPLEMENTED)

    try:
        result = await _dispatch_operation(
            parsed.operation, parsed.data, session, session_store, request
        )
    except OperationFault as exc:
        return _render_soap(
            ProviderOutcome(500, {}, {}, exc.message), parsed.operation, parsed.version, namespace
        )
    return _render_soap(
        ProviderOutcome(_error_status_code(result), result, {}, None),
        parsed.operation,
        parsed.version,
        namespace,
    )


def _render_soap(
    outcome: ProviderOutcome, operation: str, version: str, namespace: str
) -> Response:
    body = outcome.body
    if outcome.fault is not None:
        xml = build_soap_fault(outcome.fault, fault_code="Server", version=version)
        return _soap_response(xml, version, outcome.status_code)
    if isinstance(body, dict) and isinstance(body.get("Error"), dict):
        err = body["Error"]
        fault_string = f"{err.get('ErrorCode', 'Error')}: {err.get('ErrorMessage', '')}"
        xml = build_soap_fault(fault_string, fault_code="Client", version=version)
    else:
        xml = build_soap_response(
            operation, body if isinstance(body, dict) else {}, namespace=namespace, version=version
        )
    response = _soap_response(xml, version, outcome.status_code)
    response.headers.update(outcome.headers)
    return response


@compat_router.post("/soap/{webservice}")
async def canonical_soap_endpoint(
    webservice: str,  # noqa: ARG001 - accepted for URL-path parity with Znuny; operation comes from the Body
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
) -> Response:
    """Canonical always-available SOAP provider endpoint (default NameSpace, no mapping)."""
    return await _handle_soap_request(request, session, session_store, namespace=DEFAULT_NAMESPACE)


# ---------------------------------------------------------------------------
# Webservice routes (resolved per request from the registry)
# ---------------------------------------------------------------------------

_WS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _fault_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(rest_fault_body(message), status_code=status_code)


def _text_error(status_code: int, message: str) -> Response:
    """Znuny's REST transport errors are ``text/plain`` (``REST.pm`` ``_Output``)."""
    return Response(content=message, status_code=status_code, media_type="text/plain")


async def _serve_webservice(
    request: Request,
    session: AsyncSession,
    session_store: SessionStore,
    *,
    ws_name: str | None,
    ws_id: int | None,
) -> Response:
    try:
        return await _serve_webservice_inner(
            request, session, session_store, ws_name=ws_name, ws_id=ws_id
        )
    finally:
        await _flush_debugger(request, session)


async def _serve_webservice_inner(
    request: Request,
    session: AsyncSession,
    session_store: SessionStore,
    *,
    ws_name: str | None,
    ws_id: int | None,
) -> Response:
    registry = get_registry(request.app)
    await registry.ensure_loaded(session)
    ws = registry.by_id(ws_id) if ws_id is not None else registry.by_name(ws_name or "")
    if ws is None:
        return _fault_response(
            f"Could not find valid web service for query string '{request.url.path}'",
            status.HTTP_404_NOT_FOUND,
        )

    if ws.transport == "HTTP::SOAP":
        if request.method != "POST" or rest_request_path(request) != "/":
            return _fault_response(
                f"Could not find valid web service for query string '{request.url.path}'",
                status.HTTP_404_NOT_FOUND,
            )
        return await _handle_soap_request(
            request, session, session_store, namespace=ws.namespace, webservice=ws
        )

    if ws.transport != "HTTP::REST":
        return _fault_response(f"Transport '{ws.transport}' is not supported by Tiqora", 500)

    path = rest_request_path(request)
    try:
        matched = match_rest_route(ws, path, request.method)
    except RouteMatchError as exc:
        message = f"HTTP::REST Error while determine Operation: {exc}"
        await provider_log(await _open_debugger(request, ws, name=None), "error", message)
        return _text_error(500, message)
    if matched is None:
        message = f"HTTP::REST Error while determine Operation for request URI '{path}'."
        await provider_log(await _open_debugger(request, ws, name=None), "error", message)
        return _text_error(500, message)
    operation, uri_data = matched
    debugger = await _open_debugger(request, ws, name=operation)
    try:
        data = await build_rest_request_data(request, ws, uri_data)
    except RestRequestError as exc:
        await provider_log(debugger, "error", exc.message)
        return _text_error(exc.status, exc.message)

    async def _run(handler: str, op_data: dict[str, Any], op: OperationConfig) -> dict[str, Any]:
        return await _dispatch_operation(
            handler, op_data, session, session_store, request, operation_config=op
        )

    outcome = await run_provider(ws, operation, data, run_operation=_run, debugger=debugger)
    return render_rest(outcome)


@compat_router.api_route("/Webservice/{ws_name}", methods=_WS_METHODS, include_in_schema=False)
@compat_router.api_route(
    "/Webservice/{ws_name}/{subpath:path}", methods=_WS_METHODS, include_in_schema=False
)
async def webservice_by_name(
    ws_name: str,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
    subpath: str = "",  # noqa: ARG001 - the raw path is re-read from the ASGI scope
) -> Response:
    return await _serve_webservice(request, session, session_store, ws_name=ws_name, ws_id=None)


@compat_router.api_route("/WebserviceID/{ws_id:int}", methods=_WS_METHODS, include_in_schema=False)
@compat_router.api_route(
    "/WebserviceID/{ws_id:int}/{subpath:path}", methods=_WS_METHODS, include_in_schema=False
)
async def webservice_by_id(
    ws_id: int,
    request: Request,
    session: DbSession,
    session_store: SessionStore = Depends(get_session_store),  # noqa: B008
    subpath: str = "",  # noqa: ARG001 - the raw path is re-read from the ASGI scope
) -> Response:
    return await _serve_webservice(request, session, session_store, ws_name=None, ws_id=ws_id)


# ---------------------------------------------------------------------------
# Admin reload endpoint
# ---------------------------------------------------------------------------


@compat_router.post("/admin/reload")
async def admin_reload_routes(
    request: Request,
    current_user: AdminUser,
    session: DbSession,
) -> dict[str, Any]:
    """Reload the webservice registry from ``gi_webservice_config`` (admin only)."""
    registry = get_registry(request.app)
    await registry.refresh(session, force=True)
    await publish_webservices_changed(getattr(request.app.state, "redis", None))
    names = registry.names()
    logger.info("compat_webservices_reloaded", count=len(names), user=current_user.login)
    return {"status": "ok", "webservices_loaded": len(names), "webservices": names}


__all__ = ["compat_router"]
