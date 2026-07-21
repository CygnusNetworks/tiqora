"""Auth endpoints: login, me, logout, method discovery, OIDC/SSO, SPNEGO, TOTP 2FA."""

from __future__ import annotations

import base64
import secrets
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    TOTPServiceDep,
    get_auth_service,
    get_redis,
)
from tiqora.domain.auth import AuthenticatedUser, AuthService, user_to_dict
from tiqora.domain.auth_ldap import LdapAuthService
from tiqora.domain.oidc import OIDCError, OIDCService
from tiqora.domain.schemas import (
    AuthMethodsOut,
    LoginRequest,
    LoginResponse,
    TOTPCodeIn,
    TOTPEnrollOut,
    TOTPStatusOut,
    UserMe,
)
from tiqora.domain.spnego import SpnegoService, SpnegoUnavailable, principal_to_login
from tiqora.domain.totp_qr import totp_qr_svg
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_OIDC_STATE_PREFIX = "tiqora:oidc:state:"
_OIDC_STATE_TTL = 300


async def _user_me(
    session: AsyncSession,
    user: AuthenticatedUser,
    *,
    extra: dict[str, Any] | None = None,
) -> UserMe:
    """Build ``UserMe`` including ``is_admin`` from ``PermissionEngine``."""
    pe = PermissionEngine(session)
    data = user_to_dict(user)
    if extra:
        data.update(extra)
    data["is_admin"] = await pe.is_admin(user.id)
    return UserMe(**data)


def _set_session_cookie(response: Response, settings: AppSettings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.get("/methods", response_model=AuthMethodsOut)
async def auth_methods(settings: AppSettings) -> AuthMethodsOut:
    """Discovery endpoint the login page uses to decide which buttons to show."""
    return AuthMethodsOut(
        password=True,
        oidc=settings.oidc_enabled,
        spnego=settings.spnego_enabled,
        ldap=settings.ldap_enabled,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    settings: AppSettings,
    session: DbSession,
) -> LoginResponse:
    user = await auth.authenticate_password(body.login, body.password)
    if user is None and settings.ldap_enabled:
        # LDAP is tried as a fallback when local password auth fails,
        # mirroring Znuny's chained AuthModule::LDAP behaviour. No
        # auto-provisioning in v1: the resolved LDAP UID must match an
        # existing, valid `users.login` row.
        ldap_service = LdapAuthService(settings)
        ldap_uid = await ldap_service.authenticate(body.login, body.password)
        if ldap_uid is not None:
            user = await auth.get_user_by_login(ldap_uid, auth_method="ldap")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if await totp.is_enabled(user.id):
        pending_token = await auth.create_pending_session(user)
        _set_session_cookie(response, settings, pending_token)
        return LoginResponse(user=None, pending_2fa=True)
    token = await auth.create_session(user)
    _set_session_cookie(response, settings, token)
    return LoginResponse(user=await _user_me(session, user))


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser, session: DbSession) -> UserMe:
    return await _user_me(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: AppSettings,
) -> Response:
    token = getattr(request.state, "session_token", None) or request.cookies.get(
        settings.session_cookie_name
    )
    if token:
        await auth.logout(token)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# TOTP 2FA
# ---------------------------------------------------------------------------


@router.post("/totp/verify", response_model=LoginResponse)
async def totp_verify(
    body: TOTPCodeIn,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    settings: AppSettings,
    session: DbSession,
) -> LoginResponse:
    """Promote a pending-2FA session to a full session after a valid TOTP code."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    pending = await auth.get_pending_session(token)
    if pending is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    user_id, _login = pending
    if not await totp.verify(user_id, body.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")
    promoted = await auth.promote_pending_session(token)
    if promoted is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    new_token, user = promoted
    _set_session_cookie(response, settings, new_token)
    return LoginResponse(user=await _user_me(session, user))


@router.post("/totp/enroll", response_model=TOTPEnrollOut)
async def totp_enroll(user: CurrentUser, totp: TOTPServiceDep) -> TOTPEnrollOut:
    secret, uri = await totp.enroll(user.id, user.login)
    return TOTPEnrollOut(secret=secret, otpauth_uri=uri)


@router.get("/totp/enroll/qr")
async def totp_enroll_qr(user: CurrentUser, totp: TOTPServiceDep) -> Response:
    """SVG QR code for the pending enrollment's ``otpauth://`` URI.

    404 if the caller has no pending enrollment (never called
    ``POST /totp/enroll``, or already confirmed one — re-enroll first).
    """
    uri = await totp.get_pending_provisioning_uri(user.id, user.login)
    if uri is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending TOTP enrollment"
        )
    return Response(content=totp_qr_svg(uri), media_type="image/svg+xml")


@router.post("/totp/confirm", response_model=TOTPStatusOut)
async def totp_confirm(body: TOTPCodeIn, user: CurrentUser, totp: TOTPServiceDep) -> TOTPStatusOut:
    ok = await totp.confirm(user.id, body.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")
    return TOTPStatusOut(enabled=True)


@router.delete("/totp", response_model=TOTPStatusOut)
async def totp_disable(body: TOTPCodeIn, user: CurrentUser, totp: TOTPServiceDep) -> TOTPStatusOut:
    ok = await totp.disable(user.id, body.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")
    return TOTPStatusOut(enabled=False)


@router.get("/totp/status", response_model=TOTPStatusOut)
async def totp_status(user: CurrentUser, totp: TOTPServiceDep) -> TOTPStatusOut:
    return TOTPStatusOut(enabled=await totp.is_enabled(user.id))


# ---------------------------------------------------------------------------
# OIDC / SSO
# ---------------------------------------------------------------------------


@router.get("/oidc/login")
async def oidc_login(request: Request, settings: AppSettings) -> RedirectResponse:
    if not settings.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC is not enabled")
    redis_client = await get_redis(request)
    state = secrets.token_urlsafe(24)
    await redis_client.set(f"{_OIDC_STATE_PREFIX}{state}", "1", ex=_OIDC_STATE_TTL)
    oidc = OIDCService(settings)
    try:
        url = await oidc.authorize_url(state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("oidc_discovery_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="OIDC provider unreachable"
        ) from exc
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/oidc/callback", response_model=LoginResponse)
async def oidc_callback(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    settings: AppSettings,
    session: DbSession,
    code: str | None = None,
    state: str | None = None,
) -> LoginResponse:
    if not settings.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC is not enabled")
    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code/state")
    redis_client = await get_redis(request)
    state_key = f"{_OIDC_STATE_PREFIX}{state}"
    if not await redis_client.get(state_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
        )
    await redis_client.delete(state_key)

    oidc = OIDCService(settings)
    try:
        claims = await oidc.fetch_claims(code)
    except OIDCError as exc:
        logger.warning("oidc_exchange_failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    login_value = claims.get(settings.oidc_claim)
    if not login_value or not isinstance(login_value, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC claim '{settings.oidc_claim}' missing from userinfo response",
        )

    user = await auth.get_user_by_login(login_value, auth_method="sso")
    if user is None:
        # No auto-provisioning in v1: unknown users are rejected outright.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No local user matches SSO identity '{login_value}'",
        )

    # Best-effort Google/OIDC profile picture (userinfo ``picture`` claim).
    picture_raw = claims.get("picture")
    avatar_url: str | None = None
    if isinstance(picture_raw, str):
        picture = picture_raw.strip()
        if picture.startswith(("http://", "https://")):
            avatar_url = picture

    if await totp.is_enabled(user.id):
        pending_token = await auth.create_pending_session(user, avatar_url=avatar_url)
        _set_session_cookie(response, settings, pending_token)
        return LoginResponse(user=None, pending_2fa=True)

    token = await auth.create_session(user, avatar_url=avatar_url)
    _set_session_cookie(response, settings, token)
    return LoginResponse(user=await _user_me(session, user, extra={"avatar_url": avatar_url}))


# ---------------------------------------------------------------------------
# Kerberos / SPNEGO
# ---------------------------------------------------------------------------


@router.get("/spnego", response_model=LoginResponse)
async def spnego(
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    settings: AppSettings,
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> LoginResponse:
    if not settings.spnego_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SPNEGO is not enabled")

    if not authorization or not authorization.startswith("Negotiate "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Negotiate token required",
            headers={"WWW-Authenticate": "Negotiate"},
        )

    raw_token = authorization[len("Negotiate ") :].strip()
    try:
        token_bytes = base64.b64decode(raw_token)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed Negotiate token"
        ) from exc

    service = SpnegoService(settings)
    try:
        principal = await service.accept(token_bytes)
    except SpnegoUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    login_value = principal_to_login(principal)
    user = await auth.get_user_by_login(login_value, auth_method="spnego")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No local user matches Kerberos principal '{principal}'",
        )

    if await totp.is_enabled(user.id):
        pending_token = await auth.create_pending_session(user)
        _set_session_cookie(response, settings, pending_token)
        return LoginResponse(user=None, pending_2fa=True)

    token = await auth.create_session(user)
    _set_session_cookie(response, settings, token)
    return LoginResponse(user=await _user_me(session, user))
