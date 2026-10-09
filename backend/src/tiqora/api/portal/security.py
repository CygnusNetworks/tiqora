"""Customer portal second factors: TOTP and passkeys (mirrors api/v1/auth.py).

Customer 2FA is opt-in; SSO logins skip it (the identity provider is the strong
factor), password and LDAP logins with an enrolled factor get a pending session.
WebAuthn switched off later: the login step still counts enrolled passkeys (as on
the agent side), and ``/passkey/*`` answers 404 while it is off, so a passkey-only
customer is stranded until WebAuthn is back on or an admin resets the factors
(``POST /api/v1/admin/customer-users/{id}/reset-2fa``).
A pending token is accepted ONLY by ``/totp/verify`` and
``/passkey/authenticate/*`` (via :class:`PendingCustomer`); every other route
resolves sessions through ``get_current_customer``, which rejects it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tiqora.api.deps import get_redis
from tiqora.api.portal.auth import set_customer_session_cookie
from tiqora.api.portal.deps import (
    AppSettings,
    CurrentCustomer,
    CustomerTOTPServiceDep,
    CustomerWebAuthnServiceDep,
    PendingCustomer,
    get_customer_auth_service,
    get_customer_session_store,
)
from tiqora.domain.customer_auth import CustomerAuthService, CustomerSessionStore, customer_to_dict
from tiqora.domain.passkey import two_factor_enabled, webauthn_enabled
from tiqora.domain.schemas import (
    CustomerLoginResponse,
    CustomerMe,
    PasskeyAuthenticateFinishIn,
    PasskeyOut,
    PasskeyRegisterFinishIn,
    PasskeyStatusOut,
    TOTPCodeIn,
    TOTPEnrollIn,
    TOTPEnrollOut,
    TOTPStatusOut,
)
from tiqora.domain.totp import TOTPStepUpRequired
from tiqora.domain.totp_qr import totp_qr_svg
from tiqora.security.ratelimit import AuthRateLimiter, RateLimitDecision, client_ip

router = APIRouter(prefix="/auth", tags=["portal-auth"])


def _rate_limit_error(decision: RateLimitDecision) -> HTTPException:
    retry_after = int(decision.retry_after or 0)
    headers = {"Retry-After": str(max(1, retry_after))} if retry_after else None
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed login attempts; try again later",
        headers=headers,
    )


async def _throttled[T](
    request: Request,
    settings: AppSettings,
    login: str,
    attempt: Callable[[], Awaitable[T | None]],
    *,
    on_fail: HTTPException,
) -> T:
    """Run one second-factor guess under the customer 2FA throttle.

    check -> attempt -> (falsy: record failure, 429 if that locks, else
    *on_fail*) -> reset on success. The key is ``2fa:customer:<login>``,
    separate from the agent counter of the same login.
    """
    limiter = AuthRateLimiter(await get_redis(request), settings)
    ip = client_ip(request)
    key = _rl_key(login)
    pre = await limiter.check(login=key, ip=ip)
    if not pre.allowed:
        raise _rate_limit_error(pre)
    result = await attempt()
    if not result:
        locked = await limiter.record_failure(login=key, ip=ip)
        if locked is not None:
            raise _rate_limit_error(locked)
        raise on_fail
    await limiter.reset(login=key, ip=ip)
    return result


def _rl_key(login: str) -> str:
    # Own namespace: a lockout of customer "x" must not lock agent "x" (and back).
    return f"2fa:customer:{login}"


def _require_webauthn(settings: AppSettings) -> None:
    if not webauthn_enabled(settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WebAuthn is not enabled")


async def _require_recent_auth(
    request: Request,
    settings: AppSettings,
    sessions: CustomerSessionStore,
    customer_id: int,
    totp: CustomerTOTPServiceDep,
    webauthn: CustomerWebAuthnServiceDep,
) -> None:
    """Gate passkey changes on a recently authenticated session (agent contract).

    Skipped while the account has no second factor yet: first enrollment must
    stay frictionless and there is nothing to protect. Unknown age counts as
    stale.
    """
    if not await two_factor_enabled(totp, webauthn, customer_id):
        return
    token = getattr(request.state, "customer_session_token", None)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")
    age = await sessions.seconds_since_auth(str(token))
    if age is None or age > settings.step_up_max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="reauthentication_required"
        )


def _session_token(request: Request) -> str:
    token = getattr(request.state, "customer_session_token", None)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")
    return str(token)


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


@router.post("/totp/verify", response_model=CustomerLoginResponse)
async def totp_verify(
    body: TOTPCodeIn,
    request: Request,
    response: Response,
    pending: PendingCustomer,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    totp: CustomerTOTPServiceDep,
    settings: AppSettings,
) -> CustomerLoginResponse:
    """Promote a pending-2FA session to a full session after a valid TOTP code."""
    await _throttled(
        request,
        settings,
        pending.login,
        lambda: totp.verify(pending.customer_id, body.code),
        on_fail=HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code"),
    )
    return await _promote(response, settings, auth, pending.token)


async def _promote(
    response: Response, settings: AppSettings, auth: CustomerAuthService, token: str
) -> CustomerLoginResponse:
    promoted = await auth.promote_pending_session(token)
    if promoted is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    new_token, customer = promoted
    set_customer_session_cookie(response, settings, new_token)
    return CustomerLoginResponse(customer=CustomerMe(**customer_to_dict(customer)))


@router.post("/totp/enroll", response_model=TOTPEnrollOut)
async def totp_enroll(
    customer: CurrentCustomer,
    totp: CustomerTOTPServiceDep,
    webauthn: CustomerWebAuthnServiceDep,
    sessions: Annotated[CustomerSessionStore, Depends(get_customer_session_store)],
    request: Request,
    settings: AppSettings,
    body: TOTPEnrollIn | None = None,
) -> TOTPEnrollOut:
    """Start (or replace) a TOTP enrollment; replacing a live factor needs its code.

    Adding TOTP to a passkey-only account needs a recent sign-in (the passkey
    gate), or a stolen session could attach an authenticator it controls.
    """
    if await totp.is_enabled(customer.id):
        current = body.current_code if body else None

        async def replace() -> TOTPEnrollOut | None:
            try:
                secret, uri = await totp.enroll(customer.id, customer.login, current_code=current)
            except TOTPStepUpRequired:
                return None
            return TOTPEnrollOut(secret=secret, otpauth_uri=uri)

        return await _throttled(
            request,
            settings,
            customer.login,
            replace,
            on_fail=HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A valid code from the current authenticator is required to re-enroll",
            ),
        )
    if await webauthn.has_passkey(customer.id):
        await _require_recent_auth(request, settings, sessions, customer.id, totp, webauthn)
    secret, uri = await totp.enroll(customer.id, customer.login)
    return TOTPEnrollOut(secret=secret, otpauth_uri=uri)


@router.get("/totp/enroll/qr")
async def totp_enroll_qr(customer: CurrentCustomer, totp: CustomerTOTPServiceDep) -> Response:
    uri = await totp.get_pending_provisioning_uri(customer.id, customer.login)
    if uri is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending TOTP enrollment"
        )
    return Response(content=totp_qr_svg(uri), media_type="image/svg+xml")


@router.post("/totp/confirm", response_model=TOTPStatusOut)
async def totp_confirm(
    body: TOTPCodeIn,
    request: Request,
    customer: CurrentCustomer,
    totp: CustomerTOTPServiceDep,
    settings: AppSettings,
) -> TOTPStatusOut:
    # A wrong code is a 2FA guess like any other: same throttle as verify.
    await _throttled(
        request,
        settings,
        customer.login,
        lambda: totp.confirm(customer.id, body.code),
        on_fail=HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code"),
    )
    return TOTPStatusOut(enabled=True)


@router.delete("/totp", response_model=TOTPStatusOut)
async def totp_disable(
    body: TOTPCodeIn,
    request: Request,
    customer: CurrentCustomer,
    totp: CustomerTOTPServiceDep,
    settings: AppSettings,
) -> TOTPStatusOut:
    await _throttled(
        request,
        settings,
        customer.login,
        lambda: totp.disable(customer.id, body.code),
        on_fail=HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code"),
    )
    return TOTPStatusOut(enabled=False)


@router.get("/totp/status", response_model=TOTPStatusOut)
async def totp_status(customer: CurrentCustomer, totp: CustomerTOTPServiceDep) -> TOTPStatusOut:
    return TOTPStatusOut(enabled=await totp.is_enabled(customer.id))


# ---------------------------------------------------------------------------
# Passkeys
# ---------------------------------------------------------------------------


@router.post("/passkey/register/begin")
async def passkey_register_begin(
    request: Request,
    customer: CurrentCustomer,
    webauthn: CustomerWebAuthnServiceDep,
    totp: CustomerTOTPServiceDep,
    sessions: Annotated[CustomerSessionStore, Depends(get_customer_session_store)],
    settings: AppSettings,
) -> dict[str, Any]:
    _require_webauthn(settings)
    await _require_recent_auth(request, settings, sessions, customer.id, totp, webauthn)
    return await webauthn.begin_registration(
        user_id=customer.id, login=customer.login, session_token=_session_token(request)
    )


@router.post("/passkey/register/finish", response_model=PasskeyStatusOut)
async def passkey_register_finish(
    body: PasskeyRegisterFinishIn,
    request: Request,
    customer: CurrentCustomer,
    webauthn: CustomerWebAuthnServiceDep,
    totp: CustomerTOTPServiceDep,
    sessions: Annotated[CustomerSessionStore, Depends(get_customer_session_store)],
    settings: AppSettings,
) -> PasskeyStatusOut:
    _require_webauthn(settings)
    await _require_recent_auth(request, settings, sessions, customer.id, totp, webauthn)
    row = await webauthn.finish_registration(
        user_id=customer.id,
        session_token=_session_token(request),
        credential=body.credential,
        name=body.name,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired passkey registration",
        )
    return PasskeyStatusOut(id=int(row.id), name=row.name, enabled=True)


@router.get("/passkey", response_model=list[PasskeyOut])
async def passkey_list(
    customer: CurrentCustomer, webauthn: CustomerWebAuthnServiceDep, settings: AppSettings
) -> list[PasskeyOut]:
    _require_webauthn(settings)
    rows = await webauthn.list(customer.id)
    return [
        PasskeyOut(
            id=int(row.id), name=row.name, created=row.created, last_used_at=row.last_used_at
        )
        for row in rows
    ]


@router.delete("/passkey/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_delete(
    passkey_id: int,
    request: Request,
    customer: CurrentCustomer,
    webauthn: CustomerWebAuthnServiceDep,
    totp: CustomerTOTPServiceDep,
    sessions: Annotated[CustomerSessionStore, Depends(get_customer_session_store)],
    settings: AppSettings,
) -> Response:
    _require_webauthn(settings)
    await _require_recent_auth(request, settings, sessions, customer.id, totp, webauthn)
    if not await webauthn.delete(customer.id, passkey_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/passkey/authenticate/begin")
async def passkey_authenticate_begin(
    pending: PendingCustomer,
    webauthn: CustomerWebAuthnServiceDep,
    settings: AppSettings,
) -> dict[str, Any]:
    """Start a passkey assertion; the challenge is bound to the pending token."""
    _require_webauthn(settings)
    options = await webauthn.begin_authentication(
        user_id=pending.customer_id, session_token=pending.token
    )
    if options is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No passkeys registered for this account",
        )
    return options


@router.post("/passkey/authenticate/finish", response_model=CustomerLoginResponse)
async def passkey_authenticate_finish(
    body: PasskeyAuthenticateFinishIn,
    request: Request,
    response: Response,
    pending: PendingCustomer,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    webauthn: CustomerWebAuthnServiceDep,
    settings: AppSettings,
) -> CustomerLoginResponse:
    _require_webauthn(settings)
    await _throttled(
        request,
        settings,
        pending.login,
        lambda: webauthn.finish_authentication(
            user_id=pending.customer_id, session_token=pending.token, credential=body.credential
        ),
        on_fail=HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired passkey assertion",
        ),
    )
    return await _promote(response, settings, auth, pending.token)
