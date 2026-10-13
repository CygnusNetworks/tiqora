"""Customer portal auth endpoints: login, me, logout (mirrors api/v1/auth.py)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tiqora.api.deps import DbSession, get_redis
from tiqora.api.portal.deps import (
    AppSettings,
    CurrentCustomer,
    CustomerTOTPServiceDep,
    CustomerWebAuthnServiceDep,
    get_customer_auth_service,
)
from tiqora.domain.customer_auth import CustomerAuthService, customer_to_dict
from tiqora.domain.schemas import CustomerLoginResponse, CustomerMe, LoginRequest, PendingLoginOut
from tiqora.identity.ldap_login import LdapSignInUnavailable, ldap_sign_in
from tiqora.identity.sso_managed import SsoManagedGate
from tiqora.identity.sso_routes import session_sso_state
from tiqora.plugins.auth_providers import plugin_password_sign_in
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


def set_customer_session_cookie(
    response: Response, settings: AppSettings, token: str, *, max_age: int | None = None
) -> None:
    response.set_cookie(
        key=settings.customer_session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        max_age=max_age if max_age is not None else settings.session_ttl_seconds,
        path="/",
    )


def _too_many(retry_after: int | None) -> HTTPException:
    headers = {"Retry-After": str(max(1, retry_after))} if retry_after else None
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed login attempts; try again later",
        headers=headers,
    )


@router.get("/pending", response_model=PendingLoginOut)
async def pending_login(
    request: Request,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    totp: CustomerTOTPServiceDep,
    webauthn: CustomerWebAuthnServiceDep,
    settings: AppSettings,
) -> PendingLoginOut:
    """State of the login behind the portal cookie (read-only; never touches TTLs)."""
    token = request.cookies.get(settings.customer_session_cookie_name)
    pending = await auth.get_pending_session(token) if token else None
    if pending is None:
        return PendingLoginOut(state="none")
    return PendingLoginOut(
        state="pending_2fa",
        totp_enrolled=await totp.is_enabled(pending[0]),
        passkey_enrolled=await webauthn.has_passkey(pending[0]),
    )


@router.post("/login", response_model=CustomerLoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    totp: CustomerTOTPServiceDep,
    webauthn: CustomerWebAuthnServiceDep,
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

    # P2A-PM-I2: an SSO-managed account signs in through its IdP; every link refuses it and a
    # refusal ends the chain (answered like a wrong password).
    gate = SsoManagedGate(session, "customer", body.login)
    customer = await auth.authenticate_password(body.login, body.password, refuse=gate.local)
    if customer is None and not gate.refused:
        # Then the LDAP directories (Znuny's chained AuthModules), first bind wins.
        try:
            account_login = await ldap_sign_in(
                session,
                settings,
                login=body.login,
                password=body.password,
                user_kind="customer",
                refuse=gate.ldap,
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
            # gate.ldap already ran before apply_login wrote anything; this backstop covers
            # an account apply_login only found on its race re-plan.
            if customer is not None and await gate.refuses(customer.id, path="ldap"):
                customer = None
    if customer is None and not gate.refused:
        # Last link of the chain: password auth providers from plugins (named accounts only).
        found = await plugin_password_sign_in(
            session, login=body.login, password=body.password, user_kind="customer", client_ip=ip
        )
        if found is not None:
            customer = await auth.get_customer_by_login(found[0])
    if customer is None:
        locked = await limiter.record_failure(login=body.login, ip=ip)
        if locked is not None:
            raise _too_many(locked.retry_after)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    await limiter.reset(login=body.login, ip=ip)
    # Customer 2FA is opt-in: only an enrolled factor changes the flow. The
    # lookup runs after the password check, so the response never reveals
    # whether an unknown login "has" a second factor.
    totp_enrolled = await totp.is_enabled(customer.id)
    passkey_enrolled = await webauthn.has_passkey(customer.id)
    if totp_enrolled or passkey_enrolled:
        pending_token = await auth.create_pending_session(customer)
        set_customer_session_cookie(
            response, settings, pending_token, max_age=settings.totp_pending_ttl_seconds
        )
        return CustomerLoginResponse(
            customer=None,
            pending_2fa=True,
            totp_enrolled=totp_enrolled,
            passkey_enrolled=passkey_enrolled,
        )
    token = await auth.create_session(customer)
    set_customer_session_cookie(response, settings, token)
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
