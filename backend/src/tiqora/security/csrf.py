"""Conservative Origin/Referer CSRF check for cookie-authenticated mutations.

Only applies to state-changing requests on ``/api/v1`` and ``/api/portal`` that
carry a session cookie and are **not** authenticated via ``Authorization``
(API keys are CSRF-immune). Safe methods and missing cookies are left alone so
the SPA (same-origin) and non-browser tools keep working (SECURITY M-02).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tiqora.config import Settings

CallNext = Callable[[Request], Awaitable[Response]]

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PROTECTED_PREFIXES = ("/api/v1", "/api/portal")


def _origin_host(value: str) -> str | None:
    """Return lowercase netloc (host[:port]) from an Origin or Referer URL."""
    raw = (value or "").strip()
    if not raw or raw == "null":
        return None
    # Origin is scheme://host[:port] with no path; Referer may include a path.
    parsed = urlparse(raw if "://" in raw else f"//{raw}", scheme="")
    host = (parsed.netloc or parsed.path.split("/")[0] or "").lower()
    return host or None


def _allowed_hosts(settings: Settings, request: Request) -> set[str]:
    hosts: set[str] = set()
    for origin in settings.cors_origin_list:
        h = _origin_host(origin)
        if h:
            hosts.add(h)
    # Same-origin SPA: always accept the Host the browser is talking to.
    host_header = (request.headers.get("host") or "").strip().lower()
    if host_header:
        hosts.add(host_header)
    # Request URL host as a fallback (ASGI often mirrors Host).
    if request.url.hostname:
        port = request.url.port
        if port and port not in (80, 443):
            hosts.add(f"{request.url.hostname.lower()}:{port}")
        else:
            hosts.add(request.url.hostname.lower())
    return hosts


def request_has_session_cookie(request: Request, settings: Settings) -> bool:
    """True when either agent or customer session cookie is present."""
    cookies = request.cookies
    return bool(
        cookies.get(settings.session_cookie_name)
        or cookies.get(settings.customer_session_cookie_name)
    )


def request_has_authorization(request: Request) -> bool:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    return bool(auth and auth.strip())


def csrf_check_required(request: Request, settings: Settings) -> bool:
    if not settings.csrf_origin_check_enabled:
        return False
    if request.method.upper() not in _UNSAFE:
        return False
    path = request.url.path
    if not any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES):
        return False
    # API-key / Bearer clients are CSRF-immune.
    if request_has_authorization(request):
        return False
    # No session cookie → nothing to forge (login itself has no cookie yet).
    return request_has_session_cookie(request, settings)


def origin_allowed(request: Request, settings: Settings) -> bool:
    allowed = _allowed_hosts(settings, request)
    origin = request.headers.get("origin")
    if origin:
        host = _origin_host(origin)
        return bool(host and host in allowed)
    referer = request.headers.get("referer")
    if referer:
        host = _origin_host(referer)
        return bool(host and host in allowed)
    # Cookie present, unsafe method, no Origin/Referer → reject (browser always
    # sends Origin on cross-site POSTs; same-origin SPA sends Origin too).
    return False


async def csrf_origin_middleware(request: Request, call_next: CallNext) -> Response:
    """Starlette middleware entry — reads settings from ``app.state``."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if (
        settings is not None
        and csrf_check_required(request, settings)
        and not origin_allowed(request, settings)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF origin check failed"},
        )
    return await call_next(request)
