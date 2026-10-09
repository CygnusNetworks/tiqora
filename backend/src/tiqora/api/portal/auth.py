"""Customer portal auth endpoints: login, me, logout (mirrors api/v1/auth.py)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tiqora.api.deps import DbSession, get_redis
from tiqora.api.portal.deps import AppSettings, CurrentCustomer, get_customer_auth_service
from tiqora.domain.customer_auth import CustomerAuthService, customer_to_dict
from tiqora.domain.schemas import CustomerLoginResponse, CustomerMe, LoginRequest
from tiqora.identity.ldap_login import LdapSignInUnavailable, ldap_sign_in
from tiqora.identity.sso_routes import session_sso_state
from tiqora.security.ratelimit import AuthRateLimiter, client_ip

router = APIRouter(prefix="/auth", tags=["portal-auth"])


def _clear_customer_session_cookie(response: Response, settings: AppSettings) -> None:
    """Delete the portal session cookie with matching set-cookie flags (M-10)."""
    response.delete_cookie(
        key=settings.customer_session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
    )


def _too_many(retry_after: int | None) -> HTTPException:
    headers = {"Retry-After": str(max(1, retry_after))} if retry_after else None
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed login attempts; try again later",
        headers=headers,
    )


@router.post("/login", response_model=CustomerLoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    settings: AppSettings,
    session: DbSession,
) -> CustomerLoginResponse:
    redis_client = await get_redis(request)
    limiter = AuthRateLimiter(redis_client, settings)
    ip = client_ip(request)
    pre = await limiter.check(login=body.login, ip=ip)
    if not pre.allowed:
        headers = {"Retry-After": str(max(1, pre.retry_after))} if pre.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts; try again later",
            headers=headers,
        )

    customer = await auth.authenticate_password(body.login, body.password)
    if customer is None:
        # Then the LDAP directories (Znuny's chained AuthModules), first bind wins.
        try:
            account_login = await ldap_sign_in(
                session, settings, login=body.login, password=body.password, user_kind="customer"
            )
        except LdapSignInUnavailable as exc:
            # Counted like a failure (no free retries), but honest: try again later.
            locked = await limiter.record_failure(login=body.login, ip=ip)
            if locked is not None:
                raise _too_many(locked.retry_after) from exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message
            ) from exc
        if account_login is not None:
            customer = await auth.get_customer_by_login(account_login)
    if customer is None:
        locked = await limiter.record_failure(login=body.login, ip=ip)
        if locked is not None:
            raise _too_many(locked.retry_after)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    await limiter.reset(login=body.login, ip=ip)
    token = await auth.create_session(customer)
    response.set_cookie(
        key=settings.customer_session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return CustomerLoginResponse(customer=CustomerMe(**customer_to_dict(customer)))


@router.get("/me", response_model=CustomerMe)
async def me(
    request: Request, customer: CurrentCustomer, settings: AppSettings, session: DbSession
) -> CustomerMe:
    token = getattr(request.state, "customer_session_token", None)
    sso_logout = False
    if token:
        try:
            redis_client = await get_redis(request)
        except Exception:  # noqa: BLE001 - never fail /me for the logout hint
            redis_client = None
        if redis_client is not None:
            sso_logout = await session_sso_state(
                redis_client, session, settings, token, user_kind="customer"
            )
    return CustomerMe(**customer_to_dict(customer), sso_logout=sso_logout)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    settings: AppSettings,
) -> Response:
    token = getattr(request.state, "customer_session_token", None) or request.cookies.get(
        settings.customer_session_cookie_name
    )
    if token:
        await auth.logout(token)
    _clear_customer_session_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
