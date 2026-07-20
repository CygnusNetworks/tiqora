"""Unit tests for the GenericInterface-compat dynamic route mounting.

Regression: Znuny's RouteOperationMapping stores RequestMethod as a YAML
*list* (e.g. ``["POST"]``). The dynamic-route loader used to call
``.upper()`` on it directly, crashing startup with
``'list' object has no attribute 'upper'`` and silently dropping all
webservice-config-driven routes (seen in production api logs as
``compat_dynamic_routes_failed``).
"""

from __future__ import annotations

from tiqora.api.compat.router import (
    _normalize_request_methods,
    build_dynamic_router,
)


def test_normalize_request_methods_list() -> None:
    assert _normalize_request_methods(["POST"]) == ["POST"]
    assert _normalize_request_methods(["get", "post"]) == ["GET", "POST"]


def test_normalize_request_methods_string() -> None:
    assert _normalize_request_methods("patch") == ["PATCH"]


def test_normalize_request_methods_empty_defaults_to_get() -> None:
    assert _normalize_request_methods(None) == ["GET"]
    assert _normalize_request_methods([]) == ["GET"]
    assert _normalize_request_methods("") == ["GET"]


def test_build_dynamic_router_registers_list_methods() -> None:
    """A route whose method came from a YAML list must be mounted, not crash."""
    routes = [
        (1, "GenericTicketConnectorREST", "TicketCreate", "/Ticket", "POST"),
        (1, "GenericTicketConnectorREST", "TicketGet", "/Ticket/:TicketID", "GET"),
    ]
    router = build_dynamic_router(routes)
    paths = {r.path for r in router.routes}  # type: ignore[attr-defined]
    assert "/Webservice/GenericTicketConnectorREST/Ticket" in paths
    assert "/WebserviceID/1/Ticket/{TicketID}" in paths
