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

import base64
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from tiqora.config import Settings
from tiqora.security.outbound import OutboundURLError, pin_outbound_url

if TYPE_CHECKING:
    from tiqora.identity.config_models import OidcProviderConfig

_DISCOVERY_TTL = 3600.0
# issuer (+ private-network flag) -> (fetched monotonic, document); shared per process.
_DISCOVERY_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}


def clear_discovery_cache() -> None:
    _DISCOVERY_CACHE.clear()


class OIDCError(Exception):
    """Raised for discovery/token/userinfo failures."""


class OIDCOutboundError(OIDCError):
    """The issuer or one of its endpoints was refused by the SSRF guard."""


@dataclass(frozen=True, slots=True)
class OidcClientConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: str
    redirect_uri: str
    pkce: Literal["auto", "always", "never"] = "auto"
    allow_private_network: bool = False


@dataclass(frozen=True, slots=True)
class OidcLoginResult:
    claims: dict[str, Any]  # userinfo (or token "userinfo" fallback)
    id_token: str | None  # raw, kept only as id_token_hint for logout
    id_token_claims: dict[str, Any] | None = None  # verified payload (JWKS check: Task 21)


def oidc_client_config_from_settings(settings: Settings) -> OidcClientConfig:
    return OidcClientConfig(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        scopes=settings.oidc_scopes,
        redirect_uri=settings.oidc_redirect_uri,
    )


def oidc_client_config(cfg: OidcProviderConfig, *, redirect_uri: str) -> OidcClientConfig:
    return OidcClientConfig(
        issuer=cfg.issuer,
        client_id=cfg.client_id,
        client_secret=cfg.client_secret.get_secret_value() if cfg.client_secret else "",
        scopes=cfg.scopes,
        redirect_uri=redirect_uri,
        pkce=cfg.pkce,
        allow_private_network=cfg.allow_private_network,
    )


def _unverified_sub(id_token: str) -> str | None:
    """``sub`` of an id_token received straight from the token endpoint over TLS.

    Not a signature check (Task 21 adds JWKS verification); only used to refuse a
    userinfo response that belongs to a different subject (OIDC Core 5.3.2).
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        return None
    sub = payload.get("sub") if isinstance(payload, dict) else None
    return sub if isinstance(sub, str) and sub else None


class OIDCService:
    def __init__(
        self, config: OidcClientConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport

    def _safe_get_url(self, url: str) -> tuple[str, dict[str, str], dict[str, str]]:
        """Pin *url* for an outbound request; map SSRF errors to :class:`OIDCOutboundError`."""
        try:
            pinned = pin_outbound_url(
                url, allow_private_networks=self._config.allow_private_network
            )
        except OutboundURLError as exc:
            raise OIDCOutboundError(f"outbound URL rejected: {exc}") from exc
        return pinned.request_url, pinned.request_headers(), pinned.request_extensions()

    async def discover(self) -> dict[str, Any]:
        key = (self._config.issuer, self._config.allow_private_network)
        hit = _DISCOVERY_CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < _DISCOVERY_TTL:
            return hit[1]
        url = self._config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        request_url, headers, extensions = self._safe_get_url(url)
        async with httpx.AsyncClient(transport=self._transport, follow_redirects=False) as client:
            resp = await client.get(request_url, headers=headers, extensions=extensions)
            resp.raise_for_status()
            doc = resp.json()
        if not isinstance(doc, dict):
            raise OIDCError("provider discovery returned no JSON object")
        _DISCOVERY_CACHE[key] = (time.monotonic(), doc)
        return doc

    async def supports_pkce(self) -> bool:
        """Whether the provider advertises PKCE with S256.

        Only sent when advertised: a provider that does not know the parameter
        may reject the whole authorization request, and PKCE is defense in depth
        here (the client is confidential and holds a secret), not the primary
        control.
        """
        try:
            disc = await self.discover()
        except (OIDCError, httpx.HTTPError, ValueError):
            return False
        methods = disc.get("code_challenge_methods_supported")
        return isinstance(methods, list) and "S256" in methods

    async def use_pkce(self) -> bool:
        if self._config.pkce == "always":
            return True
        if self._config.pkce == "never":
            return False
        return await self.supports_pkce()

    async def end_session_endpoint(self) -> str | None:
        value = (await self.discover()).get("end_session_endpoint")
        return value if isinstance(value, str) and value else None

    async def authorize_url(
        self, state: str, *, code_verifier: str | None = None, nonce: str | None = None
    ) -> str:
        """Build the authorization URL, binding *code_verifier* via PKCE S256.

        With PKCE the authorization code is useless to anyone who did not start
        the flow, so an intercepted code (proxy logs, a leaky redirect chain)
        cannot be redeemed (security review L-5).
        """
        disc = await self.discover()
        client = AsyncOAuth2Client(
            client_id=self._config.client_id,
            client_secret=self._config.client_secret,
            scope=self._config.scopes,
            redirect_uri=self._config.redirect_uri,
            code_challenge_method="S256" if code_verifier else None,
            transport=self._transport,
        )
        try:
            extra: dict[str, str] = {"nonce": nonce} if nonce else {}
            url, _ = client.create_authorization_url(
                disc["authorization_endpoint"], state=state, code_verifier=code_verifier, **extra
            )
            return str(url)
        finally:
            await client.aclose()

    async def fetch_claims(self, code: str, *, code_verifier: str | None = None) -> dict[str, Any]:
        """Exchange *code* for tokens and return the userinfo claims dict."""
        return (await self.fetch_tokens_and_claims(code, code_verifier=code_verifier)).claims

    async def fetch_tokens_and_claims(
        self, code: str, *, code_verifier: str | None = None
    ) -> OidcLoginResult:
        disc = await self.discover()
        token_endpoint = disc.get("token_endpoint")
        if not token_endpoint or not isinstance(token_endpoint, str):
            raise OIDCError("provider discovery is missing token_endpoint")

        # Pin the token POST exactly like userinfo — connect by resolved IP with
        # fixed Host/SNI — so a compromised issuer can't rebind DNS to an
        # internal address between validation and connect (security review, DNS
        # rebinding). client_secret_basic is the OAuth2/OIDC default.
        token_url, token_headers, token_ext = self._safe_get_url(token_endpoint)
        try:
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
            }
            if code_verifier:
                form["code_verifier"] = code_verifier
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
                    auth=(self._config.client_id, self._config.client_secret),
                )
                token_resp.raise_for_status()
                token = token_resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # normalize provider errors
            raise OIDCError(f"token exchange failed: {exc}") from exc
        if not isinstance(token, dict):
            raise OIDCError("token endpoint returned no JSON object")
        raw_id_token = token.get("id_token")
        id_token = raw_id_token if isinstance(raw_id_token, str) and raw_id_token else None

        claims = await self._userinfo(disc, token)
        if id_token is not None:
            id_sub = _unverified_sub(id_token)
            user_sub = claims.get("sub")
            if id_sub is not None and user_sub is not None and str(user_sub) != id_sub:
                raise OIDCError("userinfo subject does not match the id_token subject")
        return OidcLoginResult(claims=claims, id_token=id_token)

    async def _userinfo(self, disc: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
        userinfo_endpoint = disc.get("userinfo_endpoint")
        if userinfo_endpoint:
            if not isinstance(userinfo_endpoint, str):
                raise OIDCError("provider discovery has invalid userinfo_endpoint")
            request_url, headers, extensions = self._safe_get_url(userinfo_endpoint)
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, follow_redirects=False
                ) as raw:
                    auth_headers = dict(headers)
                    access = token.get("access_token")
                    if access:
                        auth_headers["Authorization"] = f"Bearer {access}"
                    resp = await raw.get(request_url, headers=auth_headers, extensions=extensions)
                    resp.raise_for_status()
                    claims = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise OIDCError(f"userinfo request failed: {exc}") from exc
            if not isinstance(claims, dict):
                raise OIDCError("userinfo response is not a JSON object")
            return claims

        # No userinfo endpoint advertised: fall back to whatever the token
        # response itself carries (some minimal test providers).
        claims_fallback = token.get("userinfo")
        if isinstance(claims_fallback, dict):
            return claims_fallback
        raise OIDCError("provider has no userinfo_endpoint and returned no claims")
