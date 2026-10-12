"""``/scim/v2`` routes (S5): discovery, Users, Groups; every answer is a SCIM document.

Authentication is a router-level dependency (:func:`require_scim_tenant`); endpoints take the
``Request`` and parse manually (no Pydantic bodies), call the service, commit once, end the
sessions of a deactivated account and only then answer. The route class turns every exception
into a SCIM error body (also for FastAPI's own 404/405/422 under this prefix), rolls the request
session back on failure and writes one audit event per write (own session, S8).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Final

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from tiqora.api.deps import get_app_settings, get_redis
from tiqora.api.scim.auth import (
    SCIM_PREFIX,
    ScimContext,
    ScimHttpError,
    require_scim_tenant,
    resource_of,
)
from tiqora.api.scim.body import read_scim_json
from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.session_revocation import end_agent_sessions, end_customer_sessions
from tiqora.identity.scim.audit import ScimOperation, record_event
from tiqora.identity.scim.errors import ScimError, scim_error_body
from tiqora.identity.scim.groups import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    patch_group,
    replace_group,
)
from tiqora.identity.scim.patch import parse_patch
from tiqora.identity.scim.protocol import (
    ListParams,
    list_response,
    parse_filter,
    parse_list_params,
    project,
    resource_types,
    schemas_document,
    service_provider_config,
)
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users import (
    ScimConflictError,
    ScimUserResult,
    create_user,
    delete_user,
    get_user,
    list_users,
    patch_user,
    replace_user,
)
from tiqora.identity.urls import base_url

__all__ = ["SCIM_PREFIX", "scim_router"]

SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]

logger = structlog.get_logger(__name__)

_WRITES: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ALL_METHODS: Final = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_GROUPS_UNAVAILABLE: Final = "Groups are not available for customer provisioning"
_MAX_ID_LENGTH: Final = 64
_CATCH_ALL: Final = "/{rest:path}"


class ScimResponse(JSONResponse):
    media_type = "application/scim+json"


def scim_response(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> Response:
    return ScimResponse(content=body, status_code=status, headers=headers)


def _error(
    status: int, scim_type: Any, detail: str, headers: dict[str, str] | None = None
) -> Response:
    return scim_response(status, scim_error_body(status, scim_type, detail), headers)


def _to_scim_error(exc: Exception) -> ScimError:
    """Map any exception raised under this router to the SCIM error it answers with."""
    if isinstance(exc, ScimError):
        return exc
    if isinstance(exc, StarletteHTTPException):
        return ScimError(exc.status_code, None, "Request failed")
    if isinstance(exc, RequestValidationError):
        return ScimError(400, "invalidSyntax", "Malformed request")
    if isinstance(exc, IntegrityError):
        return ScimConflictError("race", "Conflicting update, retry the request")
    if isinstance(exc, DataError):
        return ScimError(400, "invalidValue", "A value is not acceptable")
    if isinstance(exc, OperationalError):
        return ScimHttpError(503, None, "Temporarily unavailable", {"Retry-After": "1"})
    logger.error("scim.unhandled_error", error_type=type(exc).__name__, exc_info=exc)
    return ScimError(500, None, "Internal server error")


async def _rollback_quietly(ctx: ScimContext) -> None:
    try:
        await ctx.db.rollback()
    except Exception:  # noqa: BLE001 — the failed request is already being answered
        logger.warning("scim.rollback_failed")


async def _finish(request: Request, response: Response, error: ScimError | None) -> None:
    """Structured request log for every call; one audit event for authenticated writes."""
    ctx: ScimContext | None = getattr(request.state, "scim", None)
    if ctx is None:
        return
    status = response.status_code
    operation = ctx.operation
    detail = ctx.detail
    if error is not None:
        operation = "rejected"
        detail = error.scim_type or f"status_{status}"
        if isinstance(error, ScimConflictError):
            operation, detail = "conflict", error.reason
    logger.info(
        "scim.request",
        provider_id=ctx.tenant.provider.id,
        token_id=ctx.tenant.token_id,
        operation=operation or request.method.lower(),
        status=status,
        duration_ms=int((time.monotonic() - ctx.started) * 1000),
    )
    if request.method not in _WRITES:
        return
    await record_event(
        ctx.factory,
        provider_id=ctx.tenant.provider.id,
        token_id=ctx.tenant.token_id,
        method=request.method[:8],
        resource=resource_of(request.url.path),
        operation=operation or "rejected",
        status=status,
        scim_id=ctx.scim_id or request.path_params.get("scim_id"),
        user_kind=ctx.tenant.user_kind,
        user_id=ctx.user_id,
        detail=detail,
    )


class ScimRoute(APIRoute):
    """Route class that answers every outcome with a SCIM document."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            error: ScimError | None = None
            try:
                response = await original(request)
            except Exception as exc:  # noqa: BLE001 — everything becomes a SCIM error body
                error = _to_scim_error(exc)
                ctx: ScimContext | None = getattr(request.state, "scim", None)
                if ctx is not None:
                    await _rollback_quietly(ctx)
                headers = getattr(error, "headers", None)
                response = _error(error.status, error.scim_type, error.detail, headers)
            await _finish(request, response, error)
            return response

        return handler


scim_router = APIRouter(
    route_class=ScimRoute,
    include_in_schema=False,
    dependencies=[Depends(require_scim_tenant)],
)


# --------------------------------------------------------------------------- helpers


def _ctx(request: Request) -> ScimContext:
    ctx: ScimContext = request.state.scim
    return ctx


def _base(request: Request, settings: Settings) -> str:
    root = base_url(settings) if settings.public_base_url.strip() else str(request.base_url)
    return root.rstrip("/") + SCIM_PREFIX


def _not_found() -> ScimError:
    return ScimError(404, None, "Resource not found")


def _clean_id(raw: str) -> str:
    """A resource id from the path; anything that cannot be one of ours is a plain 404."""
    if len(raw) > _MAX_ID_LENGTH or not raw.isascii() or not raw.isprintable():
        raise _not_found()
    return raw


def _agent_only(tenant: ScimTenant) -> None:
    if tenant.user_kind != "agent":
        raise ScimError(404, None, _GROUPS_UNAVAILABLE)


def _wants_members(params: ListParams) -> bool:
    if params.attributes is not None and "members" not in params.attributes:
        return False
    return "members" not in params.excluded


def _created(resource: dict[str, Any]) -> Response:
    location = str(resource.get("meta", {}).get("location", ""))
    return scim_response(201, resource, {"Location": location} if location else None)


async def _after_user_write(
    ctx: ScimContext, settings: Settings, redis: Redis, result: ScimUserResult
) -> None:
    """Commit-then-end-sessions: a deactivated account has no live session when we answer."""
    ctx.operation = result.operation
    ctx.user_id = result.user_id
    ctx.detail = result.detail
    if result.resource.get("id"):
        ctx.scim_id = str(result.resource["id"])
    if not result.deactivated:
        return
    if ctx.tenant.user_kind == "agent":
        await end_agent_sessions(ctx.db, redis, settings, result.user_id, reason="scim")
        return
    login = (
        await ctx.db.execute(select(CustomerUser.login).where(CustomerUser.id == result.user_id))
    ).scalar_one_or_none()
    if login:
        await end_customer_sessions(ctx.db, redis, settings, [str(login)], reason="scim")


# --------------------------------------------------------------------------- discovery


@scim_router.get("/ServiceProviderConfig")
async def get_service_provider_config(request: Request, settings: SettingsDep) -> Response:
    return scim_response(200, service_provider_config(_base(request, settings)))


@scim_router.get("/ResourceTypes")
async def get_resource_types(request: Request, settings: SettingsDep) -> Response:
    tenant = _ctx(request).tenant
    docs = resource_types(_base(request, settings), groups=tenant.user_kind == "agent")
    return scim_response(200, list_response(len(docs), docs, parse_list_params({})))


@scim_router.get("/ResourceTypes/{name}")
async def get_resource_type(name: str, request: Request, settings: SettingsDep) -> Response:
    tenant = _ctx(request).tenant
    for doc in resource_types(_base(request, settings), groups=tenant.user_kind == "agent"):
        if str(doc["id"]).lower() == name.lower():
            return scim_response(200, doc)
    raise _not_found()


def _schemas_for(tenant: ScimTenant) -> list[dict[str, Any]]:
    docs = schemas_document()
    if tenant.user_kind == "agent":
        return docs
    return [d for d in docs if d.get("name") != "Group"]


@scim_router.get("/Schemas")
async def get_schemas(request: Request) -> Response:
    docs = _schemas_for(_ctx(request).tenant)
    return scim_response(200, list_response(len(docs), docs, parse_list_params({})))


@scim_router.get("/Schemas/{urn}")
async def get_schema(urn: str, request: Request) -> Response:
    for doc in _schemas_for(_ctx(request).tenant):
        if str(doc["id"]).lower() == urn.lower():
            return scim_response(200, doc)
    raise _not_found()


# --------------------------------------------------------------------------- users


@scim_router.get("/Users")
async def users_list(request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    params = parse_list_params(request.query_params)
    flt = parse_filter(request.query_params.get("filter"), resource="User")
    total, docs = await list_users(ctx.db, ctx.tenant, flt, params, base=_base(request, settings))
    return scim_response(200, list_response(total, [project(d, params) for d in docs], params))


@scim_router.post("/Users")
async def users_create(
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    ctx = _ctx(request)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    result = await create_user(ctx.db, ctx.tenant, doc, base=_base(request, settings))
    await ctx.db.commit()
    await _after_user_write(ctx, settings, redis, result)
    return _created(result.resource)


# Declared before ``/Users/{scim_id}`` so ``.search`` is never read as a resource id.
async def _not_implemented(request: Request) -> Response:
    raise ScimError(501, None, "Not implemented")


for _path in ("/Bulk", "/Me", "/Bulk/{rest:path}", "/Me/{rest:path}"):
    scim_router.add_api_route(_path, _not_implemented, methods=_ALL_METHODS)
for _path in ("/.search", "/Users/.search", "/Groups/.search"):
    scim_router.add_api_route(_path, _not_implemented, methods=["POST"])


@scim_router.get("/Users/{scim_id}")
async def users_get(scim_id: str, request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    params = parse_list_params(request.query_params)
    doc = await get_user(ctx.db, ctx.tenant, _clean_id(scim_id), base=_base(request, settings))
    return scim_response(200, project(doc, params))


@scim_router.put("/Users/{scim_id}")
async def users_replace(
    scim_id: str,
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    ctx = _ctx(request)
    ctx.scim_id = sid = _clean_id(scim_id)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    result = await replace_user(ctx.db, ctx.tenant, sid, doc, base=_base(request, settings))
    await ctx.db.commit()
    await _after_user_write(ctx, settings, redis, result)
    return scim_response(200, result.resource)


@scim_router.patch("/Users/{scim_id}")
async def users_patch(
    scim_id: str,
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    ctx = _ctx(request)
    ctx.scim_id = sid = _clean_id(scim_id)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    ops = parse_patch(doc)
    result = await patch_user(ctx.db, ctx.tenant, sid, ops, base=_base(request, settings))
    await ctx.db.commit()
    await _after_user_write(ctx, settings, redis, result)
    return scim_response(200, result.resource)


@scim_router.delete("/Users/{scim_id}")
async def users_delete(
    scim_id: str,
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    ctx = _ctx(request)
    ctx.scim_id = sid = _clean_id(scim_id)
    result = await delete_user(ctx.db, ctx.tenant, sid)
    await ctx.db.commit()
    await _after_user_write(ctx, settings, redis, result)
    return Response(status_code=204)


# --------------------------------------------------------------------------- groups


@scim_router.get("/Groups")
async def groups_list(request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    params = parse_list_params(request.query_params)
    flt = parse_filter(request.query_params.get("filter"), resource="Group")
    total, docs = await list_groups(
        ctx.db,
        ctx.tenant,
        flt,
        params,
        base=_base(request, settings),
        include_members=_wants_members(params),
    )
    return scim_response(200, list_response(total, [project(d, params) for d in docs], params))


@scim_router.post("/Groups")
async def groups_create(request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    result = await create_group(
        ctx.db,
        ctx.tenant,
        doc,
        base=_base(request, settings),
        max_members=settings.scim_max_members_per_request,
    )
    await ctx.db.commit()
    _group_audit(ctx, result.resource, result.operation, result.ignored_members)
    return _created(result.resource)


_GROUP_OPERATIONS: Final[dict[str, ScimOperation]] = {
    "create": "group_create",
    "replace": "group_replace",
    "patch": "group_patch",
    "delete": "group_delete",
}


def _group_audit(
    ctx: ScimContext, resource: dict[str, Any], operation: str, ignored_members: int
) -> None:
    ctx.operation = _GROUP_OPERATIONS.get(operation, "rejected")
    if resource.get("id"):
        ctx.scim_id = str(resource["id"])
    if ignored_members:
        ctx.detail = f"ignored_members={ignored_members}"


@scim_router.get("/Groups/{scim_id}")
async def groups_get(scim_id: str, request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    params = parse_list_params(request.query_params)
    doc = await get_group(
        ctx.db,
        ctx.tenant,
        _clean_id(scim_id),
        base=_base(request, settings),
        include_members=_wants_members(params),
    )
    return scim_response(200, project(doc, params))


@scim_router.put("/Groups/{scim_id}")
async def groups_replace(scim_id: str, request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    ctx.scim_id = sid = _clean_id(scim_id)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    result = await replace_group(
        ctx.db,
        ctx.tenant,
        sid,
        doc,
        base=_base(request, settings),
        max_members=settings.scim_max_members_per_request,
    )
    await ctx.db.commit()
    _group_audit(ctx, result.resource, result.operation, result.ignored_members)
    return scim_response(200, result.resource)


@scim_router.patch("/Groups/{scim_id}")
async def groups_patch(scim_id: str, request: Request, settings: SettingsDep) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    ctx.scim_id = sid = _clean_id(scim_id)
    doc = await read_scim_json(request, settings.scim_max_body_bytes)
    ops = parse_patch(doc)
    result = await patch_group(
        ctx.db,
        ctx.tenant,
        sid,
        ops,
        base=_base(request, settings),
        max_members=settings.scim_max_members_per_request,
    )
    await ctx.db.commit()
    _group_audit(ctx, result.resource, result.operation, result.ignored_members)
    return scim_response(200, result.resource)


@scim_router.delete("/Groups/{scim_id}")
async def groups_delete(scim_id: str, request: Request) -> Response:
    ctx = _ctx(request)
    _agent_only(ctx.tenant)
    ctx.scim_id = sid = _clean_id(scim_id)
    result = await delete_group(ctx.db, ctx.tenant, sid)
    await ctx.db.commit()
    _group_audit(ctx, result.resource, result.operation, result.ignored_members)
    return Response(status_code=204)


# --------------------------------------------------------------------------- catch-all


@scim_router.api_route("", methods=_ALL_METHODS)
@scim_router.api_route(_CATCH_ALL, methods=_ALL_METHODS)
async def unknown_path(request: Request) -> Response:
    """Last route: a known path with the wrong method is 405 (``Allow``), anything else 404.

    Without this, Starlette's own plain-text 404/405 would answer first for the whole prefix.
    """
    # FastAPI keeps included routes in lazy wrappers, so walk this router's own routes against
    # the path below the prefix. Any route whose path matches here has the wrong method (the
    # right ones were tried before this catch-all).
    subpath = request.url.path[len(SCIM_PREFIX) :]
    allowed: set[str] = set()
    for route in scim_router.routes:
        if not isinstance(route, APIRoute) or route.path in ("", _CATCH_ALL):
            continue
        if route.path_regex.match(subpath) and route.methods:
            allowed |= set(route.methods)
    if allowed:
        raise ScimHttpError(405, None, "Method not allowed", {"Allow": ", ".join(sorted(allowed))})
    raise _not_found()
