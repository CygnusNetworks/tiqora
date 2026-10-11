"""REST requester transport of the GenericInterface (G7).

Port of ``Kernel/GenericInterface/Transport/HTTP/REST.pm`` ``RequesterPerformRequest`` and its
``_FlattenDataStructure``, with the Tiqora egress rules on top: the target is resolved and pinned
by :mod:`tiqora.gi.egress`, redirects are never followed, certificate files come only from the
configured directories, the response is size-capped, and every secret (passwords, tokens, header
values) is masked in the debugger.

Deliberate differences from Znuny (all safe-side):

* ``CONNECT``/``TRACE`` and ``JWT`` authentication are refused with a plain message.
* ``ReuseClient``/``KeepAlive`` are ignored (one client per call).
* A proxy password goes to the proxy only, never to the target as a request header.
* ``GET``-style query strings keep the order of the data (Perl uses random hash order); nested
  hashes have no sensible query form and are skipped (Perl would send ``HASH(0x...)``).
* A ``POST`` without remaining data is sent without a body (Perl hands the header hash to
  ``REST::Client`` as body there).
"""

from __future__ import annotations

import base64
import copy
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, TypeGuard
from urllib.parse import quote, quote_plus, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.mapping.perl import perl_str, perl_truthy
from tiqora.api.compat.mapping.xml_simple import XmlSimpleError, xml_out
from tiqora.api.compat.rest_request import znuny_json_decode
from tiqora.config import get_settings
from tiqora.domain.oauth2_mail import OAuth2MailError, get_access_token
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
from tiqora.gi.redact import MIN_SECRET_LENGTH, redact_headers, redact_text, redact_url
from tiqora.security.outbound import HostResolver, PinnedOutboundURL

_COMMANDS: Final = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
)
_BODY_COMMANDS: Final = frozenset({"POST", "PUT", "PATCH"})
_CONTENT_TYPES: Final = {
    "JSON": "application/json",
    "FORM": "application/x-www-form-urlencoded",
    "XML": "application/xml",
}
_DEFAULT_CONTENT_TYPE: Final = "application/json; charset=UTF-8"
_OAUTH2_PLACEHOLDER: Final = "OTRS_OAUTH2_TOKEN"
_UNKNOWN_PLACEHOLDER_RE: Final = re.compile(r"<OTRS_.*?>")
_TEXT_TYPE_RE: Final = re.compile(r"text/(plain|html|xml)")
_SUCCESS_CODE_RE: Final = re.compile(r"20\d")
_PERL_FLAGS: Final = re.MULTILINE | re.DOTALL


@dataclass(frozen=True, slots=True)
class TransportResult:
    success: bool
    error_message: str | None
    data: Any  # decoded response (dict/list/None, or {"Body": text} for text/*)
    size_exceeded: bool
    status_code: int | None
    secret_values: tuple[str, ...]  # values the caller must mask in later debugger rows


@dataclass(slots=True)
class PreparedRestRequest:
    """A fully built request, not yet sent. ``headers``/``body`` contain secrets: never log them
    directly, use :meth:`display`."""

    method: str
    controller: str
    host: str
    url: str  # Host + controller as configured (before pinning)
    headers: dict[str, str]
    body: bytes | None
    timeout: float
    error_base: str
    pinned: PinnedOutboundURL | None
    proxy_url: str | None
    proxy_headers: dict[str, str]
    ssl_context: Any
    secret_values: tuple[str, ...]
    extras: dict[str, Any] = field(default_factory=dict)

    def display(self) -> dict[str, Any]:
        """The request as it may be shown or stored: masked headers and URL, body size only."""
        return {
            "method": self.method,
            "url": redact_url(self.url),
            "headers": redact_headers(self.headers, secret_values=self.secret_values),
            "body_bytes": len(self.body) if self.body is not None else 0,
        }


# --------------------------------------------------------------------------------- helpers


def _is_hash_with_data(value: Any) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and bool(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_array_with_data(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _string_with_data(value: Any) -> bool:
    """``IsStringWithData``: a non-empty plain scalar."""
    if isinstance(value, (dict, list, tuple)) or value is None:
        return False
    return perl_str(value) != ""


def _scalar_text(value: Any) -> str | None:
    """Perl string of a scalar; ``None`` for undef and for references."""
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    return perl_str(value)


def _uri_escape(value: str) -> str:
    """``URI::Escape::uri_escape_utf8``: everything but the unreserved characters."""
    return quote(value, safe="")


def flatten_data_structure(data: Any) -> dict[str, Any]:
    """``_FlattenDataStructure``: nested data to ``Parent.Child`` / ``List.0`` keys.

    Keeps Znuny's quirks: falsy keys/elements (``""``, ``"0"``, ``0``, undef) are skipped
    without advancing the array counter, and containers inside arrays are not descended into.
    """
    flat: dict[str, Any] = {}
    _flatten(data, flat, "")
    return flat


def _flatten(data: Any, flat: dict[str, Any], prefix: str) -> None:
    if isinstance(data, dict):
        for key in sorted(data, key=str):
            if not perl_truthy(key):
                continue
            item = data[key]
            if _is_hash_with_data(item) or _is_array_with_data(item):
                _flatten(item, flat, f"{prefix}{key}.")
            else:
                flat[f"{prefix}{key}"] = item
        return
    if isinstance(data, list):
        count = 0
        for item in data:
            if not perl_truthy(item):
                continue
            if _is_hash_with_data(item) or _is_array_with_data(item):
                pass  # Perl recurses with a "HASH(0x..)." prefix nobody can address
            else:
                flat[f"{prefix}{count}"] = item
            count += 1


def interpolate_controller(controller: str, data: Mapping[str, Any]) -> tuple[str, set[str]]:
    """Replace ``:Name`` placeholders of the controller and its query by values of *data*.

    Returns the new controller (path, plus the query string only when at least one query
    placeholder was filled - Znuny drops a query part without hits) and the top-level data
    keys that were used (to be removed from the body).
    """
    match = re.match(r"([^?]+)(.+)?", controller, re.DOTALL)
    if match is None:
        return controller, set()
    path = match.group(1)
    query = match.group(2) or ""
    flat = flatten_data_structure(data)
    used: set[str] = set()

    for name in sorted(flat):
        pattern = re.compile(f":{re.escape(name)}(?=/|\\?|$)", _PERL_FLAGS)
        if not pattern.search(path):
            continue
        text = _scalar_text(flat[name])
        if text is None:
            continue
        escaped = _uri_escape(text)
        path = pattern.sub(lambda _m, e=escaped: e, path)
        if "." not in name:
            used.add(name)

    if query:
        replaced = False
        for name in sorted(flat):
            pattern = re.compile(f":{re.escape(name)}(?=&|$)", _PERL_FLAGS)
            if not pattern.search(query):
                continue
            text = _scalar_text(flat[name])
            if text is None:
                continue
            escaped = _uri_escape(text)
            query = pattern.sub(lambda _m, e=escaped: e, query)
            replaced = True
            if "." not in name:
                used.add(name)
        if replaced:
            path += query
    return path, used


def build_query(data: Mapping[str, Any]) -> str:
    """``REST::Client::buildQuery`` (``URI::query_form``): ``?a=1&b=2``, in the order of *data*.

    List values repeat the key, undef becomes an empty value, space becomes ``+``.
    """
    pairs: list[str] = []
    for key, value in data.items():
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            if isinstance(item, dict | list | tuple):
                continue
            text = "" if item is None else perl_str(item)
            pairs.append(f"{_form_escape(str(key))}={_form_escape(text)}")
    return "?" + "&".join(pairs) if pairs else ""


def _form_escape(value: str) -> str:
    return quote_plus(value, safe="!*'()")


def _json_body(data: Any) -> str:
    """``JSON->Encode`` (compact, insertion order) incl. the escaped unicode line separators."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _utf8_safe(text: str) -> str:
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    """Set a header, replacing one of the same name in any letter case (HTTP::Headers)."""
    for existing in [k for k in headers if k.lower() == name.lower()]:
        del headers[existing]
    headers[name] = value


def _fail(
    message: str, *, status: int | None = None, secrets: tuple[str, ...] = ()
) -> TransportResult:
    return TransportResult(False, message, None, False, status, secrets)


def _secret_header_values(values: list[str]) -> list[str]:
    return [v for v in values if len(v) >= MIN_SECRET_LENGTH]


# ------------------------------------------------------------------------ request building


async def build_rest_request(
    *,
    transport_config: Mapping[str, Any],
    operation: str,
    data: Any,
    debugger: GiDebugger,
    policy: EgressPolicy,
    session: AsyncSession,
    resolver: HostResolver | None = None,
) -> PreparedRestRequest | TransportResult:
    """Everything of ``RequesterPerformRequest`` up to (not including) the network call.

    Used by the real call and by the dry run. Returns a failed :class:`TransportResult` for any
    configuration, egress or token problem (already written to the debugger).
    """
    config = transport_config  # the transport's ``Config`` hash (Host, DefaultCommand, ...)
    if not _is_hash_with_data(config):
        return _fail("REST Transport: Have no Config")
    assert isinstance(config, dict)

    for needed in ("Host", "DefaultCommand", "Timeout"):
        if not _string_with_data(config.get(needed)):
            return _fail(f"REST Transport: Have no {needed} in config")
    if data is not None and not isinstance(data, dict):
        return _fail("REST Transport: Invalid Data")
    if not _string_with_data(operation):
        return _fail("REST Transport: Need Operation")
    try:
        timeout = min(float(perl_str(config["Timeout"])), policy.max_timeout)
    except ValueError:
        return _fail("REST Transport: Invalid Timeout in config")
    if not math.isfinite(timeout) or timeout <= 0:
        return _fail("REST Transport: Invalid Timeout in config")

    host = perl_str(config["Host"])
    payload: dict[str, Any] = copy.deepcopy(data) if isinstance(data, dict) else {}
    secrets: list[str] = []
    headers: dict[str, str] = {"Content-Type": _DEFAULT_CONTENT_TYPE}

    async def error(message: str, detail: Any = None) -> TransportResult:
        await debugger.error(message, detail)
        return _fail(redact_text(message, secrets), secrets=tuple(secrets))

    # ---- client certificates / TLS / proxy (egress) --------------------------------------
    x509 = _dict(config.get("X509"))
    ssl_cfg = _dict(config.get("SSL"))
    use_x509 = perl_str(x509.get("UseX509")) == "Yes"
    use_ssl = perl_str(ssl_cfg.get("UseSSL")) == "Yes"
    cert_file = (ssl_cfg.get("SSLCertificate") if use_ssl else None) or (
        x509.get("X509CertFile") if use_x509 else None
    )
    key_file = (ssl_cfg.get("SSLKey") if use_ssl else None) or (
        x509.get("X509KeyFile") if use_x509 else None
    )
    ca_file = (ssl_cfg.get("SSLCAFile") if use_ssl else None) or (
        x509.get("X509CAFile") if use_x509 else None
    )
    ca_dir = ssl_cfg.get("SSLCADir") if use_ssl else None
    key_password = _scalar_text(ssl_cfg.get("SSLPassword")) if use_ssl else None
    if key_password:
        secrets.append(key_password)
    no_verify = perl_truthy(config.get("SSLNoHostnameVerification"))
    ssl_context = None
    proxy_url: str | None = None
    proxy_headers: dict[str, str] = {}
    try:
        if cert_file or key_file or ca_file or ca_dir or no_verify:
            ssl_context = build_ssl_context(
                policy=policy,
                verify_hostname=not no_verify,
                cert_file=perl_str(cert_file) if cert_file else None,
                key_file=perl_str(key_file) if key_file else None,
                key_password=key_password or None,
                ca_file=perl_str(ca_file) if ca_file else None,
                ca_dir=perl_str(ca_dir) if ca_dir else None,
            )
        proxy_cfg = config.get("Proxy")
        if isinstance(proxy_cfg, dict) and _string_with_data(proxy_cfg.get("ProxyPassword")):
            secrets.append(perl_str(proxy_cfg["ProxyPassword"]))
        proxy_url, proxy_headers = proxy_for(config, policy)
    except EgressError as exc:
        return await error(str(exc))
    for value in proxy_headers.values():
        secrets.append(value.rsplit(" ", 1)[-1])
    debugger.add_secrets(*secrets)

    # ---- authentication ------------------------------------------------------------------
    auth = _dict(config.get("Authentication"))
    auth_type = perl_str(auth.get("AuthType")) if auth else ""
    if auth_type == "JWT":
        return await error("Anmeldung per JWT wird von Tiqora nicht unterstützt.")
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

    # ---- additional headers (OAuth2 token placeholder) -----------------------------------
    # Resolved up front so that their values are masked from the first debugger line on.
    extra_headers: dict[str, str] = {}
    additional = config.get("AdditionalHeaders")
    if _is_hash_with_data(additional):
        placeholders: dict[str, str] = {}
        config_id = auth.get("OAuth2TokenConfigID")
        if auth_type == "OAuth2Token" and perl_truthy(config_id):
            try:
                token_value = await get_access_token(session, config_id=int(perl_str(config_id)))
            except (OAuth2MailError, httpx.HTTPError, ValueError):
                token_value = ""
            if not token_value:
                return await error(
                    f"OAuth2 token for config with ID {perl_str(config_id)} could not be retrieved."
                )
            placeholders[_OAUTH2_PLACEHOLDER] = token_value
            secrets.append(token_value)
            debugger.add_secrets(token_value)
        for name in sorted(additional):
            raw = additional[name]
            if raw is None:
                continue
            value = perl_str(raw)
            for placeholder, replacement in sorted(placeholders.items()):
                value = value.replace(f"<{placeholder}>", replacement)
            extra_headers[str(name)] = _UNKNOWN_PLACEHOLDER_RE.sub("", value)
        found = _secret_header_values(list(extra_headers.values()))
        secrets.extend(found)
        debugger.add_secrets(*found)

    # ---- command and controller ----------------------------------------------------------
    mapping = config.get("InvokerControllerMapping")
    entry = mapping.get(operation) if isinstance(mapping, dict) else None
    command = perl_str(config["DefaultCommand"])
    if isinstance(entry, dict) and _string_with_data(entry.get("Command")):
        command = perl_str(entry["Command"])
    command = command.upper()
    if command not in _COMMANDS:
        return await error(f"'{command}' is not a valid REST command.")
    if command in ("CONNECT", "TRACE"):
        return await error(f"Das REST-Kommando '{command}' wird von Tiqora nicht unterstützt.")
    if not isinstance(entry, dict) or not _string_with_data(entry.get("Controller")):
        return await error(
            f"REST Transport: Have no Invoker <-> Controller mapping for Invoker '{operation}'."
        )

    if command in _BODY_COMMANDS:
        content_type = _CONTENT_TYPES.get(perl_str(config.get("ContentType")))
        if content_type:
            _set_header(headers, "Content-Type", content_type)

    request_headers = payload.pop("__RequestHeaders", None)
    if _is_hash_with_data(request_headers):
        headers = {str(k): perl_str(v) for k, v in request_headers.items()}
    append_headers = payload.pop("__RequestHeadersAppend", None)
    if _is_hash_with_data(append_headers):
        for name, value in append_headers.items():
            _set_header(headers, str(name), perl_str(value))

    controller, used = interpolate_controller(perl_str(entry["Controller"]), payload)
    await debugger.debug(
        "URI after interpolating URI params from outgoing data", redact_url(controller)
    )
    for name in used:
        payload.pop(name, None)

    # ---- body ----------------------------------------------------------------------------
    body: bytes | None = None
    if _is_hash_with_data(payload):
        if command in _BODY_COMMANDS:
            await debugger.debug("Remaining outgoing data to be sent", payload)
            media = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
            if media == "application/x-www-form-urlencoded":
                text = build_query(payload).removeprefix("?")
            elif media == "application/xml":
                try:
                    xml = xml_out(payload, no_attr=False, root_name=None)
                except XmlSimpleError:
                    xml = ""
                if not xml:
                    await debugger.error("Could not generate XML content for request.")
                text = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml
            else:
                text = _json_body(payload)
            body = text.encode("utf-8") if text else None
        else:
            query = build_query(payload)
            if "?" in controller:
                query = query.replace("?", "&", 1) if query.startswith("?") else query
            controller += query
            await debugger.debug(
                "URI after adding Query params from outgoing data", redact_url(controller)
            )
            await debugger.debug(
                "Remaining outgoing data to be sent",
                f"No data is sent in the request body as {command} command sets all Data as"
                " query params",
            )

    for name, value in extra_headers.items():
        _set_header(headers, name, value)
    # lone surrogates cannot be sent (nor logged): make them '?' instead of raising later
    headers = {_utf8_safe(k): _utf8_safe(v) for k, v in headers.items()}

    # ---- target --------------------------------------------------------------------------
    url = host + (controller if controller.startswith("/") else "/" + controller)
    error_base = (
        f"Error while performing REST '{command}' request to Controller "
        f"'{redact_url(controller)}' on Host '{redact_url(host)}'."
    )
    scheme = urlparse(url).scheme.lower()
    pinned: PinnedOutboundURL | None = None
    try:
        if proxy_url is None:
            pinned = pin(url, policy, resolver=resolver)
        elif scheme not in ("http", "https"):
            raise EgressError(f"Ziel {host} ist nicht erlaubt: nur http und https.")
    except EgressError as exc:
        return await error(str(exc))

    return PreparedRestRequest(
        method=command,
        controller=controller,
        host=host,
        url=url,
        headers=headers,
        body=body,
        timeout=timeout,
        error_base=redact_text(error_base, secrets),
        pinned=pinned,
        proxy_url=proxy_url,
        proxy_headers=proxy_headers,
        ssl_context=ssl_context,
        secret_values=tuple(dict.fromkeys(secrets)),
    )


# ---------------------------------------------------------------------------- the call


async def rest_perform_request(
    *,
    transport_config: Mapping[str, Any],
    operation: str,
    data: Any,
    debugger: GiDebugger,
    policy: EgressPolicy,
    session: AsyncSession,
    http_transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> TransportResult:
    prepared = await build_rest_request(
        transport_config=transport_config,
        operation=operation,
        data=data,
        debugger=debugger,
        policy=policy,
        session=session,
        resolver=resolver,
    )
    if isinstance(prepared, TransportResult):
        return prepared
    secrets = prepared.secret_values

    def failed(message: str, status: int | None = None) -> TransportResult:
        return TransportResult(False, redact_text(message, secrets), None, False, status, secrets)

    await debugger.debug("Request params from outgoing data", prepared.display())

    if prepared.pinned is not None:
        send_url = prepared.pinned.request_url
        wire_headers = prepared.pinned.request_headers(prepared.headers)
        extensions: dict[str, Any] = dict(prepared.pinned.request_extensions())
    else:
        send_url = prepared.url
        wire_headers = dict(prepared.headers)
        extensions = {}
    if any(c in n or c in v for n, v in wire_headers.items() for c in "\r\n\0"):
        detail = "Header name or value contains a control character (CR, LF or NUL)."
        await debugger.error(prepared.error_base, detail)
        return failed(f"{prepared.error_base} {detail}")
    # httpx encodes str header values as ASCII and would raise on "Müller". Znuny sends the
    # octets as they are, so send UTF-8 bytes (lone surrogates were replaced when building).
    wire_list = [(n.encode("utf-8"), v.encode("utf-8")) for n, v in wire_headers.items()]
    proxy: httpx.Proxy | None = None
    if prepared.proxy_url:
        proxy = httpx.Proxy(prepared.proxy_url, headers=prepared.proxy_headers or None)

    status: int | None = None
    try:
        async with client(
            policy=policy,
            ssl_context=prepared.ssl_context,
            proxy=proxy,
            transport=http_transport,
        ) as http:
            request = http.build_request(
                prepared.method,
                send_url,
                headers=wire_list,
                content=prepared.body,
                timeout=prepared.timeout,
                extensions=extensions,
            )
            response = await http.send(request, stream=True)
            try:
                status = response.status_code
                raw = await read_capped(response, policy.max_response_bytes)
                content_type = response.headers.get("content-type", "")
                location = response.headers.get("location")
            finally:
                await response.aclose()
    except EgressError as exc:
        await debugger.error(prepared.error_base, str(exc))
        return failed(f"{prepared.error_base} {exc}", status)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        # ValueError/TypeError: build_request rejecting a malformed header, URL or timeout
        detail = f"{type(exc).__name__}: {redact_text(str(exc), secrets)}"
        await debugger.error(prepared.error_base, detail)
        return failed(prepared.error_base, status)

    text = raw.decode("utf-8", errors="replace")
    response_error: str | None = None
    if not _SUCCESS_CODE_RE.fullmatch(str(status)):
        response_error = f"{prepared.error_base} Response code '{status}'."
    if status != 204 and not text:
        response_error = (response_error or "") + " No content provided."

    size_exceeded = len(raw) >= get_settings().gi_debug_max_content_bytes
    if size_exceeded:
        await debugger.debug(
            "JSON data received from remote system was too large for logging",
            "See SysConfig option GenericInterface::Operation::ResponseLoggingMaxSize to change"
            " the maximum (Tiqora: TIQORA_GI_DEBUG_MAX_CONTENT_BYTES).",
        )
    else:
        await debugger.debug("JSON data received from remote system", text)

    if response_error is not None:
        if location and 300 <= (status or 0) < 400:
            await debugger.info("Redirect not followed", f"Location: {redact_url(location)}")
        await debugger.error(
            response_error, f"Response content: '{text}'" if text else "No content provided."
        )
        return failed(response_error, status)

    if status == 204:
        return TransportResult(True, None, None, size_exceeded, status, secrets)

    if _TEXT_TYPE_RE.search(content_type):
        return TransportResult(True, None, {"Body": text}, size_exceeded, status, secrets)

    try:
        result = znuny_json_decode(text)
    except ValueError:
        result = None
    if not perl_truthy(result):
        message = f"{prepared.error_base} Error while parsing JSON data."
        await debugger.error(message)
        return failed(message, status)
    return TransportResult(True, None, result, size_exceeded, status, secrets)
