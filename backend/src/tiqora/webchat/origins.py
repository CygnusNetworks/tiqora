"""Origin handling for the web-chat widget: allowed websites, same-origin checks, frame CSP.

Everything here is pure (no I/O; ``own_origin`` only logs a one-time warning). The CSP
header is built only from strings that matched
``_ORIGIN_RE`` (``[a-z0-9.-]`` host, digits port), so nothing a visitor or an admin types can
break out of a directive.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Final
from urllib.parse import urlsplit

import idna
import structlog
from starlette.requests import Request

from tiqora.config import Settings

logger = structlog.get_logger(__name__)

MAX_ORIGINS: Final = 20
CHAT_API_PATH: Final = "/api/chat/v1/"
_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ORIGIN_RE: Final = re.compile(r"(https?)://([a-z0-9.-]+)(?::([0-9]{1,5}))?")
_LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1"})
_DEFAULT_PORT: Final = {"http": 80, "https": 443}
_MAX_RAW: Final = 300


class OriginError(ValueError):
    """An origin string is not acceptable (German message for the admin form)."""


def _canon(raw: str, *, allow_plain_http: bool) -> str:
    """Strict canonical ``scheme://host[:port]`` or ``OriginError``."""
    if not isinstance(raw, str):
        raise OriginError("Ungültige Website-Adresse.")
    value = raw.strip()
    if not value:
        raise OriginError("Bitte eine Website-Adresse angeben.")
    if len(value) > _MAX_RAW or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise OriginError("Die Website-Adresse enthält unzulässige Zeichen.")
    if "*" in value:
        raise OriginError("Platzhalter (*) sind nicht erlaubt.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise OriginError("Ungültige Website-Adresse.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise OriginError("Nur https:// (oder http://localhost) ist erlaubt.")
    if parts.netloc.endswith(":"):
        raise OriginError("Ungültiger Port in der Website-Adresse.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise OriginError("Zugangsdaten in der Adresse sind nicht erlaubt.")
    if parts.path not in ("", "/") or parts.query or parts.fragment or "?" in value or "#" in value:
        raise OriginError("Bitte nur die Website ohne Pfad angeben, z. B. https://www.example.org.")
    host = parts.hostname or ""
    if not host:
        raise OriginError("Die Website-Adresse hat keinen Host.")
    try:
        ascii_host = idna.encode(host, uts46=True, transitional=False).decode("ascii").lower()
    except (idna.IDNAError, UnicodeError) as exc:
        raise OriginError("Der Host der Website-Adresse ist ungültig.") from exc
    if ascii_host.endswith(".") or ".." in ascii_host or ascii_host.startswith("."):
        raise OriginError("Der Host der Website-Adresse ist ungültig.")
    if not allow_plain_http and scheme == "http" and ascii_host not in _LOCAL_HOSTS:
        raise OriginError("Nur https:// ist erlaubt (http nur für localhost).")
    if port is not None and not 1 <= port <= 65535:
        raise OriginError("Ungültiger Port in der Website-Adresse.")
    out = f"{scheme}://{ascii_host}"
    if port is not None and port != _DEFAULT_PORT[scheme]:
        out += f":{port}"
    if _ORIGIN_RE.fullmatch(out) is None:
        raise OriginError("Die Website-Adresse enthält unzulässige Zeichen.")
    return out


def normalize_origin(raw: str) -> str:
    """Normalise an admin-entered origin; see plan C3. Raises ``OriginError``."""
    return _canon(raw, allow_plain_http=False)


def validate_origins(values: Iterable[str]) -> list[str]:
    """Normalise, de-duplicate (order kept) and cap at ``MAX_ORIGINS``."""
    out: list[str] = []
    for value in values:
        norm = normalize_origin(value)
        if norm not in out:
            out.append(norm)
    if len(out) > MAX_ORIGINS:
        raise OriginError(f"Höchstens {MAX_ORIGINS} Websites sind erlaubt.")
    return out


def origin_allowed(origin: str | None, allowed: Sequence[str]) -> bool:
    """Exact match of the normalised origin against the (normalised) allow-list."""
    if not origin:
        return False
    try:
        norm = normalize_origin(origin)
    except OriginError:
        return False
    for entry in allowed:
        try:
            if normalize_origin(entry) == norm:
                return True
        except OriginError:
            continue
    return False


def _base_origin(public_base_url: str) -> str | None:
    """scheme://host[:port] of ``public_base_url`` (path ignored), ``None`` if unset or unusable."""
    base = (public_base_url or "").strip()
    if not base:
        return None
    try:
        parts = urlsplit(base)
        return _canon(f"{parts.scheme}://{parts.netloc}", allow_plain_http=True)
    except (OriginError, ValueError):
        return None


def own_origin(request: Request, settings: Settings) -> str:
    """Origin of ``public_base_url`` when set and usable, else scheme+netloc of the request.

    The request fallback is for development: in production it logs
    ``webchat_public_base_url_missing`` once (TLS usually ends at a proxy, so the request
    scheme is ``http``).

    Raises ``OriginError`` when the fallback Host is not a clean origin (trailing dot, IPv6
    literal, ...). The Host fallback is only trusted as far as the deployment sets no
    ``public_base_url``; callers map the error to 403.
    """
    base = _base_origin(settings.public_base_url)
    if base is not None:
        return base
    if settings.is_production:
        _warn_public_base_url_missing()
    return _canon(f"{request.url.scheme}://{request.url.netloc}", allow_plain_http=True)


_base_url_warning = {"done": False}


def _warn_public_base_url_missing() -> None:
    """Behind a TLS-terminating proxy the request origin is wrong; say so once per process."""
    if _base_url_warning["done"]:
        return
    _base_url_warning["done"] = True
    logger.warning(
        "webchat_public_base_url_missing",
        hint="Set TIQORA_PUBLIC_BASE_URL to the public https:// address of Tiqora.",
    )


def reset_public_base_url_warning() -> None:
    """Tests: let the once-per-process warning fire again."""
    _base_url_warning["done"] = False


def referer_origin(value: str | None) -> str | None:
    """Origin (``scheme://host[:port]``) of a Referer URL, ``None`` when absent or malformed."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        return _canon(f"{parts.scheme}://{parts.netloc}", allow_plain_http=True)
    except (OriginError, ValueError):
        return None


def preview_request_allowed(origin_param: str | None, referer: str | None, own: str) -> bool:
    """R-PREVIEW: ``origin`` is Tiqora's own origin AND the Referer comes from it (missing: no)."""
    if not origin_param:
        return False
    try:
        if _canon(origin_param, allow_plain_http=True) != own:
            return False
    except OriginError:
        return False
    return referer_origin(referer) == own


def _origin_netloc(value: str) -> str | None:
    """Lower-case ``host[:port]`` (default port dropped) of a strict Origin header value."""
    m = _ORIGIN_RE.fullmatch(value.strip(" \t").lower())
    if m is None:
        return None
    scheme, host, port = m.groups()
    if host.endswith(".") or host.startswith(".") or ".." in host:
        return None
    if port is not None and int(port) != _DEFAULT_PORT[scheme]:
        return f"{host}:{int(port)}"
    return host


def _host_netloc(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    for default in (":80", ":443"):
        if raw.endswith(default):
            return raw[: -len(default)]
    return raw


def same_origin_ok(
    method: str,
    origins: Sequence[str],
    sec_fetch_site: str | None,
    host_header: str | None,
    public_base_url: str,
) -> bool:
    """R-ORIGIN: browser requests to ``/api/chat/v1`` must come from Tiqora's own origin.

    Unsafe methods REQUIRE exactly one ``Origin``; ``null``, ``Sec-Fetch-Site: cross-site`` and
    any host that is neither the ``public_base_url`` host nor the request ``Host`` are refused.
    Host[:port] only is compared (like ``security/csrf.py``), not the scheme.
    Safe methods may omit ``Origin`` but a present one must match too.
    """
    unsafe = method.upper() in _UNSAFE_METHODS
    if len(origins) > 1:
        return False
    if unsafe and (sec_fetch_site or "").strip().lower() == "cross-site":
        return False
    if not origins:
        return not unsafe
    netloc = _origin_netloc(origins[0])
    if netloc is None:
        return False
    allowed: set[str] = set()
    host = _host_netloc(host_header)
    if host:
        allowed.add(host)
    base = _base_origin(public_base_url)
    base_netloc = _origin_netloc(base) if base else None
    if base_netloc:
        allowed.add(base_netloc)
    return netloc in allowed


def request_same_origin_ok(request: Request, settings: Settings) -> bool:
    headers = request.headers
    return same_origin_ok(
        request.method,
        headers.getlist("origin"),
        headers.get("sec-fetch-site"),
        headers.get("host"),
        settings.public_base_url,
    )


FRAME_CSP_BASE: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "font-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
)


def frame_csp(
    allowed: Sequence[str],
    *,
    own: str,
    origin: str | None = None,
    preview_only: bool = False,
) -> str:
    """Content-Security-Policy of the chat frame (R-FRAME-ANCESTORS, R-FRAME-CONNECT).

    ``frame-ancestors`` is ``'self'`` plus ONLY the embedding ``origin`` which must be valid and
    in ``allowed`` (else ``OriginError``); ``preview_only`` or no origin gives ``'self'`` alone.
    ``connect-src`` is restricted to the chat API path of ``own``, which must be a bare origin
    (pass ``own_origin()``, never ``settings.public_base_url``).
    """
    own_norm = _canon(own, allow_plain_http=True)
    ancestors = "'self'"
    if origin is not None and not preview_only:
        norm = normalize_origin(origin)
        if not origin_allowed(norm, allowed):
            raise OriginError("Diese Website ist für den Chat nicht freigegeben.")
        ancestors += f" {norm}"
    return f"{FRAME_CSP_BASE}; connect-src {own_norm}{CHAT_API_PATH}; frame-ancestors {ancestors}"
