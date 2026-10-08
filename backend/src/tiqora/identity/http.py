"""HTTP helpers shared by the SSO routes (agent and portal)."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from fastapi import status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tiqora.config import Settings
from tiqora.identity.types import UserKind


def safe_next(next_path: str | None, *, default: str = "/") -> str:
    """Same-site absolute path only — reject protocol-relative (``//evil``),
    backslash tricks (``/\\evil``) and anything carrying a scheme/host, so the
    post-SSO redirect can never be turned into an open redirect."""
    if (
        isinstance(next_path, str)
        and next_path.startswith("/")
        and not next_path.startswith("//")
        and not next_path.startswith("/\\")
    ):
        return next_path
    return default


def safe_portal_next(next_path: str | None) -> str:
    """Like :func:`safe_next`, but the target must live under ``/portal``."""
    candidate = safe_next(next_path, default="/portal")
    if candidate == "/portal" or candidate.startswith(("/portal/", "/portal?", "/portal#")):
        return candidate
    return "/portal"


def login_error_redirect(user_kind: UserKind, code: str, next_path: str) -> RedirectResponse:
    base = "/login" if user_kind == "agent" else "/portal/login"
    target = f"{base}?sso_error={quote(code, safe='')}&next={quote(next_path, safe='')}"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


def login_landing_response(target: str) -> HTMLResponse:
    """Tiny page for cross-site POST logins (SAML ACS).

    A 302 straight after a cross-site POST would not carry ``SameSite=Lax``
    cookies; this page navigates same-site so the new session cookie is sent.
    """
    href = escape(target, quote=True)
    body = (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={href}">'
        "<title>Anmeldung</title></head><body>"
        f'<p>Anmeldung erfolgreich. <a href="{href}">Weiter</a></p>'
        "</body></html>"
    )
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


def set_session_cookie(
    response: Response, settings: Settings, token: str, *, user_kind: UserKind
) -> None:
    name = (
        settings.session_cookie_name
        if user_kind == "agent"
        else settings.customer_session_cookie_name
    )
    samesite: Any = settings.session_cookie_samesite
    response.set_cookie(
        key=name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=samesite,
        max_age=settings.session_ttl_seconds,
        path="/",
    )
