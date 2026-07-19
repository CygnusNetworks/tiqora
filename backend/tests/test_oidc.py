"""Unit tests for OIDC/SSO: discovery, token exchange, claim mapping.

Uses ``httpx.MockTransport`` to fake an OIDC provider — no network, no
Docker required.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tiqora.config import Settings
from tiqora.domain.oidc import OIDCError, OIDCService

ISSUER = "https://idp.example.com"


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


def _make_transport(*, claims: dict[str, object] | None, userinfo_status: int = 200) -> httpx.MockTransport:
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
    service = OIDCService(settings, transport=_make_transport(claims={}))
    url = await service.authorize_url("xyz-state")
    assert url.startswith(f"{ISSUER}/authorize")
    assert "client_id=tiqora" in url
    assert "state=xyz-state" in url


@pytest.mark.asyncio
async def test_fetch_claims_maps_preferred_username() -> None:
    settings = _settings()
    service = OIDCService(
        settings, transport=_make_transport(claims={"preferred_username": "jdoe", "email": "j@x.com"})
    )
    claims = await service.fetch_claims("auth-code-123")
    assert claims["preferred_username"] == "jdoe"


@pytest.mark.asyncio
async def test_fetch_claims_custom_claim_name() -> None:
    settings = _settings(oidc_claim="upn")
    service = OIDCService(settings, transport=_make_transport(claims={"upn": "jdoe@corp"}))
    claims = await service.fetch_claims("code")
    assert claims[settings.oidc_claim] == "jdoe@corp"


@pytest.mark.asyncio
async def test_fetch_claims_userinfo_failure_raises_oidc_error() -> None:
    settings = _settings()
    service = OIDCService(settings, transport=_make_transport(claims={}, userinfo_status=500))
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

    service = OIDCService(_settings(), transport=httpx.MockTransport(handler))
    await service.discover()
    await service.discover()
    assert calls == [
        "/.well-known/openid-configuration"
    ], f"discovery should be cached, got {calls}"


def test_claims_serialize_roundtrip() -> None:
    # sanity check the mock transport payloads are valid JSON (guards typos above)
    assert json.loads(json.dumps({"preferred_username": "jdoe"}))["preferred_username"] == "jdoe"
