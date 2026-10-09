"""CSRF exemption of the SAML POST endpoints and the OIDC back-channel logout only.

Wave 1, Tasks 10 and 21. Pure, no DB.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from tiqora.config import Settings
from tiqora.security.csrf import csrf_check_required, origin_allowed

SETTINGS = Settings(
    environment="test",
    csrf_origin_check_enabled=True,
    cors_origins="https://helpdesk.example.org",
)
BOTH = {"tiqora_session": "a", "tiqora_customer_session": "b"}
FOREIGN = {"origin": "https://idp.example.org"}


def _req(method: str, path: str, cookies: dict[str, str] | None = None) -> Request:
    headers = [
        (b"host", b"helpdesk.example.org"),
        *((k.encode(), v.encode()) for k, v in FOREIGN.items()),
    ]
    if cookies:
        headers.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("helpdesk.example.org", 443),
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/saml/x/acs",
        "/api/v1/auth/saml/x/sls",
        "/api/portal/auth/saml/x/acs",
        "/api/v1/auth/saml/idptest-saml-a/acs",
        "/api/v1/auth/oidc/x/backchannel-logout",
        "/api/v1/auth/oidc/env-oidc/backchannel-logout",
    ],
)
def test_saml_post_endpoints_are_exempt(path: str) -> None:
    assert csrf_check_required(_req("POST", path, BOTH), SETTINGS) is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/saml/x/acs/extra",
        "/api/v1/auth/saml/x/acs/",
        "/api/v1/auth/saml/X/acs",
        "/api/v1/auth/saml/x/metadata",
        "/api/portal/auth/saml/x/sls",
        "/api/v1/auth/oidc/x/backchannel-logout/extra",
        "/api/v1/auth/oidc/x/backchannel-logout/",
        "/api/v1/auth/oidc/X/backchannel-logout",
        "/api/portal/auth/oidc/x/backchannel-logout",
        "/api/v1/auth/oidc/x/callback",
        "/api/v1/auth/logout",
        "/api/v1/auth/sso/logout",
        "/api/v1/tickets",
    ],
)
def test_everything_else_with_a_cookie_is_still_checked(path: str) -> None:
    req = _req("POST", path, BOTH)
    assert csrf_check_required(req, SETTINGS) is True
    assert origin_allowed(req, SETTINGS) is False


def test_backchannel_logout_without_cookies_needs_no_exemption() -> None:
    req = _req("POST", "/api/v1/auth/oidc/x/backchannel-logout")
    assert csrf_check_required(req, SETTINGS) is False


def test_single_session_cookie_logout_is_checked() -> None:
    req = _req("POST", "/api/v1/auth/logout", {"tiqora_session": "a"})
    assert csrf_check_required(req, SETTINGS) is True
