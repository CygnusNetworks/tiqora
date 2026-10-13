"""GenericInterface operations, mappings and invoker types from plugins (D14).

A plugin operation is a webservice ``Type: Plugin_<short_id>::<Name>``; a plugin mapping is a
``MappingInbound/Outbound`` ``Type: Plugin_<short_id>_<Name>``. Core types always win (the core
``OPERATION_TYPES`` / ``MAPPER_FACTORIES`` dicts are consulted first and never modified), the
inbound/outbound mapping, the debugger and the transport are the unchanged provider pipeline.

An operation answers in Znuny's shape: ``{"Success": 1, "Data": {...}}`` (the ``Data`` dict is
the response body), ``{"Success": 0, "ErrorCode": ..., "ErrorMessage": ...}`` or a ready
``{"Error": {...}}`` (for example the one ``ctx.authenticate`` hands back). Anything else that is
not a dict, an exception and a timeout all end as Znuny's fault
``OperationObject returned an error, cancelling Request`` (HTTP 500).

Mappers stay pure and synchronous (they run in ``asyncio.to_thread``): no timeout is possible.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog

from tiqora.api.compat.mapping import (
    DataInclude,
    Mapper,
    MapperFactory,
    MappingConfigError,
    MappingError,
)
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import call_plugin
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiqora.api.compat.webservices import OperationConfig
    from tiqora.domain.auth import SessionStore

logger = structlog.get_logger(__name__)

__all__ = [
    "GI_MAPPINGS",
    "GI_OPERATIONS",
    "MAX_OPERATION_TIMEOUT",
    "PLUGIN_HANDLER_PREFIX",
    "GiMapping",
    "GiOperation",
    "GiOperationContext",
    "GiOperationRun",
    "GiPrincipal",
    "InvokerContext",
    "InvokerHandle",
    "InvokerPrepare",
    "InvokerResponse",
    "is_registered_operation",
    "plugin_mapper_factory",
    "run_plugin_operation",
]

PLUGIN_HANDLER_PREFIX: Final = "plugin:"
#: request budget (F6): operation plus mapping must stay below nginx's 60 s
MAX_OPERATION_TIMEOUT: Final = 45


@dataclass(frozen=True, slots=True)
class GiPrincipal:
    user_id: int  # 0 for a customer
    login: str
    user_type: Literal["User", "Customer"]


@dataclass(frozen=True, slots=True)
class GiOperationContext:
    plugin: PluginContext
    webservice_id: int
    webservice: str
    operation: str
    remote_ip: str | None
    #: wraps the core GI authentication (``SessionID`` / ``UserLogin``+``Password`` /
    #: ``CustomerUserLogin``+``Password``); a dict is Znuny's error result - return it unchanged
    authenticate: Callable[[dict[str, Any]], Awaitable[GiPrincipal | dict[str, Any]]]


GiOperationRun = Callable[[GiOperationContext, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class GiOperation:
    type: str  # "Plugin_<sid>::<Name>"
    plugin_id: str
    run: GiOperationRun
    timeout_seconds: int  # 1..45
    context: PluginContext | None = None


GI_OPERATIONS: Final[ExtensionPoint[GiOperation]] = ExtensionPoint(
    key="core.gi.operations",
    item_key=lambda o: o.type,
    description="GenericInterface operations a plugin offers to remote systems.",
    sdk_stable=True,
)


@dataclass(frozen=True, slots=True)
class GiMapping:
    type: str  # "Plugin_<sid>_<Name>"
    plugin_id: str
    factory: Callable[[dict[str, Any] | None], Mapper]
    context: PluginContext | None = None


GI_MAPPINGS: Final[ExtensionPoint[GiMapping]] = ExtensionPoint(
    key="core.gi.mappings",
    item_key=lambda m: m.type,
    description="GenericInterface data mappings a plugin offers.",
    sdk_stable=True,
)


@dataclass(frozen=True, slots=True)
class InvokerContext:
    plugin: PluginContext
    webservice_id: int
    invoker: str
    ticket_id: int | None


@dataclass(frozen=True, slots=True)
class InvokerResponse:
    success: bool
    data: Any
    error_message: str | None


InvokerPrepare = Callable[[InvokerContext, dict[str, Any]], Awaitable[dict[str, Any] | None]]
InvokerHandle = Callable[[InvokerContext, InvokerResponse], Awaitable[None]]


# -- operations -----------------------------------------------------------------------------


def _find_operation(op_type: str) -> GiOperation | None:
    for item in get_registry().items(GI_OPERATIONS):
        if item.type == op_type:
            return item
    return None


def is_registered_operation(op_type: str) -> bool:
    """True when a loaded plugin registered the operation type (checked at webservice parse)."""
    return _find_operation(op_type) is not None


def _not_implemented(op_type: str) -> dict[str, Any]:
    return {
        "Error": {
            "ErrorCode": "Operation.NotImplemented",
            "ErrorMessage": f"Operation type {op_type!r} is not available",
        }
    }


def _znuny_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Translate the plugin's Znuny-shaped answer into the body the pipeline expects."""
    if isinstance(result.get("Error"), dict):
        return result
    if "Success" in result:
        if result["Success"] in (1, True, "1"):
            data = result.get("Data", {})
            return data if isinstance(data, dict) else None
        code = result.get("ErrorCode")
        message = result.get("ErrorMessage")
        return {
            "Error": {
                "ErrorCode": str(code) if code else "Operation.Failed",
                "ErrorMessage": str(message) if message else "The operation failed",
            }
        }
    return result


async def run_plugin_operation(
    handler: str,
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    request: Request,
    operation_config: OperationConfig | None,
) -> dict[str, Any]:
    """Run a ``plugin:<type>`` handler of the provider pipeline (see the module docstring)."""
    from tiqora.api.compat.operations import OPERATION_FAILED_MESSAGE, _auth_from_params
    from tiqora.api.compat.provider import OperationFault
    from tiqora.security.ratelimit import client_ip

    op_type = handler[len(PLUGIN_HANDLER_PREFIX) :]
    item = _find_operation(op_type)
    if item is None or not is_active(item.plugin_id):
        return _not_implemented(op_type)

    settings = getattr(request.app.state, "settings", None)

    async def authenticate(params: dict[str, Any]) -> GiPrincipal | dict[str, Any]:
        auth = await _auth_from_params(
            params, session, session_store, request=request, settings=settings
        )
        if isinstance(auth, dict):
            return auth
        user_id, login, user_type = auth
        return GiPrincipal(user_id, login, "Customer" if user_type == "Customer" else "User")

    ctx = GiOperationContext(
        plugin=item.context or minimal_context(item.plugin_id),
        webservice_id=operation_config.webservice_id if operation_config else 0,
        webservice=operation_config.webservice if operation_config else "",
        operation=operation_config.name if operation_config else "",
        remote_ip=client_ip(request),
        authenticate=authenticate,
    )
    payload = copy.deepcopy(data)
    outcome = await call_plugin(
        item.plugin_id,
        f"gi_operation {op_type}",
        lambda: item.run(ctx, payload),
        timeout=min(item.timeout_seconds, MAX_OPERATION_TIMEOUT),
    )
    if outcome.timed_out:
        # the plugin may have been cancelled in the middle of a statement on the request session
        try:
            await session.invalidate()
        except Exception:  # noqa: BLE001
            logger.warning("plugin_gi_session_invalidate_failed", plugin=item.plugin_id)
    if not outcome.ok:
        raise OperationFault(OPERATION_FAILED_MESSAGE)
    result = outcome.value
    body = _znuny_result(result) if isinstance(result, dict) else None
    if body is None:
        logger.warning(
            "plugin_call_failed",
            plugin=item.plugin_id,
            what=f"gi_operation {op_type}",
            error="TypeError: Ergebnis ist kein Dictionary",
            timed_out=False,
        )
        raise OperationFault(OPERATION_FAILED_MESSAGE)
    return body


# -- mappings -------------------------------------------------------------------------------


class _PluginMapper:
    """Wraps a plugin mapper so that any non-``MappingError`` becomes ``MappingError``."""

    def __init__(self, item: GiMapping, inner: Mapper) -> None:
        self._item = item
        self._inner = inner

    def map(self, data: Any, data_include: DataInclude | None = None) -> Any:
        try:
            return self._inner.map(data, data_include)
        except MappingError:
            raise
        except Exception as exc:
            logger.warning(
                "plugin_call_failed",
                plugin=self._item.plugin_id,
                what=f"gi_mapping {self._item.type}",
                error=f"{type(exc).__name__}: {exc}"[:500],
                timed_out=False,
            )
            raise MappingError(f"{self._item.type}: {type(exc).__name__}: {exc}") from exc


def plugin_mapper_factory(mapping_type: str) -> MapperFactory | None:
    """A ``MapperFactory`` for a plugin mapping type, or ``None`` when no active plugin has it."""
    for item in get_registry().items(GI_MAPPINGS):
        if item.type == mapping_type and is_active(item.plugin_id):
            found = item
            break
    else:
        return None

    def factory(config: dict[str, Any] | None, huge_xml: bool) -> Mapper:
        try:
            inner = found.factory(config)
        except MappingError:
            raise
        except Exception as exc:
            raise MappingConfigError(f"{found.type}: {type(exc).__name__}: {exc}") from exc
        return _PluginMapper(found, inner)

    return factory
