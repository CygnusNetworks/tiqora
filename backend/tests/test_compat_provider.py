"""Provider pipeline (inbound mapping -> operation -> outbound mapping), pure with a fake runner."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tiqora.api.compat.mapping.xslt import XsltMapper
from tiqora.api.compat.provider import render_rest, rest_fault_body, run_provider
from tiqora.api.compat.webservices import OperationConfig, Webservice, parse_webservice

pytestmark = pytest.mark.asyncio

_XSL = 'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'


def _simple(**maps: Any) -> dict[str, Any]:
    return {
        "Type": "Simple",
        "Config": {
            "KeyMapDefault": {"MapTo": "", "MapType": "Keep"},
            "ValueMapDefault": {"MapTo": "", "MapType": "Keep"},
            **maps,
        },
    }


def _ws(operations: dict[str, Any], transport: str = "HTTP::REST") -> Webservice:
    return parse_webservice(
        1,
        "Prov",
        {"Provider": {"Transport": {"Type": transport, "Config": {}}, "Operation": operations}},
        huge_xml=False,
    )


class _Runner:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, handler: str, data: dict[str, Any], op: OperationConfig
    ) -> dict[str, Any]:
        self.calls.append((handler, data))
        return self.result


async def test_inbound_and_outbound_simple_mapping() -> None:
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingInbound": _simple(KeyMapExact={"Login": "UserLogin"}),
                "MappingOutbound": _simple(KeyMapExact={"TicketID": "id"}),
            }
        }
    )
    runner = _Runner({"TicketID": 5})
    outcome = await run_provider(ws, "Get", {"Login": "agent"}, run_operation=runner)
    assert runner.calls == [("TicketGet", {"UserLogin": "agent"})]
    assert outcome.fault is None
    assert outcome.status_code == 200
    assert outcome.body == {"id": 5}


async def test_operation_error_passes_outbound_mapping_but_keeps_status() -> None:
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingOutbound": _simple(KeyMapDefault={"MapTo": "", "MapType": "Ignore"}),
            }
        }
    )
    runner = _Runner({"Error": {"ErrorCode": "TicketGet.AuthFail", "ErrorMessage": "no"}})
    outcome = await run_provider(ws, "Get", {}, run_operation=runner)
    assert outcome.body == {}
    assert outcome.status_code == 401
    assert outcome.fault is None


async def test_data_include_reaches_outbound_xslt() -> None:
    template = (
        f'<xsl:stylesheet version="1.0" {_XSL}><xsl:output method="xml" encoding="utf-8"/>'
        '<xsl:template match="/RootElement"><R><Echo>'
        '<xsl:value-of select="/RootElement/DataInclude/ProviderRequestInput/Q"/>'
        "</Echo></R></xsl:template></xsl:stylesheet>"
    )
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingOutbound": {
                    "Type": "XSLT",
                    "Config": {"DataInclude": ["ProviderRequestInput"], "Template": template},
                },
            }
        }
    )
    outcome = await run_provider(ws, "Get", {"Q": "needle"}, run_operation=_Runner({"TicketID": 1}))
    assert outcome.fault is None
    assert outcome.body == {"Echo": "needle"}


async def test_mapping_failure_is_fault_500() -> None:
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingInbound": {"Type": "XSLT", "Config": {"Template": "this is no xml"}},
            }
        }
    )
    runner = _Runner({"TicketID": 1})
    outcome = await run_provider(ws, "Get", {"a": "b"}, run_operation=runner)
    assert outcome.status_code == 500
    assert outcome.fault
    assert outcome.fault == "Could not load configured XSLT template"
    assert runner.calls == []


async def test_unknown_operation_and_unsupported_type() -> None:
    ws = _ws({"Cfg": {"Type": "FAQ::PublicFAQGet"}})
    runner = _Runner({"TicketID": 1})
    missing = await run_provider(ws, "Nope", {}, run_operation=runner)
    assert missing.status_code == 500
    assert missing.fault == "OperationType for operation 'Nope' is not registered"

    unsupported = await run_provider(ws, "Cfg", {}, run_operation=runner)
    assert unsupported.status_code == 501
    assert unsupported.fault is None
    assert unsupported.body["Error"]["ErrorCode"] == "Operation.NotImplemented"
    assert runner.calls == []


async def test_additional_response_headers_popped() -> None:
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingOutbound": _simple(),
            }
        }
    )
    runner = _Runner({"TicketID": 1, "AdditionalResponseHeaders": {"X-Test": "yes", "X-Num": 3}})
    outcome = await run_provider(ws, "Get", {}, run_operation=runner)
    assert outcome.body == {"TicketID": 1}
    assert outcome.headers == {"X-Test": "yes"}


async def test_xslt_runs_in_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    real = asyncio.to_thread

    async def spy(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        seen.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr("tiqora.api.compat.webservices.asyncio.to_thread", spy)
    template = (
        f'<xsl:stylesheet version="1.0" {_XSL}><xsl:output method="xml" encoding="utf-8"/>'
        '<xsl:template match="/RootElement"><R><K>v</K></R></xsl:template></xsl:stylesheet>'
    )
    ws = _ws(
        {
            "Get": {
                "Type": "Ticket::TicketGet",
                "MappingInbound": {"Type": "XSLT", "Config": {"Template": template}},
            }
        }
    )
    runner = _Runner({"TicketID": 1})
    outcome = await run_provider(ws, "Get", {"a": "b"}, run_operation=runner)
    assert runner.calls == [("TicketGet", {"K": "v"})]
    assert outcome.fault is None
    assert len(seen) == 1
    assert seen[0].__func__ is XsltMapper.map


async def test_render_rest_sorted_compact_json_and_fault_shape() -> None:
    ws = _ws({"Get": {"Type": "Ticket::TicketGet"}})
    outcome = await run_provider(
        ws, "Get", {}, run_operation=_Runner({"b": "ü", "a": {"z": 1, "y": 2}})
    )
    response = render_rest(outcome)
    assert response.body == '{"a":{"y":2,"z":1},"b":"ü"}'.encode()
    assert response.headers["content-type"] == "application/json; charset=utf-8"

    fault = render_rest(await run_provider(ws, "Nope", {}, run_operation=_Runner({})))
    assert fault.status_code == 500
    assert json.loads(fault.body) == rest_fault_body(
        "OperationType for operation 'Nope' is not registered"
    )


async def test_operation_fault_becomes_a_server_fault() -> None:
    """Operation Success => 0: Provider _HandleError -> _GenerateErrorResponse (HTTP 500)."""
    from tiqora.api.compat.provider import OperationFault, run_provider
    from tiqora.api.compat.webservices import parse_webservice

    ws = parse_webservice(
        8950,
        "Gi8950Fault",
        {"Provider": {"Operation": {"Away": {"Type": "User::OutOfOffice"}}}},
        huge_xml=False,
    )

    async def _run(handler: str, data: dict[str, Any], op: Any) -> dict[str, Any]:
        raise OperationFault("Payload does not contain out-of-office entries")

    outcome = await run_provider(ws, "Away", {}, run_operation=_run)
    assert outcome.status_code == 500
    assert outcome.fault == "Payload does not contain out-of-office entries"


async def test_rate_limited_operation_keeps_retry_after_through_a_webservice() -> None:
    from tiqora.api.compat.webservices import parse_webservice

    ws = parse_webservice(
        8951,
        "Gi8951Rate",
        {
            "Provider": {
                "Transport": {"Type": "HTTP::REST", "Config": {}},
                "Operation": {"Login": {"Type": "Session::SessionCreate"}},
            }
        },
        huge_xml=False,
    )

    async def _run(handler: str, data: dict[str, Any], op: Any) -> dict[str, Any]:
        return {
            "Error": {
                "ErrorCode": "SessionCreate.RateLimited",
                "ErrorMessage": "Too many attempts",
                "RetryAfter": 42,
            }
        }

    outcome = await run_provider(ws, "Login", {}, run_operation=_run)
    assert outcome.status_code == 429
    response = render_rest(outcome)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
