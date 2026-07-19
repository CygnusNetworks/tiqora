"""OIDC/SSO login: authorization-code exchange + claim-based user mapping.

v1 deliberately does **not** auto-provision users: the mapped claim (default
``preferred_username``) must match an existing, ``valid_id=1`` row in
``users.login`` or the login is rejected. Provisioning/JIT user creation is
left for a future phase once role/group mapping policy is defined.

Uses authlib's :class:`~authlib.integrations.httpx_client.AsyncOAuth2Client`
for the token exchange so a fake provider can be substituted in tests via
``transport=httpx.MockTransport(...)``.
"""

from __future__ import annotations

from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from tiqora.config import Settings


class OIDCError(Exception):
    """Raised for discovery/token/userinfo failures."""


class OIDCService:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport
        self._discovery: dict[str, Any] | None = None

    async def discover(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        url = self._settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    async def authorize_url(self, state: str) -> str:
        disc = await self.discover()
        client = AsyncOAuth2Client(
            client_id=self._settings.oidc_client_id,
            client_secret=self._settings.oidc_client_secret,
            scope=self._settings.oidc_scopes,
            redirect_uri=self._settings.oidc_redirect_uri,
            transport=self._transport,
        )
        try:
            url, _ = client.create_authorization_url(disc["authorization_endpoint"], state=state)
            return str(url)
        finally:
            await client.aclose()

    async def fetch_claims(self, code: str) -> dict[str, Any]:
        """Exchange *code* for tokens and return the userinfo claims dict."""
        disc = await self.discover()
        client = AsyncOAuth2Client(
            client_id=self._settings.oidc_client_id,
            client_secret=self._settings.oidc_client_secret,
            redirect_uri=self._settings.oidc_redirect_uri,
            transport=self._transport,
        )
        try:
            try:
                token = await client.fetch_token(
                    disc["token_endpoint"], code=code, grant_type="authorization_code"
                )
            except Exception as exc:  # noqa: BLE001 — normalize provider errors
                raise OIDCError(f"token exchange failed: {exc}") from exc

            userinfo_endpoint = disc.get("userinfo_endpoint")
            if userinfo_endpoint:
                try:
                    resp = await client.get(userinfo_endpoint)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    raise OIDCError(f"userinfo request failed: {exc}") from exc
                claims: dict[str, Any] = resp.json()
                return claims

            # No userinfo endpoint advertised: fall back to whatever the
            # token response itself carries (some minimal test providers).
            claims_fallback = token.get("userinfo") if isinstance(token, dict) else None
            if isinstance(claims_fallback, dict):
                return claims_fallback
            raise OIDCError("provider has no userinfo_endpoint and returned no claims")
        finally:
            await client.aclose()
