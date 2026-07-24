"""OIDC/SSO login: authorization-code exchange + claim-based user mapping.

v1 deliberately does **not** auto-provision users: the mapped claim (default
``preferred_username``) must match an existing, ``valid_id=1`` row in
``users.login`` or the login is rejected. Provisioning/JIT user creation is
left for a future phase once role/group mapping policy is defined.

Uses authlib's :class:`~authlib.integrations.httpx_client.AsyncOAuth2Client`
for the token exchange so a fake provider can be substituted in tests via
``transport=httpx.MockTransport(...)``.

Discovery, token, and userinfo URLs are validated (and discovery/userinfo
fetches IP-pinned) via :mod:`tiqora.security.outbound` so a malicious or
compromised issuer cannot target internal addresses.
"""

from __future__ import annotations

from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from tiqora.config import Settings
from tiqora.security.outbound import OutboundURLError, pin_outbound_url


class OIDCError(Exception):
    """Raised for discovery/token/userinfo failures."""


def _safe_get_url(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Pin *url* for an outbound GET; map SSRF errors to :class:`OIDCError`."""
    try:
        pinned = pin_outbound_url(url)
    except OutboundURLError as exc:
        raise OIDCError(f"outbound URL rejected: {exc}") from exc
    return pinned.request_url, pinned.request_headers(), pinned.request_extensions()


class OIDCService:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._discovery: dict[str, Any] | None = None

    async def discover(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        url = self._settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        request_url, headers, extensions = _safe_get_url(url)
        async with httpx.AsyncClient(transport=self._transport, follow_redirects=False) as client:
            resp = await client.get(request_url, headers=headers, extensions=extensions)
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
        token_endpoint = disc.get("token_endpoint")
        if not token_endpoint or not isinstance(token_endpoint, str):
            raise OIDCError("provider discovery is missing token_endpoint")

        # Pin the token POST exactly like userinfo — connect by resolved IP with
        # fixed Host/SNI — so a compromised issuer can't rebind DNS to an
        # internal address between validation and connect (security review, DNS
        # rebinding). client_secret_basic is the OAuth2/OIDC default.
        token_url, token_headers, token_ext = _safe_get_url(token_endpoint)
        try:
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.oidc_redirect_uri,
            }
            post_headers = dict(token_headers)
            post_headers["Content-Type"] = "application/x-www-form-urlencoded"
            async with httpx.AsyncClient(
                transport=self._transport, follow_redirects=False
            ) as token_client:
                token_resp = await token_client.post(
                    token_url,
                    data=form,
                    headers=post_headers,
                    extensions=token_ext,
                    auth=(
                        self._settings.oidc_client_id,
                        self._settings.oidc_client_secret,
                    ),
                )
                token_resp.raise_for_status()
                token = token_resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # normalize provider errors
            raise OIDCError(f"token exchange failed: {exc}") from exc

        userinfo_endpoint = disc.get("userinfo_endpoint")
        if userinfo_endpoint:
            if not isinstance(userinfo_endpoint, str):
                raise OIDCError("provider discovery has invalid userinfo_endpoint")
            request_url, headers, extensions = _safe_get_url(userinfo_endpoint)
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=False
                ) as raw:
                    auth_headers = dict(headers)
                    access = token.get("access_token") if isinstance(token, dict) else None
                    if access:
                        auth_headers["Authorization"] = f"Bearer {access}"
                    resp = await raw.get(request_url, headers=auth_headers, extensions=extensions)
                    resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise OIDCError(f"userinfo request failed: {exc}") from exc
            claims: dict[str, Any] = resp.json()
            return claims

        # No userinfo endpoint advertised: fall back to whatever the token
        # response itself carries (some minimal test providers).
        claims_fallback = token.get("userinfo") if isinstance(token, dict) else None
        if isinstance(claims_fallback, dict):
            return claims_fallback
        raise OIDCError("provider has no userinfo_endpoint and returned no claims")
