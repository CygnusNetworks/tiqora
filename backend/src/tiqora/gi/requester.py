"""Requester pipeline (G10) - port of ``Kernel/GenericInterface/Requester.pm`` ``Run`` and
``_HandleError`` for Znuny 6.5.

One call = one communication: prepare the invoker request, map it, send it through the
transport, map the answer, let the invoker act on it (response actions). Every failure runs
the webservice's error handling (``RequestRetry``) and returns a :class:`RequesterOutcome`;
nothing here raises for a remote or configuration problem.

Deliberate differences from Znuny:

* A transport failure does not call the invoker's ``HandleResponse`` with the error: for
  ``Ticket::Generic`` it carries no data, so it runs no action and returns nothing the caller
  could merge into the result. Znuny's asynchronous merge would in fact overwrite the
  reschedule data (``Data=undef``), so Znuny never retries asynchronous Ticket::Generic
  transport failures; Tiqora deliberately does.
* ``dry_run`` builds the request and returns it (masked); nothing is sent, nothing is written
  to the debugger, no response action runs - not even the invoker's ``HandleError`` after a
  failed prepare or mapping step.
* The egress policy can be injected (tests); otherwise it is loaded per webservice.
* Secrets never leave this module unmasked: every debugger line passes the debugger's
  redaction, the outcome only holds mapped response data and masked error texts.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.compat.mapping import DataInclude, Mapper
from tiqora.api.compat.webservices import run_mapper
from tiqora.config import Settings
from tiqora.gi.config import SUPPORTED_INVOKER_TYPES, DebuggerConfig, InvokerConfig
from tiqora.gi.debugger import DebugSink, GiDebugger
from tiqora.gi.egress import EgressPolicy, load_policy
from tiqora.gi.error_handling import PastExecutionData, RetryDecision
from tiqora.gi.error_handling import handle_error as run_error_handling
from tiqora.gi.invoker_generic import Enqueue, handle_response, prepare_request
from tiqora.gi.invoker_generic import handle_error as invoker_handle_error
from tiqora.gi.loader import GiWebservice
from tiqora.gi.redact import redact_text
from tiqora.gi.transport_rest import (
    PreparedRestRequest,
    TransportResult,
    build_rest_request,
    rest_perform_request,
)
from tiqora.gi.transport_soap import soap_perform_request
from tiqora.plugins.calls import call_plugin
from tiqora.plugins.gi_points import (
    MAX_INVOKER_TIMEOUT,
    GiInvoker,
    InvokerResponse,
    invoker_context,
    plugin_invoker,
)
from tiqora.security.outbound import HostResolver
from tiqora.znuny.sysconfig import SysConfig

__all__ = ["RequesterOutcome", "run_requester"]

logger = structlog.get_logger(__name__)

_SIZE_NOTE = (
    "See SysConfig option GenericInterface::Operation::ResponseLoggingMaxSize to change the"
    " maximum (Tiqora: TIQORA_GI_DEBUG_MAX_CONTENT_BYTES)."
)


@dataclass(frozen=True, slots=True)
class RequesterOutcome:
    success: bool
    error_message: str | None
    data: Any
    reschedule: RetryDecision | None
    communication_id: str


async def _no_enqueue(payload: dict[str, Any]) -> None:
    raise RuntimeError("no job queue available")


def _naive_utc(moment: datetime | None) -> datetime:
    if moment is None:
        moment = datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.replace(microsecond=0)


def _ticket_id(data: dict[str, Any]) -> int | None:
    raw = data.get("TicketID")
    try:
        return int(raw) if raw is not None and str(raw) != "" else None
    except (TypeError, ValueError):
        return None


async def _map(
    mapper: Mapper, data: Any, include: DataInclude
) -> tuple[Any, None] | tuple[None, str]:
    """``(mapped, None)`` or ``(None, error text)``; the mapper gets copies, never the originals."""
    try:
        mapped = await run_mapper(mapper, copy.deepcopy(data), copy.deepcopy(include))
    except Exception as exc:  # MappingError carries Znuny's summary; anything else is a bug
        return None, str(exc) or type(exc).__name__
    return mapped, None


async def run_requester(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    ws: GiWebservice,
    invoker: str,
    data: dict[str, Any],
    asynchronous: bool,
    past: PastExecutionData | None,
    sysconfig_factory: Callable[[AsyncSession], SysConfig],
    sink: DebugSink,
    settings: Settings,
    before_send: Callable[[], Awaitable[None]] | None = None,
    dry_run: bool = False,
    http_transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
    policy: EgressPolicy | None = None,
    enqueue: Enqueue | None = None,
    now: datetime | None = None,
    chain_depth: int = 0,
) -> RequesterOutcome:
    """``Requester::Run``. *enqueue* receives the ``OTRS_AsynchronousInvokerExecution`` items
    (the job layer adapts them); *policy* overrides the stored egress settings; *now* is the
    clock for error handling (naive UTC); *chain_depth* is how many response actions queued
    this call (0 for event, transition and resend calls)."""
    debugger_config = (
        DebuggerConfig(threshold=ws.debugger.threshold, test_mode=True) if dry_run else ws.debugger
    )
    debugger = GiDebugger(
        sink=sink,
        config=debugger_config,
        webservice_id=ws.id,
        communication_type="Requester",
        name=invoker,
        replay={"invoker": invoker, "data": data, "asynchronous": bool(asynchronous)},
    )
    debugger.ticket_id = _ticket_id(data)
    communication_id = debugger.communication_id

    def failure(
        message: str, reschedule: RetryDecision | None = None, payload: Any = None
    ) -> RequesterOutcome:
        return RequesterOutcome(False, message, payload, reschedule, communication_id)

    if not invoker or not data:
        needed = "Invoker" if not invoker else "Data"
        logger.error("gi_requester_missing_parameter", webservice_id=ws.id, needed=needed)
        return failure(f"Got no {needed}!")

    await debugger.debug("Communication sequence started", data)
    await debugger.debug(f"Using invoker '{invoker}'")

    invoker_config: InvokerConfig | None = ws.requester.invokers.get(invoker)
    # A plugin invoker (D14) is looked up again: the plugin may have been disabled since the
    # webservice snapshot was parsed. Core types never go through the registry.
    plugin_item: GiInvoker | None = None
    if invoker_config is not None and invoker_config.type not in SUPPORTED_INVOKER_TYPES:
        plugin_item = plugin_invoker(invoker_config.type)
    if (
        invoker_config is None
        or (invoker_config.type not in SUPPORTED_INVOKER_TYPES and plugin_item is None)
        or invoker_config.mapping_error
    ):
        if invoker_config is None:
            reason = f"Invoker '{invoker}' ist nicht konfiguriert."
        elif invoker_config.type not in SUPPORTED_INVOKER_TYPES and plugin_item is None:
            reason = f"Invoker-Typ '{invoker_config.type}' wird von Tiqora nicht unterstützt."
        else:
            reason = invoker_config.mapping_error or ""
        summary = "InvokerObject could not be initialized"
        await debugger.error(summary, reason)
        return failure(summary)

    include: DataInclude = {"RequesterRequestInput": copy.deepcopy(data)}
    clock = _naive_utc(now)
    enqueue_fn: Enqueue = enqueue or _no_enqueue
    ticket_id = _ticket_id(data)

    async def _plugin_response(response: InvokerResponse, failure_summary: str) -> str | None:
        """The plugin's ``handle_response``; the error text on failure or timeout, else None."""
        if plugin_item is None or plugin_item.handle_response is None:
            return None
        handler = plugin_item.handle_response
        ictx = invoker_context(
            plugin_item, webservice_id=ws.id, invoker=invoker, ticket_id=ticket_id
        )
        result = await call_plugin(
            plugin_item.plugin_id,
            f"gi_invoker_response {plugin_item.type}",
            lambda: handler(ictx, response),
            timeout=min(plugin_item.timeout_seconds, MAX_INVOKER_TIMEOUT),
        )
        if result.ok:
            return None
        await debugger.error(failure_summary, result.error)
        return result.error or failure_summary

    async def handle_failure(stage: str, summary: str, error_data: Any) -> RequesterOutcome:
        """``_HandleError``: error handling modules, then the invoker's ``HandleError``."""
        result = await run_error_handling(
            modules=ws.requester.error_handling,
            invoker=invoker,
            error_stage=stage,
            summary=summary,
            data=error_data,
            past=past,
            debugger=debugger,
            now=clock,
        )
        decision = (
            result.reschedule
            if result.reschedule is not None and result.reschedule.reschedule
            else None
        )
        outcome = failure(result.error_message or summary, decision)
        if dry_run:
            # A dry run never acts: the invoker's HandleError may map the fault to OTRS_*
            # response actions (ticket writes, queued calls), so it is skipped entirely.
            return outcome

        fault: Any = (
            error_data
            if error_data is None or isinstance(error_data, str)
            else copy.deepcopy(error_data)
        )
        if fault is None:
            fault = ""
        await debugger.debug("Error data before mapping", fault)
        if invoker_config.mapping_inbound is not None:
            error_include: DataInclude = {
                **include,
                "RequesterErrorHandlingOutput": result.module_data,
            }
            mapped, map_error = await _map(
                invoker_config.mapping_inbound, {"Fault": fault}, error_include
            )
            if map_error is not None:
                await debugger.error(map_error)
                return outcome
            fault = mapped
            await debugger.debug("Error data after mapping", fault)
        if plugin_item is not None:
            # a failing response handler is not reported to itself again
            if stage != "RequesterResponseProcess":
                await _plugin_response(
                    InvokerResponse(False, fault, outcome.error_message or summary),
                    "Error handling error data in Invoker",
                )
            return outcome
        async with session_factory() as session:
            handled = await invoker_handle_error(
                session,
                ticket_id=ticket_id,
                data=fault,
                debugger=debugger,
                enqueue=enqueue_fn,
                session_factory=session_factory,
                sysconfig=sysconfig_factory(session),
                chain_depth=chain_depth,
            )
        if not handled.get("Success"):
            await debugger.error(
                "Error handling error data in Invoker", handled.get("ErrorMessage")
            )
        return outcome

    # ---- 3. prepare ---------------------------------------------------------------------
    if plugin_item is not None:
        prepare = plugin_item.prepare
        pctx = invoker_context(
            plugin_item, webservice_id=ws.id, invoker=invoker, ticket_id=ticket_id
        )
        event_copy = copy.deepcopy(data)
        called = await call_plugin(
            plugin_item.plugin_id,
            f"gi_invoker_prepare {plugin_item.type}",
            lambda: prepare(pctx, event_copy),
            timeout=min(plugin_item.timeout_seconds, MAX_INVOKER_TIMEOUT),
        )
        if not called.ok:
            summary = "InvokerObject returned an error, cancelling Request"
            await debugger.error(summary, called.error)
            return await handle_failure("RequesterRequestPrepare", summary, summary)
        if called.value is None:
            # Znuny's StopCommunication: success, nothing is sent, nothing is retried
            await debugger.notice("Erweiterung hat den Aufruf übersprungen")
            return RequesterOutcome(True, None, None, None, communication_id)
        if not isinstance(called.value, dict):
            summary = "InvokerObject returned an error, cancelling Request"
            await debugger.error(summary, "Ergebnis der Erweiterung ist kein Dictionary")
            return await handle_failure("RequesterRequestPrepare", summary, summary)
        prepared = copy.deepcopy(called.value)
    else:
        try:
            async with session_factory() as session:
                prepared = await prepare_request(
                    session,
                    invoker_name=invoker,
                    invoker=invoker_config,
                    data=data,
                    sysconfig=sysconfig_factory(session),
                )
        except Exception as exc:
            summary = str(exc) or "InvokerObject returned an error, cancelling Request"
            return await handle_failure("RequesterRequestPrepare", summary, summary)
    include["RequesterRequestPrepareOutput"] = copy.deepcopy(prepared)

    # ---- 4. outbound mapping ------------------------------------------------------------
    out: Any = prepared
    await debugger.debug("Outgoing data before mapping", out)
    if invoker_config.mapping_outbound is not None:
        mapped, map_error = await _map(invoker_config.mapping_outbound, out, include)
        if map_error is not None:
            return await handle_failure("RequesterRequestMap", map_error, map_error)
        out = mapped
        include["RequesterRequestMapOutput"] = copy.deepcopy(out)
        await debugger.debug("Outgoing data after mapping", out)

    # ---- transport ----------------------------------------------------------------------
    transport = ws.requester.transport
    transport_type = transport.type if transport is not None else ""
    if transport is None or transport_type not in ("HTTP::REST", "HTTP::SOAP"):
        summary = "TransportObject could not be initialized"
        await debugger.error(
            summary, f"Transport '{transport_type}' wird von Tiqora nicht unterstützt"
        )
        return failure(summary)
    if policy is None:
        async with session_factory() as session:
            policy = await load_policy(session, ws.id, settings)

    if dry_run:
        return await _dry_run(
            transport.type,
            transport.config,
            invoker=invoker,
            invoker_config=invoker_config,
            out=out,
            debugger=debugger,
            policy=policy,
            session_factory=session_factory,
            resolver=resolver,
            communication_id=communication_id,
        )

    if before_send is not None:
        await before_send()

    async with session_factory() as session:
        if transport_type == "HTTP::REST":
            result = await rest_perform_request(
                transport_config=transport.config,
                operation=invoker,
                data=out,
                debugger=debugger,
                policy=policy,
                session=session,
                http_transport=http_transport,
                resolver=resolver,
            )
        else:
            result = await soap_perform_request(
                transport_config=transport.config,
                operation=invoker,
                operation_name=invoker_config.operation_name,
                data=out,
                sort=invoker_config.sort_outbound,
                debugger=debugger,
                policy=policy,
                http_transport=http_transport,
                resolver=resolver,
            )
    if not result.success:
        summary = result.error_message or "TransportObject returned an error, cancelling Request"
        return await handle_failure("RequesterRequestPerform", summary, summary)

    # ---- 7. inbound mapping -------------------------------------------------------------
    include["RequesterResponseInput"] = copy.deepcopy(result.data)
    incoming: Any = result.data
    if result.size_exceeded:
        await debugger.debug("Incoming data before mapping was too large for logging", _SIZE_NOTE)
    else:
        await debugger.debug("Incoming data before mapping", incoming)
    if invoker_config.mapping_inbound is not None:
        mapped, map_error = await _map(invoker_config.mapping_inbound, incoming, include)
        if map_error is not None:
            return await handle_failure("RequesterResponseMap", map_error, map_error)
        incoming = mapped
        include["RequesterResponseMapOutput"] = copy.deepcopy(incoming)
        if result.size_exceeded:
            await debugger.debug(
                "Incoming data after mapping was too large for logging", _SIZE_NOTE
            )
        else:
            await debugger.debug("Incoming data after mapping", incoming)

    # ---- 8. response actions ------------------------------------------------------------
    if plugin_item is not None:
        failed = await _plugin_response(
            InvokerResponse(True, incoming, None), "Error handling response data in Invoker"
        )
        if failed is not None:
            summary = "InvokerObject returned an error, cancelling Request"
            return await handle_failure("RequesterResponseProcess", summary, summary)
        return RequesterOutcome(True, None, incoming, None, communication_id)
    async with session_factory() as session:
        handled = await handle_response(
            session,
            ticket_id=ticket_id,
            success=True,
            data=incoming,
            error_message=None,
            debugger=debugger,
            enqueue=enqueue_fn,
            session_factory=session_factory,
            sysconfig=sysconfig_factory(session),
            chain_depth=chain_depth,
        )
    if not handled.get("Success"):
        summary = (
            handled.get("ErrorMessage") or "InvokerObject returned an error, cancelling Request"
        )
        return await handle_failure(
            "RequesterResponseProcess", summary, handled.get("Data") or summary
        )

    return RequesterOutcome(True, None, handled.get("Data"), None, communication_id)


async def _dry_run(
    transport_type: str,
    transport_config: dict[str, Any],
    *,
    invoker: str,
    invoker_config: InvokerConfig,
    out: Any,
    debugger: GiDebugger,
    policy: EgressPolicy,
    session_factory: async_sessionmaker[AsyncSession],
    resolver: HostResolver | None,
    communication_id: str,
) -> RequesterOutcome:
    """Build the request exactly as the transport would and return it, masked. Nothing is sent
    to the target; an OAuth2 token refresh may still contact the token endpoint."""
    result: TransportResult | PreparedRestRequest
    if transport_type == "HTTP::REST":
        async with session_factory() as session:
            result = await build_rest_request(
                transport_config=transport_config,
                operation=invoker,
                data=out,
                debugger=debugger,
                policy=policy,
                session=session,
                resolver=resolver,
            )
        if isinstance(result, TransportResult):
            return RequesterOutcome(False, result.error_message, None, None, communication_id)
        shown = result.display()
        shown["body"] = redact_text(
            (result.body or b"").decode("utf-8", errors="replace"), result.secret_values
        )
        return RequesterOutcome(True, None, {"request": shown}, None, communication_id)

    sent = await soap_perform_request(
        transport_config=transport_config,
        operation=invoker,
        operation_name=invoker_config.operation_name,
        data=out,
        sort=invoker_config.sort_outbound,
        debugger=debugger,
        policy=policy,
        resolver=resolver,
        dry_run=True,
    )
    if not sent.success:
        return RequesterOutcome(False, sent.error_message, None, None, communication_id)
    return RequesterOutcome(True, None, sent.data, None, communication_id)
