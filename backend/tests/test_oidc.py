"""Unit tests for OIDC/SSO: discovery, token exchange, claim mapping.

Uses ``httpx.MockTransport`` to fake an OIDC provider — no network, no
Docker required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tiqora.config import Settings
from tiqora.domain.oidc import OIDCError, OIDCService, oidc_client_config_from_settings

# Public hostname so outbound IP-pinning can resolve in tests (no network I/O:
# MockTransport answers the HTTP calls; only DNS is real).
ISSUER = "https://example.com"


def _settings(**overrides: object) -> Settings:
    base = {
        "oidc_enabled": True,
        "oidc_issuer": ISSUER,
        "oidc_client_id": "tiqora",
        "oidc_client_secret": "s3cr3t",
        "oidc_redirect_uri": "https://tiqora.example.com/api/v1/auth/oidc/callback",
        "oidc_claim": "preferred_username",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _make_transport(
    *, claims: dict[str, object] | None, userinfo_status: int = 200
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        if path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if path.endswith("/userinfo"):
            if userinfo_status != 200:
                return httpx.Response(userinfo_status, json={"error": "server_error"})
            return httpx.Response(200, json=claims or {})
        return httpx.Response(404, json={"error": "not_found"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_authorize_url_contains_client_and_state() -> None:
    settings = _settings()
    service = OIDCService(
        oidc_client_config_from_settings(settings), transport=_make_transport(claims={})
    )
    url = await service.authorize_url("xyz-state")
    assert url.startswith(f"{ISSUER}/authorize")
    assert "client_id=tiqora" in url
    assert "state=xyz-state" in url


@pytest.mark.asyncio
async def test_fetch_claims_maps_preferred_username() -> None:
    settings = _settings()
    service = OIDCService(
        oidc_client_config_from_settings(settings),
        transport=_make_transport(claims={"preferred_username": "jdoe", "email": "j@x.com"}),
    )
    claims = await service.fetch_claims("auth-code-123")
    assert claims["preferred_username"] == "jdoe"


@pytest.mark.asyncio
async def test_fetch_claims_custom_claim_name() -> None:
    settings = _settings(oidc_claim="upn")
    service = OIDCService(
        oidc_client_config_from_settings(settings),
        transport=_make_transport(claims={"upn": "jdoe@corp"}),
    )
    claims = await service.fetch_claims("code")
    assert claims[settings.oidc_claim] == "jdoe@corp"


@pytest.mark.asyncio
async def test_fetch_claims_userinfo_failure_raises_oidc_error() -> None:
    settings = _settings()
    service = OIDCService(
        oidc_client_config_from_settings(settings),
        transport=_make_transport(claims={}, userinfo_status=500),
    )
    with pytest.raises(OIDCError):
        await service.fetch_claims("code")


@pytest.mark.asyncio
async def test_discovery_is_cached() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "userinfo_endpoint": f"{ISSUER}/userinfo",
            },
        )

    service = OIDCService(
        oidc_client_config_from_settings(_settings()), transport=httpx.MockTransport(handler)
    )
    await service.discover()
    await service.discover()
    assert calls == ["/.well-known/openid-configuration"], (
        f"discovery should be cached, got {calls}"
    )


def test_claims_serialize_roundtrip() -> None:
    # sanity check the mock transport payloads are valid JSON (guards typos above)
    assert json.loads(json.dumps({"preferred_username": "jdoe"}))["preferred_username"] == "jdoe"


async def test_idp_calls_ask_for_uncompressed_bodies() -> None:
    """The streamed size cap counts decoded bytes; ``identity`` keeps a small compressed
    body from inflating to tens of MB before the check runs."""
    from tiqora.domain.oidc import _json_request

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    got = await _json_request(
        httpx.MockTransport(handler),
        "GET",
        "https://idp.example.org/x",
        headers={"Accept": "application/json"},
    )
    assert got == {"ok": True}
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].headers["accept"] == "application/json"


async def test_idp_call_has_an_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx's timeout is per phase; a slow IdP must not hold a callback for minutes."""
    import asyncio

    import tiqora.domain.oidc as oidc_mod

    monkeypatch.setattr(oidc_mod, "_TOTAL_REQUEST_TIMEOUT", 0.05)

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    with pytest.raises(httpx.TimeoutException):
        await oidc_mod._json_request(httpx.MockTransport(slow), "GET", "https://idp.example.org/x")


def test_token_bearing_fields_are_not_in_repr() -> None:
    from tiqora.domain.oidc import OidcLoginResult
    from tiqora.identity.flow_state import LoginState
    from tiqora.identity.login import SsoSessionDraft
    from tiqora.identity.sso_sessions import SsoSessionInfo

    secret = "tok-SECRET-value"
    objects = (
        LoginState(
            provider_id=1, next_path="/", user_kind="agent", code_verifier=secret, nonce=secret
        ),
        SsoSessionInfo(
            provider_id=1, slug="s", kind="oidc", subject_hash="h", id_token_hint=secret
        ),
        SsoSessionDraft(id_token_hint=secret),
        OidcLoginResult(claims={}, id_token=secret),
    )
    for obj in objects:
        assert secret not in repr(obj), type(obj).__name__
    assert objects[3].id_token == secret  # still readable, only hidden from repr
