"""Names for GenericInterface operations and mappings from plugins (``registry.gi_operation``).

An operation is selected by an admin as the ``Type`` of a webservice operation
(``Plugin_<short_id>::<Name>``); remote systems call it over the configured REST/SOAP transport
and the usual inbound/outbound mappings apply. ``ctx.authenticate(data)`` checks the caller with
the same rules as Tiqora's own operations (``SessionID`` or ``UserLogin``/``CustomerUserLogin``
plus ``Password``) and returns a ``GiPrincipal`` or, on failure, an error dict that the operation
returns unchanged. Operations answer ``{"Success": 1, "Data": {...}}``; an exception, a timeout
(at most 45 seconds) or a non-dict answer is Znuny's fault ``OperationObject returned an error,
cancelling Request``. Write to Znuny tables only through ``ctx.plugin.tickets``.

A mapping type (``Plugin_<short_id>_<Name>``) is built by ``factory(config)`` and must return an
object with ``map(data, data_include=None)``. Mappers are pure, fast and synchronous; raise
``MappingError`` to fail the call. Invoker types (``InvokerContext`` ...) are used by the
requester side.
"""

from __future__ import annotations

from tiqora.api.compat.mapping import Mapper, MappingError
from tiqora.plugins.gi_points import (
    GiOperationContext,
    GiOperationRun,
    GiPrincipal,
    InvokerContext,
    InvokerHandle,
    InvokerPrepare,
    InvokerResponse,
)

__all__ = [
    "GiOperationContext",
    "GiOperationRun",
    "GiPrincipal",
    "InvokerContext",
    "InvokerHandle",
    "InvokerPrepare",
    "InvokerResponse",
    "Mapper",
    "MappingError",
]
