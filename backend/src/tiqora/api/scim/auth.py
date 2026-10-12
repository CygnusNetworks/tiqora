"""Bearer-token authentication for ``/scim/v2`` (S3, rulings F1/F10/F16, R-TX).

Order (F1): the token is authenticated first with one indexed select, so a valid token is never
subject to the per-IP lock. Only failed authentications are counted and throttled per IP; a locked
IP answers 429 for bad tokens. Every 401 is byte-identical; the real reason only reaches the audit
row (F10: at most one ``auth_failed`` row per IP and failure window).

After a successful authentication the request session is rolled back (R-TX): the authentication
read would otherwise pin a MariaDB REPEATABLE READ snapshot, and group writes diff against
membership rows that a concurrent push has already committed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, Any, Final

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import get_app_settings, get_db, get_redis, get_session_factory_dep
from tiqora.config import Settings
from tiqora.identity.scim.audit import ScimOperation, record_event
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.tokens import ScimTenant, authenticate_token
from tiqora.security.ratelimit import ScimRateLimiter, client_ip

logger = structlog.get_logger(__name__)

SCIM_PREFIX: Final = "/scim/v2"
_AUTH_DETAIL: Final = "Authentication failed"
_FAIL_LOGGED_PREFIX: Final = "tiqora:scim:fail:logged:"
_RL_LOGGED_PREFIX: Final = "tiqora:scim:rl:logged:"


class ScimHttpError(ScimError):
    """A :class:`ScimError` with response headers (``WWW-Authenticate``, ``Retry-After``)."""

    def __init__(
        self,
        status: int,
        scim_type: Any,
        detail: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status, scim_type, detail)
        self.headers = headers or {}


@dataclass(slots=True)
class ScimContext:
    """Per-request state shared by the dependency, the endpoints and the route wrapper."""

    tenant: ScimTenant
    db: AsyncSession
    factory: async_sessionmaker[AsyncSession]
    started: float
    # Filled by write endpoints; the route wrapper turns it into one audit event.
    operation: ScimOperation | None = None
    scim_id: str | None = None
    user_id: int | None = None
    detail: str | None = None


def resource_of(path: str) -> str:
    """Audit ``resource`` value (<= 8 chars) for a request path."""
    rest = path[len(SCIM_PREFIX) :].lstrip("/") if path.startswith(SCIM_PREFIX) else path
    head = rest.split("/", 1)[0].lower()
    return {"users": "User", "groups": "Group"}.get(head, "Meta")


def unauthorized() -> ScimHttpError:
    return ScimHttpError(401, None, _AUTH_DETAIL, {"WWW-Authenticate": 'Bearer realm="scim"'})


def too_many(retry_after: int) -> ScimHttpError:
    return ScimHttpError(
        429, None, "Too many requests", {"Retry-After": str(max(1, int(retry_after)))}
    )


def bearer_token(request: Request) -> str:
    """The token of ``Authorization: Bearer <token>``, else empty (never query or cookie)."""
    value = request.headers.get("authorization", "")
    scheme, sep, token = value.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    token = token.strip()
    if not token or any(ch.isspace() for ch in token):
        return ""
    return token


async def _once(redis: Redis | None, key: str, ttl: int) -> bool:
    """True the first time ``key`` is seen within ``ttl`` seconds; False when Redis is unusable."""
    if redis is None:
        return False
    try:
        return bool(await redis.set(key, "1", ex=max(1, ttl), nx=True))
    except Exception:  # noqa: BLE001 — auditing noise control must never fail a request
        logger.warning("scim_audit_guard_unavailable")
        return False


async def require_scim_tenant(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)],
) -> ScimTenant:
    started = time.monotonic()
    ip = client_ip(request, settings.trusted_proxies)
    limiter = ScimRateLimiter(redis, settings)
    outcome = await authenticate_token(db, bearer_token(request), settings=settings)

    if outcome.tenant is None:
        await db.rollback()
        locked = await limiter.check_ip(ip)
        if not locked.allowed:
            logger.warning("scim.auth_locked_ip", ip=ip, reason=outcome.reason)
            raise too_many(locked.retry_after or settings.scim_auth_lockout_seconds)
        await limiter.record_auth_failure(ip)
        if await _once(redis, f"{_FAIL_LOGGED_PREFIX}{ip}", settings.scim_auth_fail_window_seconds):
            await record_event(
                factory,
                provider_id=outcome.provider_id,
                token_id=outcome.token_id,
                method=request.method[:8],
                resource=resource_of(request.url.path),
                operation="auth_failed",
                status=401,
                detail=outcome.reason,
            )
        else:
            logger.info("scim.auth_failed", ip=ip, reason=outcome.reason)
        raise unauthorized()

    tenant = outcome.tenant
    decision = await limiter.check_token(tenant.token_id)
    if not decision.allowed:
        await db.rollback()
        if await _once(
            redis, f"{_RL_LOGGED_PREFIX}{tenant.token_id}", settings.scim_rate_limit_window_seconds
        ):
            await record_event(
                factory,
                provider_id=tenant.provider.id,
                token_id=tenant.token_id,
                method=request.method[:8],
                resource=resource_of(request.url.path),
                operation="rate_limited",
                status=429,
                user_kind=tenant.user_kind,
            )
        raise too_many(decision.retry_after or settings.scim_rate_limit_window_seconds)

    # R-TX: end the authentication read's transaction before any SCIM service reads or writes.
    await db.rollback()
    request.state.scim = ScimContext(tenant=tenant, db=db, factory=factory, started=started)
    return tenant
