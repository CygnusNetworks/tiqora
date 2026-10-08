"""REST request data rules (port of ``Transport/HTTP/REST.pm`` ``ProviderProcessRequest``).

Everything here reproduces Znuny's behaviour on purpose, including its quirks: URI data wins
over the body, a first query value of ``"0"`` becomes ``""`` (``$Value || ''``), JSON booleans
become 1/0 and numbers are normalised the way Perl would stringify them.
"""

from __future__ import annotations

import codecs
import json
import re
from typing import Any
from urllib.parse import quote, unquote

import structlog
from starlette.requests import Request

from tiqora.api.compat.mapping.perl import perl_truthy
from tiqora.api.compat.webservices import Webservice
from tiqora.znuny.perl_format import perl_num

logger = structlog.get_logger(__name__)

X_OTRS_HEADERS = {  # request header (case-insensitive) -> data key; header overrides any URI value
    "X-OTRS-Header-UserLogin": "UserLogin",
    "X-OTRS-Header-CustomerUserLogin": "CustomerUserLogin",
    "X-OTRS-Header-SessionID": "SessionID",
    "X-OTRS-Header-Password": "Password",
    "X-OTRS-Header-ImpersonateAsUser": "ImpersonateAsUser",
    "X-OTRS-Header-TwoFactorToken": "TwoFactorToken",
}

_SUBPATH = re.compile(r".*Webservice(?:ID)?/[^/]+(/.*)$", re.DOTALL)
_CHARSET = re.compile(r"\A.*charset=[\"']?([^\"']+)[\"']?\Z", re.IGNORECASE | re.DOTALL)
_UTF8 = re.compile(r"\Autf-?8\Z", re.IGNORECASE)


class RestRequestError(Exception):
    """A REST transport error; Znuny answers these as ``text/plain`` with ``status``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _unescape(value: str) -> str:
    return unquote(value, errors="replace")


def parse_query_string(raw: str) -> dict[str, Any]:
    """Znuny's hand-rolled query parser (not ``parse_qs``): see module docstring for quirks."""
    params: dict[str, Any] = {}
    parts = re.split(r"[;&]", raw)
    while parts and parts[-1] == "":  # Perl split drops trailing empty fields
        parts.pop()
    for part in parts:
        pieces = part.split("=")
        while pieces and pieces[-1] == "":
            pieces.pop()
        # an empty part ("a=1&&b=2") yields no pieces: Znuny stores it as key "" -> ""
        key = _unescape(pieces[0]) if pieces else ""
        value = _unescape(pieces[1].replace("+", "%20")) if len(pieces) > 1 else ""
        if key not in params:
            params[key] = value if perl_truthy(value) else ""  # Perl: $Value || ''
        elif isinstance(params[key], str):
            params[key] = [params[key], value]
        else:
            params[key].append(value)
    return params


def _perl_json_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict):
        return {key: _perl_json_value(entry) for key, entry in value.items()}
    if isinstance(value, list):
        return [_perl_json_value(entry) for entry in value]
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def znuny_json_decode(body: bytes | str) -> Any:
    """``Kernel::System::JSON::Decode``: true/false -> 1/0, floats as Perl numbers.

    ``1.0`` becomes ``1`` and ``2.50`` stays ``2.5`` (Perl prints ``1`` / ``2.5``); NaN/Infinity
    are rejected like JSON::XS does. Raises ``ValueError`` on malformed JSON.
    """
    decoded = json.loads(
        body,
        parse_float=lambda text: perl_num(float(text)),
        parse_constant=_reject_constant,
    )
    return _perl_json_value(decoded)


def rest_request_path(request: Request) -> str:
    """Request path after ``Webservice(ID)/<x>``, still percent-encoded, without query.

    Starlette's ``path`` is already decoded, so the raw path is used (``%2F`` must not become
    a separator); route variables are unescaped once by :func:`match_rest_route`.
    """
    raw = request.scope.get("raw_path")
    path = raw.decode("latin-1") if isinstance(raw, bytes) else quote(request.scope["path"])
    path = path.split("?", 1)[0]
    found = _SUBPATH.match(path)
    return found.group(1) if found else "/"


def _known_charset(name: str) -> str:
    """Unknown charsets fall back to ISO-8859-1 like Znuny's Encode handling (logged)."""
    try:
        return codecs.lookup(name).name
    except LookupError:
        logger.warning("compat_rest_unknown_charset", charset=name)
        return "iso-8859-1"


def _is_hash_with_data(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


async def build_rest_request_data(
    request: Request, ws: Webservice, uri_data: dict[str, str]
) -> Any:
    """Combine query, path variables, X-OTRS headers and JSON body into the operation input."""
    query = request.scope.get("query_string", b"")
    query_params = parse_query_string(query.decode("latin-1")) if query else {}
    uri: dict[str, Any] = {**query_params, **uri_data} if query_params else dict(uri_data)
    for header, key in X_OTRS_HEADERS.items():
        value = request.headers.get(header)
        if value:
            uri[key] = value

    method = request.method
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if not length or method == "GET":
        return {**uri, "RequestMethod": method}

    if ws.max_length is not None and length > ws.max_length:
        raise RestRequestError(413, "Request Entity Too Large")
    body = await request.body()
    if ws.max_length is not None and len(body) > ws.max_length:
        raise RestRequestError(413, "Request Entity Too Large")
    if not body:
        raise RestRequestError(500, "Could not read input data")
    body = body.replace(b"\xef\xbb\xbf", b"")

    charset = _CHARSET.match(request.headers.get("content-type", ""))
    try:
        if charset and not _UTF8.match(charset.group(1)):
            text = body.decode(_known_charset(charset.group(1)))
        else:
            text = body.decode("utf-8")
        decoded = znuny_json_decode(text)
    except (ValueError, LookupError, RecursionError):
        decoded = None
    if not isinstance(decoded, (dict, list)) and not perl_truthy(decoded):  # Perl: !$ContentDecoded
        raise RestRequestError(500, "Error while decoding request content.")

    if _is_hash_with_data(decoded):
        return {**decoded, **uri}
    if isinstance(decoded, list) and decoded:
        return [{**entry, **uri} if _is_hash_with_data(entry) else entry for entry in decoded]
    raise RestRequestError(500, "Unsupported request content structure.")
