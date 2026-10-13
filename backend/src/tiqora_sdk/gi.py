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
``MappingError`` to fail the call.

An invoker (``registry.gi_invoker``, ``Plugin_<short_id>::<Name>``) is the outgoing side:
``prepare(ctx, data)`` receives a copy of the event data (``TicketID``, ``Event`` ...) and returns
the dict to send, or ``None`` to skip the call (no error, no retry). It then passes the outbound
mapping and the transport. ``handle_response(ctx, response)`` is optional and receives an
``InvokerResponse`` with the mapped answer or the error; it can get ``success=False`` after any
earlier stage, but a failure raised by ``handle_response`` itself is not passed back to it.
``prepare`` must have no side effects: it also runs for the admin dry run. An exception or timeout
(at most 120 seconds) in ``prepare`` runs the webservice's error handling (retries) like a core
invoker.
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
