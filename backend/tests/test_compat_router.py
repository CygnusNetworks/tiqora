"""Unit tests for GenericInterface route matching used by the request-time webservice routes.

Regression: Znuny's RouteOperationMapping stores RequestMethod as a YAML *list* (e.g.
``["POST"]``). An older route loader called ``.upper()`` on it directly and crashed startup
with ``'list' object has no attribute 'upper'``, silently dropping every webservice route.
"""

from __future__ import annotations

from typing import Any

from tiqora.api.compat.webservices import match_rest_route, parse_webservice


def _ws(routes: dict[str, Any]) -> Any:
    return parse_webservice(
        1,
        "GenericTicketConnectorREST",
        {
            "Provider": {
                "Transport": {"Type": "HTTP::REST", "Config": {"RouteOperationMapping": routes}},
                "Operation": {name: {"Type": "Ticket::TicketGet"} for name in routes},
            }
        },
        huge_xml=False,
    )


def test_list_request_method_is_matched_not_crashing() -> None:
    ws = _ws(
        {
            "TicketCreate": {"Route": "/Ticket", "RequestMethod": ["POST"]},
            "TicketGet": {"Route": "/Ticket/:TicketID", "RequestMethod": ["GET"]},
        }
    )
    assert match_rest_route(ws, "/Ticket", "POST") == ("TicketCreate", {})
    assert match_rest_route(ws, "/Ticket/7", "GET") == ("TicketGet", {"TicketID": "7"})
    assert match_rest_route(ws, "/Ticket/7", "POST") is None


def test_multiple_methods_and_string_method_means_any() -> None:
    ws = _ws(
        {
            "A": {"Route": "/A", "RequestMethod": ["GET", "POST"]},
            "B": {"Route": "/B", "RequestMethod": "patch"},
        }
    )
    assert match_rest_route(ws, "/A", "POST") == ("A", {})
    assert match_rest_route(ws, "/A", "PUT") is None
    assert match_rest_route(ws, "/B", "DELETE") == ("B", {})
