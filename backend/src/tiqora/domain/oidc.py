"""OIDC/SSO login: authorization-code exchange + claim-based user mapping.

v1 deliberately does **not** auto-provision users: the mapped claim (default
``preferred_username``) must match an existing, ``valid_id=1`` row in
``users.login`` or the login is rejected. Provisioning/JIT user creation is
left for a future phase once role/group mapping policy is defined.

Uses authlib's :class:`~authlib.integrations.httpx_client.AsyncOAuth2Client`
for the token exchange so a fake provider can be substituted in tests via
``transport=httpx.MockTransport(...)``.

Discovery, token, userinfo and JWKS URLs are validated (and fetches IP-pinned)
via :mod:`tiqora.security.outbound` so a malicious or compromised issuer cannot
target internal addresses.

An id_token returned by the token endpoint is verified against the provider's JWKS
(signature with an asymmetric algorithm from :data:`JWT_ALGORITHMS`, ``iss``, ``aud``/
``azp``, ``exp``, ``iat``, ``nonce``); the JWKS is cached per issuer with a TTL and
refetched at most once per :data:`_JWKS_REFRESH_MIN_INTERVAL` when a token names an
unknown key id. The same verifier serves back-channel logout tokens
(:mod:`tiqora.identity.oidc_backchannel`).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import secrets
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
import structlog
from authlib.integrations.httpx_client import AsyncOAuth2Client

with warnings.catch_warnings():
    # authlib.jose is deprecated in favour of joserfc (authlib 2.0); still supported in 1.x.
    warnings.simplefilter("ignore", DeprecationWarning)
    from authlib.jose import JsonWebKey, JsonWebToken

from tiqora.config import Settings
from tiqora.security.outbound import OutboundURLError, pin_outbound_url

if TYPE_CHECKING:
    from tiqora.identity.config_models import OidcProviderConfig

logger = structlog.get_logger(__name__)

_DISCOVERY_TTL = 3600.0
# Every IdP call is bounded in time and size (a slow or hostile IdP must not pin a worker
# or exhaust memory): connect/read/write/pool 10 s, bodies read streamed up to the cap.
_HTTP_TIMEOUT = httpx.Timeout(10.0)
# Wall clock for one IdP call (connect + streamed body), on top of the per-phase timeout.
_TOTAL_REQUEST_TIMEOUT = 15.0
_MAX_DOCUMENT_BYTES = 256 * 1024
# Microsoft's multi-tenant discovery ("common"/"organizations") publishes this placeholder.
TENANT_PLACEHOLDER = "{tenantid}"
_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# issuer (+ private-network flag) -> (fetched monotonic, document); shared per process.
_DISCOVERY_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}

# Asymmetric algorithms only: "none" and HS* (an HMAC "keyed" with a public key) never verify.
JWT_ALGORITHMS: tuple[str, ...] = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
)
_ALG_KTY: dict[str, str] = {"RS": "RSA", "PS": "RSA", "ES": "EC"}
JWT_LEEWAY_SECONDS = 120
MAX_JWT_BYTES = 16 * 1024
_JWKS_TTL = 600.0
# A token naming an unknown key id may refetch the JWKS (key rotation), but at most this
# often per issuer: a flood of tokens with invented key ids must not hammer the IdP.
_JWKS_REFRESH_MIN_INTERVAL = 60.0
_JWKS_TIMEOUT = httpx.Timeout(5.0)
_MAX_JWKS_BYTES = 256 * 1024
# (issuer, private-network flag) -> (fetched monotonic, JWKS document)
_JWKS_CACHE: dict[tuple[str, bool], tuple[float, dict[str, Any]]] = {}
# (issuer, private-network flag) -> monotonic time of the last forced refetch
_JWKS_FORCED: dict[tuple[str, bool], float] = {}


def clear_discovery_cache() -> None:
    _DISCOVERY_CACHE.clear()
    _JWKS_CACHE.clear()
    _JWKS_FORCED.clear()


class OIDCError(Exception):
    """Raised for discovery/token/userinfo failures."""


class OIDCOutboundError(OIDCError):
    """The issuer or one of its endpoints was refused by the SSRF guard."""


class OIDCTokenInvalid(OIDCError):  # noqa: N818 - domain term
    """A JWT (id_token, logout token) failed verification; ``reason`` is for logs only."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"token invalid: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OidcClientConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: str
    redirect_uri: str
    pkce: Literal["auto", "always", "never"] = "auto"
    allow_private_network: bool = False
    allowed_tenant_ids: tuple[str, ...] = ()  # Entra multi-tenant issuer: empty = any tenant


@dataclass(frozen=True, slots=True)
class OidcLoginResult:
    claims: dict[str, Any]  # userinfo (or token "userinfo" fallback)
    id_token: str | None = field(repr=False)  # raw, kept only as id_token_hint for logout
    id_token_claims: dict[str, Any] | None = None  # verified via JWKS; sid comes from here


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
        allowed_tenant_ids=tuple(cfg.allowed_tenant_ids),
    )


def _jwt_header(token: str) -> dict[str, Any]:
    """Unverified JOSE header (only used to pick the key; the signature is checked after)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise OIDCTokenInvalid("format")
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise OIDCTokenInvalid("header") from exc
    if not isinstance(header, dict):
        raise OIDCTokenInvalid("header")
    return header


def _select_jwk(jwks: dict[str, Any], kid: str | None, alg: str) -> dict[str, Any] | None:
    """The signing key for *kid*/*alg*; without a kid only an unambiguous single candidate."""
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return None
    kty = _ALG_KTY[alg[:2]]
    candidates = [
        k
        for k in keys
        if isinstance(k, dict)
        and k.get("kty") == kty
        and k.get("use", "sig") == "sig"
        and k.get("alg", alg) == alg
    ]
    if kid is not None:
        matches = [k for k in candidates if k.get("kid") == kid]
        return matches[0] if len(matches) == 1 else None
    return candidates[0] if len(candidates) == 1 else None


def _num(claims: dict[str, Any], name: str) -> float | None:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _shown(value: Any) -> str:
    """A claim value for a diagnostic log line (never a whole token), shortened."""
    return repr(value)[:200]


def check_audience(claims: Mapping[str, Any], client_id: str) -> None:
    """``aud`` holds *client_id*; with several audiences ``azp`` must name it (Core 3.1.3.7)."""
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else aud
    if (
        not isinstance(audiences, list)
        or not all(isinstance(a, str) for a in audiences)
        or client_id not in audiences
    ):
        logger.warning("oidc_token_aud_mismatch", expected=client_id, received=_shown(aud))
        raise OIDCTokenInvalid("aud")
    azp = claims.get("azp")
    if (azp is not None and azp != client_id) or (len(audiences) > 1 and azp is None):
        logger.warning("oidc_token_azp_mismatch", expected=client_id, received=_shown(azp))
        raise OIDCTokenInvalid("azp")


def resolve_issuer(
    template: str, claims: Mapping[str, Any], allowed_tenant_ids: Sequence[str] = ()
) -> str:
    """The issuer a token must carry.

    Microsoft's multi-tenant discovery issuer contains ``{tenantid}``: the token's ``tid``
    (required, GUID form) is substituted before the exact comparison, and a non-empty
    *allowed_tenant_ids* limits which tenants are accepted. Any other issuer is returned as is.
    """
    if TENANT_PLACEHOLDER not in template:
        return template
    tid = claims.get("tid")
    if not isinstance(tid, str) or not _GUID_RE.fullmatch(tid.lower()):
        logger.warning("oidc_token_tid_invalid", received=_shown(tid))
        raise OIDCTokenInvalid("tid")
    tid = tid.lower()
    if allowed_tenant_ids and tid not in {t.lower() for t in allowed_tenant_ids}:
        logger.warning("oidc_token_tenant_not_allowed", received=tid)
        raise OIDCTokenInvalid("tenant")
    return template.replace(TENANT_PLACEHOLDER, tid)


def check_issuer(
    claims: Mapping[str, Any], template: str, allowed_tenant_ids: Sequence[str] = ()
) -> None:
    expected = resolve_issuer(template, claims, allowed_tenant_ids)
    if claims.get("iss") != expected:
        logger.warning(
            "oidc_token_iss_mismatch", expected=expected, received=_shown(claims.get("iss"))
        )
        raise OIDCTokenInvalid("iss")


async def _read_capped(resp: httpx.Response, max_bytes: int) -> bytes:
    """Body of a streamed response, refusing anything over *max_bytes*."""
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise OIDCError("response too large")
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body += chunk
        if len(body) > max_bytes:
            raise OIDCError("response too large")
    return bytes(body)


async def _json_request(
    transport: httpx.AsyncBaseTransport | None,
    method: str,
    url: str,
    *,
    http_timeout: httpx.Timeout = _HTTP_TIMEOUT,
    max_bytes: int = _MAX_DOCUMENT_BYTES,
    **kwargs: Any,
) -> Any:
    """One bounded IdP call (no redirects, timeout, streamed size cap); the decoded JSON.

    Raises :class:`OIDCError` for an error status, an oversized body or invalid JSON, and
    lets ``httpx.HTTPError`` (connect/timeout) through. The whole call (connect + body)
    has a wall-clock deadline of ``_TOTAL_REQUEST_TIMEOUT``: httpx's timeout is per phase,
    so a slow-drip body could otherwise hold the request for minutes. ``Accept-Encoding:
    identity`` keeps the size cap honest (no decompression of a small compressed body).
    """
    headers = {**dict(kwargs.pop("headers", None) or {}), "Accept-Encoding": "identity"}
    try:
        async with (
            asyncio.timeout(_TOTAL_REQUEST_TIMEOUT),
            httpx.AsyncClient(
                transport=transport, follow_redirects=False, timeout=http_timeout
            ) as client,
            client.stream(method, url, headers=headers, **kwargs) as resp,
        ):
            if resp.status_code >= 400:
                raise OIDCError(f"HTTP {resp.status_code} from {method} {url}")
            body = await _read_capped(resp, max_bytes)
    except TimeoutError as exc:
        raise httpx.TimeoutException(
            f"no complete answer within {_TOTAL_REQUEST_TIMEOUT:.0f} s"
        ) from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise OIDCError("response is not JSON") from exc


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
        doc = await _json_request(
            self._transport, "GET", request_url, headers=headers, extensions=extensions
        )
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

    def _cache_key(self) -> tuple[str, bool]:
        return (self._config.issuer, self._config.allow_private_network)

    async def expected_issuer(self) -> str:
        """``iss`` every token must carry: the discovery ``issuer`` (configured issuer fallback)."""
        value = (await self.discover()).get("issuer")
        return value if isinstance(value, str) and value else self._config.issuer

    async def jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        """The provider's JWKS, cached per issuer for ``_JWKS_TTL`` seconds.

        ``refresh=True`` (a token named an unknown key id) refetches, but at most once per
        ``_JWKS_REFRESH_MIN_INTERVAL`` per issuer; otherwise the cached set is returned.
        """
        key = self._cache_key()
        now = time.monotonic()
        hit = _JWKS_CACHE.get(key)
        if refresh and hit is not None:
            last = _JWKS_FORCED.get(key)
            if last is not None and now - last < _JWKS_REFRESH_MIN_INTERVAL:
                return hit[1]
            _JWKS_FORCED[key] = now
        elif hit is not None and now - hit[0] < _JWKS_TTL:
            return hit[1]
        uri = (await self.discover()).get("jwks_uri")
        if not isinstance(uri, str) or not uri:
            raise OIDCError("provider discovery is missing jwks_uri")
        request_url, headers, extensions = self._safe_get_url(uri)
        try:
            doc = await _json_request(
                self._transport,
                "GET",
                request_url,
                http_timeout=_JWKS_TIMEOUT,
                max_bytes=_MAX_JWKS_BYTES,
                headers=headers,
                extensions=extensions,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(f"JWKS fetch failed: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
            raise OIDCError("JWKS document has no keys")
        _JWKS_CACHE[key] = (time.monotonic(), doc)
        return doc

    @property
    def allowed_tenant_ids(self) -> tuple[str, ...]:
        return self._config.allowed_tenant_ids

    async def verify_jwt(self, token: str) -> dict[str, Any]:
        """Signature check of a compact JWS against the JWKS; returns the (unchecked) claims."""
        return (await self.decode_jwt(token))[1]

    async def decode_jwt(self, token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Signature check of a compact JWS against the JWKS; ``(header, claims)`` unchecked.

        Only :data:`JWT_ALGORITHMS`; the key is chosen by ``kid`` (and key type), an unknown
        ``kid`` triggers one rate-limited JWKS refetch. Claim checks are the caller's job.
        """
        if not isinstance(token, str) or not token or len(token) > MAX_JWT_BYTES:
            raise OIDCTokenInvalid("size")
        header = _jwt_header(token)
        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in JWT_ALGORITHMS:
            logger.warning(
                "oidc_token_alg_rejected", expected=list(JWT_ALGORITHMS), received=_shown(alg)
            )
            raise OIDCTokenInvalid("alg")
        kid = header.get("kid")
        if kid is not None and not isinstance(kid, str):
            raise OIDCTokenInvalid("kid")
        jwk = _select_jwk(await self.jwks(), kid, alg)
        if jwk is None:
            jwk = _select_jwk(await self.jwks(refresh=True), kid, alg)
        if jwk is None:
            logger.warning("oidc_token_unknown_kid", received=_shown(kid), alg=alg)
            raise OIDCTokenInvalid("unknown_kid")
        try:
            public = {k: v for k, v in jwk.items() if k not in ("d", "p", "q", "dp", "dq", "qi")}
            claims = JsonWebToken(list(JWT_ALGORITHMS)).decode(token, JsonWebKey.import_key(public))
        except Exception as exc:  # noqa: BLE001 - bad signature, malformed key or payload
            raise OIDCTokenInvalid(f"signature:{type(exc).__name__}") from exc
        return header, dict(claims)

    async def verify_id_token(self, id_token: str, *, nonce: str | None) -> dict[str, Any]:
        """Verified id_token claims (OIDC Core 3.1.3.7): signature, iss, aud/azp, exp, iat,
        ``sub`` and ``nonce`` (when the flow stored one; pre-upgrade env states have none)."""
        claims = await self.verify_jwt(id_token)
        check_issuer(claims, await self.expected_issuer(), self._config.allowed_tenant_ids)
        check_audience(claims, self._config.client_id)
        now = time.time()
        exp, iat, nbf = _num(claims, "exp"), _num(claims, "iat"), _num(claims, "nbf")
        if exp is None or now > exp + JWT_LEEWAY_SECONDS:
            raise OIDCTokenInvalid("exp")
        if iat is None or iat > now + JWT_LEEWAY_SECONDS:
            raise OIDCTokenInvalid("iat")
        if nbf is not None and nbf > now + JWT_LEEWAY_SECONDS:
            raise OIDCTokenInvalid("nbf")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise OIDCTokenInvalid("sub")
        if nonce is not None:
            got = claims.get("nonce")
            if not isinstance(got, str) or not secrets.compare_digest(
                got.encode("utf-8"), nonce.encode("utf-8")
            ):
                raise OIDCTokenInvalid("nonce")
        return claims

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

    async def fetch_claims(
        self, code: str, *, code_verifier: str | None = None, nonce: str | None = None
    ) -> dict[str, Any]:
        """Exchange *code* for tokens and return the userinfo claims dict."""
        return (
            await self.fetch_tokens_and_claims(code, code_verifier=code_verifier, nonce=nonce)
        ).claims

    async def fetch_tokens_and_claims(
        self, code: str, *, code_verifier: str | None = None, nonce: str | None = None
    ) -> OidcLoginResult:
        """Exchange *code*; verify the id_token (when one is returned) and fetch userinfo.

        A provider that returns no id_token keeps working as before (userinfo only). When an
        id_token is present it must verify (:meth:`verify_id_token`) and userinfo ``sub``
        must equal its ``sub`` (OIDC Core 5.3.2), else :class:`OIDCError`.
        """
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
            token = await _json_request(
                self._transport,
                "POST",
                token_url,
                data=form,
                headers=post_headers,
                extensions=token_ext,
                auth=(self._config.client_id, self._config.client_secret),
            )
        except (httpx.HTTPError, ValueError) as exc:  # normalize provider errors
            raise OIDCError(f"token exchange failed: {exc}") from exc
        if not isinstance(token, dict):
            raise OIDCError("token endpoint returned no JSON object")
        raw_id_token = token.get("id_token")
        id_token = raw_id_token if isinstance(raw_id_token, str) and raw_id_token else None

        id_claims = (
            await self.verify_id_token(id_token, nonce=nonce) if id_token is not None else None
        )
        claims = await self._userinfo(disc, token)
        if id_claims is not None:
            # Core 5.3.2: userinfo MUST carry sub and it must be the id_token's.
            user_sub = claims.get("sub")
            if user_sub is None or str(user_sub) != id_claims["sub"]:
                raise OIDCError("userinfo subject missing or not the id_token subject")
        return OidcLoginResult(claims=claims, id_token=id_token, id_token_claims=id_claims)

    async def _userinfo(self, disc: dict[str, Any], token: dict[str, Any]) -> dict[str, Any]:
        userinfo_endpoint = disc.get("userinfo_endpoint")
        if userinfo_endpoint:
            if not isinstance(userinfo_endpoint, str):
                raise OIDCError("provider discovery has invalid userinfo_endpoint")
            request_url, headers, extensions = self._safe_get_url(userinfo_endpoint)
            auth_headers = dict(headers)
            access = token.get("access_token")
            if access:
                auth_headers["Authorization"] = f"Bearer {access}"
            try:
                claims = await _json_request(
                    self._transport,
                    "GET",
                    request_url,
                    headers=auth_headers,
                    extensions=extensions,
                )
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
