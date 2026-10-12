"""SOAP requester transport of the GenericInterface (G8).

Port of ``Kernel/GenericInterface/Transport/HTTP/SOAP.pm`` ``RequesterPerformRequest`` with the
same egress rules as the REST transport (:mod:`tiqora.gi.transport_rest`): the target is
resolved and pinned, redirects are never followed, certificate files come only from the
configured directories, the answer is size-capped, every secret is masked in the debugger.

Deliberate differences from Znuny (all safe-side): ``NTLM`` is refused with a plain message;
with a proxy only the proxy URL is pinned (the proxy resolves the endpoint); one client per call.
"""

from __future__ import annotations

import base64
import contextlib
import math
from collections.abc import Mapping
from typing import Any

import httpx

from tiqora.api.compat.mapping.perl import perl_str, perl_truthy
from tiqora.config import get_settings
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.egress import (
    EgressError,
    EgressPolicy,
    build_ssl_context,
    client,
    pin,
    proxy_for,
    read_capped,
)
from tiqora.gi.redact import redact_headers, redact_text, redact_url
from tiqora.gi.soap_envelope import (
    SoapOutputError,
    build_request_envelope,
    is_fault_error,
    parse_response,
    request_name,
    response_name,
    soap_action,
    soap_output,
)
from tiqora.gi.transport_rest import (
    TransportResult,
    _dict,
    _scalar_text,
    _string_with_data,
)
from tiqora.security.outbound import HostResolver, PinnedOutboundURL


def _fail(
    message: str, *, status: int | None = None, secrets: tuple[str, ...] = ()
) -> TransportResult:
    return TransportResult(False, message, None, False, status, secrets)


async def soap_perform_request(
    *,
    transport_config: Mapping[str, Any],
    operation: str,
    operation_name: str | None = None,
    data: Any,
    sort: Any = None,
    debugger: GiDebugger,
    policy: EgressPolicy,
    http_transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
    dry_run: bool = False,
) -> TransportResult:
    """``operation`` is the invoker name, ``operation_name`` its ``OperationName`` (default:
    the invoker name); ``sort`` overrides the transport's ``Sort``."""
    config = transport_config
    if not isinstance(config, dict) or not config:
        return _fail("SOAP Transport: Have no Config")
    for needed in ("Endpoint", "NameSpace", "Timeout"):
        if _string_with_data(config.get(needed)):
            continue
        if needed == "NameSpace" and perl_truthy(config.get("NoNameSpace")):
            continue
        return _fail(f"SOAP Transport: Have no {needed} in config")
    if data is not None and not isinstance(data, dict):
        return _fail("SOAP Transport: Invalid Data")
    name = operation_name if operation_name is not None else operation
    if not _string_with_data(name):
        return _fail("SOAP Transport: Need Operation")
    try:
        timeout = min(float(perl_str(config["Timeout"])), policy.max_timeout)
    except ValueError:
        return _fail("SOAP Transport: Invalid Timeout in config")
    if not math.isfinite(timeout) or timeout <= 0:
        return _fail("SOAP Transport: Invalid Timeout in config")

    secrets: list[str] = []

    async def error(message: str, detail: Any = None) -> TransportResult:
        await debugger.error(message, detail)
        return _fail(redact_text(message, secrets), secrets=tuple(secrets))

    # ---- body ----------------------------------------------------------------------------
    body = []
    if isinstance(data, dict) and data:
        try:
            body = soap_output(data, sort=sort if sort is not None else config.get("Sort"))
        except SoapOutputError as exc:
            return await error(f"Error in SOAPOutputRecursion: {exc}")
    no_namespace = perl_truthy(config.get("NoNameSpace"))
    try:
        envelope = build_request_envelope(
            operation_request=request_name(config, name),
            namespace=_scalar_text(config.get("NameSpace")) or None,
            no_namespace=no_namespace,
            fixed_prefix=_scalar_text(config.get("FixedNameSpacePrefix")) or None,
            body=body,
        )
    except SoapOutputError as exc:
        return await error(f"SOAP Transport: Error preparing used method: {exc}")

    # ---- authentication ------------------------------------------------------------------
    auth = _dict(config.get("Authentication"))
    auth_type = perl_str(auth.get("AuthType")) if auth else ""
    headers: dict[str, str] = {}
    if auth_type == "NTLM":
        return await error("Anmeldung per NTLM wird von Tiqora nicht unterstützt.")
    if (
        auth_type == "BasicAuth"
        and _string_with_data(auth.get("BasicAuthUser"))
        and _string_with_data(auth.get("BasicAuthPassword"))
    ):
        password = perl_str(auth["BasicAuthPassword"])
        token = base64.b64encode(f"{perl_str(auth['BasicAuthUser'])}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
        secrets.extend([password, token])
        debugger.add_secrets(password, token)

    # ---- TLS / proxy ---------------------------------------------------------------------
    ssl_cfg = _dict(config.get("SSL"))
    use_ssl = perl_str(ssl_cfg.get("UseSSL")) == "Yes"
    key_password = _scalar_text(ssl_cfg.get("SSLPassword")) if use_ssl else None
    if key_password:
        secrets.append(key_password)
        debugger.add_secrets(key_password)
    proxy_cfg = config.get("Proxy")
    if isinstance(proxy_cfg, dict) and _string_with_data(proxy_cfg.get("ProxyPassword")):
        secrets.append(perl_str(proxy_cfg["ProxyPassword"]))
        debugger.add_secrets(perl_str(proxy_cfg["ProxyPassword"]))
    no_verify = perl_truthy(config.get("SSLNoHostnameVerification"))
    ssl_context = None
    proxy_url: str | None = None
    proxy_headers: dict[str, str] = {}
    try:
        cert, key, ca_file, ca_dir = (
            (ssl_cfg.get(k) if use_ssl else None)
            for k in ("SSLCertificate", "SSLKey", "SSLCAFile", "SSLCADir")
        )
        if cert or key or ca_file or ca_dir or no_verify:
            ssl_context = build_ssl_context(
                policy=policy,
                verify_hostname=not no_verify,
                cert_file=perl_str(cert) if cert else None,
                key_file=perl_str(key) if key else None,
                key_password=key_password or None,
                ca_file=perl_str(ca_file) if ca_file else None,
                ca_dir=perl_str(ca_dir) if ca_dir else None,
            )
        proxy_url, proxy_headers = proxy_for(config, policy)
    except EgressError as exc:
        return await error(str(exc))
    for value in proxy_headers.values():
        secrets.append(value.rsplit(" ", 1)[-1])
    debugger.add_secrets(*secrets)

    # ---- request -------------------------------------------------------------------------
    endpoint = perl_str(config["Endpoint"])
    headers["Content-Type"] = "text/xml; charset=utf-8"
    headers["SOAPAction"] = (
        '"' + soap_action(config, operation=operation, operation_name=name) + '"'
    )
    error_base = "Error in SOAP call:"
    pinned: PinnedOutboundURL | None = None
    try:
        if proxy_url is None:
            pinned = pin(endpoint, policy, resolver=resolver)
        elif not endpoint.lower().startswith(("http://", "https://")):
            raise EgressError(f"Ziel {redact_url(endpoint)} ist nicht erlaubt: nur http und https.")
    except EgressError as exc:
        return await error(str(exc))

    secret_values = tuple(dict.fromkeys(secrets))
    xml_sent = envelope.decode("utf-8")
    await debugger.debug(
        "Request params from outgoing data",
        {
            "method": "POST",
            "url": redact_url(endpoint),
            "headers": redact_headers(headers, secret_values=secret_values),
            "body_bytes": len(envelope),
        },
    )
    await debugger.debug("XML data sent to remote system", redact_text(xml_sent, secret_values))
    if dry_run:
        # Nothing is sent; the request is returned as it may be shown (masked).
        return TransportResult(
            True,
            None,
            {
                "request": {
                    "method": "POST",
                    "url": redact_url(endpoint),
                    "headers": redact_headers(headers, secret_values=secret_values),
                    "body_bytes": len(envelope),
                    "body": redact_text(xml_sent, secret_values),
                }
            },
            False,
            None,
            secret_values,
        )

    if pinned is not None:
        send_url = pinned.request_url
        wire_headers = pinned.request_headers(headers)
        extensions: dict[str, Any] = dict(pinned.request_extensions())
    else:
        send_url, wire_headers, extensions = endpoint, dict(headers), {}
    wire_list = [(n.encode("utf-8"), v.encode("utf-8")) for n, v in wire_headers.items()]
    proxy = httpx.Proxy(proxy_url, headers=proxy_headers or None) if proxy_url is not None else None

    status: int | None = None
    reason = ""
    try:
        async with client(
            policy=policy, ssl_context=ssl_context, proxy=proxy, transport=http_transport
        ) as http:
            request = http.build_request(
                "POST",
                send_url,
                headers=wire_list,
                content=envelope,
                timeout=timeout,
                extensions=extensions,
            )
            response = await http.send(request, stream=True)
            try:
                status = response.status_code
                reason = response.reason_phrase
                raw = await read_capped(response, policy.max_response_bytes)
            finally:
                await response.aclose()
    except EgressError as exc:
        return _failed_after(
            await error(f"{error_base} {exc}"), status=status, secrets=secret_values
        )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        detail = f"{type(exc).__name__}: {redact_text(str(exc), secrets)}"
        await debugger.error(error_base, detail)
        return _fail(
            redact_text(f"{error_base} {type(exc).__name__}", secrets),
            status=status,
            secrets=secret_values,
        )

    size_exceeded = len(raw) >= get_settings().gi_debug_max_content_bytes
    text = raw.decode("utf-8", errors="replace")
    if not raw:
        message = (
            f"{error_base} {status} {reason}".rstrip()
            if status is not None and not 200 <= status < 300
            else "Could not get XML data received from remote system"
        )
        await debugger.error(message)
        return _fail(redact_text(message, secrets), status=status, secrets=secret_values)

    encoding = _scalar_text(config.get("Encoding")) or None
    if size_exceeded:
        await debugger.debug(
            "XML data received from remote system was too large for logging",
            "See SysConfig option GenericInterface::Operation::ResponseLoggingMaxSize to change"
            " the maximum (Tiqora: TIQORA_GI_DEBUG_MAX_CONTENT_BYTES).",
        )
    else:
        shown = text
        if encoding:
            with contextlib.suppress(LookupError):
                shown = raw.decode(encoding, errors="replace")
        await debugger.debug("XML data received from remote system", redact_text(shown, secrets))

    data_out, parse_error = parse_response(
        raw,
        response_name=response_name(config, name),
        encoding=encoding,
        operation_name=name,
    )
    ok_status = status is not None and 200 <= status < 300
    if parse_error is not None and (ok_status or is_fault_error(parse_error)):
        message = redact_text(parse_error, secrets)
        await debugger.error(message)
        return _fail(message, status=status, secrets=secret_values)
    if not ok_status:
        message = redact_text(f"{error_base} {status} {reason}".rstrip(), secrets)
        await debugger.error(message)
        return _fail(message, status=status, secrets=secret_values)
    return TransportResult(True, None, data_out, size_exceeded, status, secret_values)


def _failed_after(
    result: TransportResult, *, status: int | None, secrets: tuple[str, ...]
) -> TransportResult:
    return TransportResult(False, result.error_message, None, False, status, secrets)
