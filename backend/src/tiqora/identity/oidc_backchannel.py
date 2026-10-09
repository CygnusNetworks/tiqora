"""OIDC Back-Channel Logout 1.0: logout-token verification and the per-IP request limit.

The identity provider POSTs a signed logout token (server to server, no cookies) to
``/api/v1/auth/oidc/{slug}/backchannel-logout``. :func:`verify_logout_token` applies the
validation steps of section 2.6: signature against the provider's JWKS with an asymmetric
algorithm (never ``none``/HS*), ``iss``, ``aud``, a fresh ``iat``, the back-channel ``events``
member, no ``nonce`` (so an id_token can never pass as a logout token), ``jti`` and ``sub`` or
``sid``. The ``jti`` replay cache lives in :mod:`tiqora.identity.flow_state`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

import structlog

from tiqora.domain.oidc import (
    JWT_LEEWAY_SECONDS,
    OIDCError,
    OIDCService,
    OIDCTokenInvalid,
    check_audience,
    check_issuer,
)

logger = structlog.get_logger(__name__)

BACKCHANNEL_EVENT: Final = "http://schemas.openid.net/event/backchannel-logout"
MAX_JTI_LENGTH: Final = 256
MAX_SELECTOR_LENGTH: Final = 1024

# Accepted JOSE "typ" values (case-insensitive, optional "application/" prefix, RFC 7515
# 4.1.9); a missing typ is accepted (spec: explicit typing RECOMMENDED, not required).
ACCEPTED_TYP: Final = frozenset({"logout+jwt", "jwt"})

# Fixed window per client IP, counted BEFORE any provider lookup: an IdP ending many sessions
# at once stays far below this; a client spraying tokens or slugs is cut off.
RATE_LIMIT_MAX: int = 300
RATE_LIMIT_WINDOW_SECONDS: int = 60
_RATE_PREFIX: Final = "tiqora:rl:oidc-bcl:"


class LogoutTokenInvalid(ValueError):  # noqa: N818 - name fixed by the plan
    """The logout token failed a validation step; ``reason`` is logged, never returned."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LogoutToken:
    sub: str | None
    sid: str | None
    jti: str


def _opt_str(claims: dict[str, Any], name: str) -> str | None:
    value = claims.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_SELECTOR_LENGTH:
        raise LogoutTokenInvalid(name)
    return value


async def verify_logout_token(
    token: str,
    *,
    service: OIDCService,
    client_id: str,
    issuer: str,
    max_age_seconds: int = 300,
) -> LogoutToken:
    """Verify a back-channel logout token (section 2.6); :class:`LogoutTokenInvalid` otherwise.

    *issuer* may be Microsoft's multi-tenant template (``{tenantid}``): the token's ``tid``
    is substituted, limited to the service's ``allowed_tenant_ids`` when set. ``exp`` is
    optional (the ``iat`` window bounds the token's life).
    """
    try:
        header, claims = await service.decode_jwt(token)
    except OIDCTokenInvalid as exc:
        raise LogoutTokenInvalid(exc.reason) from exc
    except OIDCError as exc:  # JWKS/discovery unreachable: the token cannot be validated
        raise LogoutTokenInvalid("jwks_unavailable") from exc
    typ = header.get("typ")
    if typ is not None and (
        not isinstance(typ, str) or typ.lower().removeprefix("application/") not in ACCEPTED_TYP
    ):
        logger.warning("oidc_logout_token_typ_rejected", received=repr(typ)[:100])
        raise LogoutTokenInvalid("typ")
    try:
        check_issuer(claims, issuer, service.allowed_tenant_ids)
        check_audience(claims, client_id)
    except OIDCTokenInvalid as exc:
        raise LogoutTokenInvalid(exc.reason) from exc
    now = time.time()
    iat = claims.get("iat")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)):
        raise LogoutTokenInvalid("iat")
    if iat > now + JWT_LEEWAY_SECONDS or now - iat > max_age_seconds:
        raise LogoutTokenInvalid("iat")
    exp = claims.get("exp")
    if exp is not None and (
        isinstance(exp, bool) or not isinstance(exp, (int, float)) or now > exp + JWT_LEEWAY_SECONDS
    ):
        raise LogoutTokenInvalid("exp")
    events = claims.get("events")
    if not isinstance(events, dict) or not isinstance(events.get(BACKCHANNEL_EVENT), dict):
        raise LogoutTokenInvalid("events")
    if "nonce" in claims:
        raise LogoutTokenInvalid("nonce")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > MAX_JTI_LENGTH:
        raise LogoutTokenInvalid("jti")
    sub = _opt_str(claims, "sub")
    sid = _opt_str(claims, "sid")
    if sub is None and sid is None:
        raise LogoutTokenInvalid("no_selector")
    return LogoutToken(sub=sub, sid=sid, jti=jti)


async def rate_limited(redis: Any, ip: str) -> int:
    """Count this request of client *ip*; return Retry-After seconds when over the limit, else 0.

    The window key is created with its TTL atomically (SET NX EX) before INCR, so a crash
    between the two can never leave a counter without expiry (a permanent lockout); a key
    that expired between the two calls is given its TTL back. Fails open on Redis trouble
    (a Redis outage must not stop logouts).
    """
    key = f"{_RATE_PREFIX}{ip or 'unknown'}"
    try:
        await redis.set(key, "0", ex=RATE_LIMIT_WINDOW_SECONDS, nx=True)
        count = int(await redis.incr(key))
        # The key can expire between SET NX and INCR; INCR then re-creates it without a TTL.
        if int(await redis.ttl(key)) < 0:
            await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        if count > RATE_LIMIT_MAX:
            ttl = int(await redis.ttl(key) or RATE_LIMIT_WINDOW_SECONDS)
            return max(1, ttl)
    except Exception:  # noqa: BLE001 - fail open
        logger.debug("oidc_backchannel_rate_limit_unavailable")
    return 0
