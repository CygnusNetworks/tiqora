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
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.redact import MASK

logger = structlog.get_logger(__name__)

#: Request/response keys whose value never reaches the debugger (case-insensitive). The generic
#: ``redact_config`` already masks ``Password``/``TwoFactorToken`` by name; ``SessionID`` is not
#: a secret key in general, but on provider traffic it is a bearer credential.
PROVIDER_SECRET_KEYS = frozenset({"password", "sessionid", "twofactortoken"})

OperationRunner = Callable[[str, dict[str, Any], OperationConfig], Awaitable[dict[str, Any]]]


class OperationFault(Exception):
    """The operation answered ``Success => 0`` (Znuny ``Provider.pm`` ``_HandleError`` ->
    ``_GenerateErrorResponse``): the transport sends a fault with this message (HTTP 500),
    not an ``{"Error": ...}`` body."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def mask_provider_data(value: Any) -> Any:
    """Deep copy of *value* with every :data:`PROVIDER_SECRET_KEYS` value replaced by ``***``."""
    if isinstance(value, dict):
        return {
            k: (
                MASK
                if isinstance(k, str) and k.lower() in PROVIDER_SECRET_KEYS and v not in (None, "")
                else mask_provider_data(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [mask_provider_data(v) for v in value]
    return value


def _secret_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in PROVIDER_SECRET_KEYS and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_secret_values(v))
    elif isinstance(value, list | tuple):
        for item in value:
            found.extend(_secret_values(item))
    return found


async def provider_log(
    debugger: GiDebugger | None,
    level: str,
    summary: str,
    data: Any = None,
    *,
    mask: bool = True,
) -> None:
    """One debugger line; never raises. A broken debugger store must not fail the request
    (Znuny's ``Debugger->Debug`` has no failure path into ``Provider.pm`` either)."""
    if debugger is None:
        return
    try:
        if mask:
            debugger.add_secrets(*_secret_values(data))
            data = mask_provider_data(data)
        await debugger.log(level, summary, data)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        logger.warning("gi_provider_debugger_failed", summary=summary[:80], exc_info=True)


def _single_ticket_id(data: Any) -> int | None:
    """The ticket of a request or result that names exactly one (``TicketID: 7`` or
    ``"7"``); lists and ``"1,2"`` name none."""
    if not isinstance(data, dict):
        return None
    raw = data.get("TicketID")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        return value if value > 0 else None
    return None


def _note_ticket(debugger: GiDebugger | None, data: Any) -> None:
    """Link the debugger entry to a single ticket (GDPR erasure deletes entries by ticket)."""
    if debugger is not None and debugger.ticket_id is None:
        debugger.ticket_id = _single_ticket_id(data)


async def run_provider(
    ws: Webservice,
    operation: str,
    data: Any,
    *,
    run_operation: OperationRunner,
    debugger: GiDebugger | None = None,
) -> ProviderOutcome:
    """Run one provider request. With a *debugger* the summaries of ``Provider.pm`` are written
    (the caller already wrote ``Communication sequence started``); the trail goes through the
    debugger's own sink, so it survives a rolled-back operation."""
    await provider_log(debugger, "debug", f"Detected operation '{operation}'")
    op = ws.operations.get(operation)
    if op is None:
        message = f"OperationType for operation '{operation}' is not registered"
        await provider_log(debugger, "error", "Operation could not be initialized", message)
        return _fault(message)
    if op.mapping_error:
        await provider_log(debugger, "error", op.mapping_error)
        return _fault(op.mapping_error)

    data_include: dict[str, Any] = {"ProviderRequestInput": copy.deepcopy(data)}

    _note_ticket(debugger, data)
    await provider_log(debugger, "debug", "Incoming data before mapping", data)
    data_in = data
    if op.mapping_inbound is not None:
        try:
            data_in = await run_mapper(op.mapping_inbound, data)
        except MappingError as exc:
            await provider_log(debugger, "error", str(exc))
            return _fault(str(exc))
        data_include["ProviderRequestMapOutput"] = copy.deepcopy(data_in)
        _note_ticket(debugger, data_in)
        await provider_log(debugger, "debug", "Incoming data after mapping", data_in)

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
        try:
            result = await run_operation(
                op.handler, data_in if isinstance(data_in, dict) else {}, op
            )
        except OperationFault as exc:
            await provider_log(debugger, "error", exc.message)
            return _fault(exc.message)
    _note_ticket(debugger, result)  # TicketCreate: the new ticket
    error = result.get("Error")
    if isinstance(error, dict):
        # Operation/Common.pm ReturnError: Summary = ErrorCode, Data = ErrorMessage
        await provider_log(
            debugger, "error", str(error.get("ErrorCode", "Error")), error.get("ErrorMessage")
        )
    status = error_status_code(result)
    headers: dict[str, str] = {}
    retry = result.get("Error", {}).get("RetryAfter") if status == 429 else None
    if retry is not None:
        headers["Retry-After"] = str(retry)

    body: Any = result
    await provider_log(debugger, "debug", "Outgoing data before mapping", result)
    if op.mapping_outbound is not None:
        data_include["ProviderResponseInput"] = copy.deepcopy(result)
        try:
            body = await run_mapper(op.mapping_outbound, result, data_include)
        except MappingError as exc:
            await provider_log(debugger, "error", str(exc))
            return _fault(str(exc))
        await provider_log(debugger, "debug", "Outgoing data after mapping", body)

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
    "OperationFault",
    "OperationRunner",
    "ProviderOutcome",
    "PROVIDER_SECRET_KEYS",
    "error_status_code",
    "mask_provider_data",
    "provider_log",
    "render_rest",
    "rest_fault_body",
    "run_provider",
]
