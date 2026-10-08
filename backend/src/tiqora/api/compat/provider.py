"""Provider pipeline (port of ``Kernel::GenericInterface::Provider::Run``).

Inbound mapping -> operation -> outbound mapping, independent of the transport. The transport
(REST or SOAP, in ``router.py``) only decodes the request and renders the :class:`ProviderOutcome`.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Response

from tiqora.api.compat.mapping import MappingError
from tiqora.api.compat.webservices import OperationConfig, Webservice, run_mapper

logger = structlog.get_logger(__name__)

OperationRunner = Callable[[str, dict[str, Any], OperationConfig], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    status_code: int
    body: Any  # REST: JSON-able; SOAP: dict for build_soap_response
    headers: dict[str, str]
    fault: str | None  # set -> the transport renders its fault shape with this faultstring


def error_status_code(result: dict[str, Any]) -> int:
    """Map a Znuny-style ``{"Error": {"ErrorCode": ...}}`` result to an HTTP status ."""
    error = result.get("Error")
    if isinstance(error, dict):
        code = str(error.get("ErrorCode", ""))
        if "NotImplemented" in code:
            return 501
        if "RateLimited" in code:
            return 429
        if "AuthFail" in code:
            return 401
        if "AccessDenied" in code:
            return 403
        if "MissingParameter" in code or "InvalidParameter" in code:
            return 400
    return 200


def rest_fault_body(message: str) -> dict[str, str]:
    return {"faultcode": "Server", "faultstring": message}


def _fault(message: str) -> ProviderOutcome:
    return ProviderOutcome(500, rest_fault_body(message), {}, message)


async def run_provider(
    ws: Webservice, operation: str, data: Any, *, run_operation: OperationRunner
) -> ProviderOutcome:
    op = ws.operations.get(operation)
    if op is None:
        return _fault(f"OperationType for operation '{operation}' is not registered")
    if op.mapping_error:
        return _fault(op.mapping_error)

    data_include: dict[str, Any] = {"ProviderRequestInput": copy.deepcopy(data)}

    data_in = data
    if op.mapping_inbound is not None:
        try:
            data_in = await run_mapper(op.mapping_inbound, data)
        except MappingError as exc:
            return _fault(str(exc))
        data_include["ProviderRequestMapOutput"] = copy.deepcopy(data_in)

    if op.handler is None:
        result: dict[str, Any] = {
            "Error": {
                "ErrorCode": "Operation.NotImplemented",
                "ErrorMessage": (
                    f"Operation type {op.type!r} of operation {operation!r} "
                    "is not supported by Tiqora"
                ),
            }
        }
    else:
        result = await run_operation(op.handler, data_in if isinstance(data_in, dict) else {}, op)
    status = error_status_code(result)
    headers: dict[str, str] = {}
    retry = result.get("Error", {}).get("RetryAfter") if status == 429 else None
    if retry is not None:
        headers["Retry-After"] = str(retry)

    body: Any = result
    if op.mapping_outbound is not None:
        data_include["ProviderResponseInput"] = copy.deepcopy(result)
        try:
            body = await run_mapper(op.mapping_outbound, result, data_include)
        except MappingError as exc:
            return _fault(str(exc))

    if ws.transport == "HTTP::REST" and isinstance(body, dict):
        extra = body.get("AdditionalResponseHeaders")
        if "AdditionalResponseHeaders" in body:
            body = {k: v for k, v in body.items() if k != "AdditionalResponseHeaders"}
        if isinstance(extra, dict):
            headers.update({str(k): v for k, v in extra.items() if isinstance(v, str)})
    return ProviderOutcome(status, body, headers, None)


def render_rest(outcome: ProviderOutcome) -> Response:
    """Render an outcome as Znuny's REST response (``JSON->Encode(SortKeys => 1)``)."""
    body = rest_fault_body(outcome.fault) if outcome.fault is not None else outcome.body
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return Response(
        content=text.encode("utf-8"),
        status_code=outcome.status_code,
        media_type="application/json; charset=utf-8",
        headers=outcome.headers,
    )


__all__ = [
    "OperationRunner",
    "ProviderOutcome",
    "error_status_code",
    "render_rest",
    "rest_fault_body",
    "run_provider",
]
