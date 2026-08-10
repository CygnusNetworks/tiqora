"""Auth endpoints: login, me, logout, method discovery, OIDC/SSO, SPNEGO, TOTP/passkey 2FA."""

from __future__ import annotations

import base64
import contextlib
import secrets
from html import escape as html_escape
from typing import Annotated, Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import (
    AppSettings,
    CurrentUser,
    DbSession,
    EnrollableUser,
    TOTPServiceDep,
    WebAuthnServiceDep,
    get_auth_service,
    get_redis,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.auth import (
    AuthenticatedUser,
    AuthService,
    decode_preference_value,
    normalize_language_code,
    user_to_dict,
)
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.auth_ldap import LdapAuthService
from tiqora.domain.oidc import OIDCError, OIDCService
from tiqora.domain.passkey import webauthn_enabled
from tiqora.domain.password_policy import PasswordPolicyError, validate_password
from tiqora.domain.password_setup import redeem_token, resolve_token
from tiqora.domain.portal_gate import portal_enabled as resolve_portal_enabled
from tiqora.domain.schemas import (
    AuthMethodsOut,
    LoginRequest,
    LoginResponse,
    PasskeyAuthenticateFinishIn,
    PasskeyOut,
    PasskeyRegisterFinishIn,
    PasskeyStatusOut,
    TOTPCodeIn,
    TOTPEnrollOut,
    TOTPStatusOut,
    UserLanguageUpdate,
    UserMe,
)
from tiqora.domain.spnego import (
    SpnegoAuthFailed,
    SpnegoService,
    SpnegoUnavailable,
    principal_to_login,
)
from tiqora.domain.template_permission import TemplatePermissionService
from tiqora.domain.totp_qr import totp_qr_svg
from tiqora.permissions.engine import PermissionEngine
from tiqora.security.ratelimit import AuthRateLimiter, client_ip

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_OIDC_STATE_PREFIX = "tiqora:oidc:state:"
_OIDC_STATE_TTL = 300
_OIDC_STATE_COOKIE = "tiqora_oidc_state"


async def _load_user_language(session: AsyncSession, user_id: int) -> str | None:
    """Read Znuny ``UserLanguage`` preference; never raises into /me."""
    from sqlalchemy import text

    try:
        # Raw SQL: ORM LargeBinary vs PG TEXT can mis-coerce preference values.
        result = await session.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id = :uid AND preferences_key = 'UserLanguage'"
            ),
            {"uid": user_id},
        )
        return decode_preference_value(result.scalar_one_or_none())
    except Exception:  # noqa: BLE001 — never fail /me for a missing pref
        return None


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
    # Load language before optional tiqora-table probes. On Postgres a failed
    # statement aborts the transaction; later queries then raise and would be
    # swallowed by the language loader's broad except → language always null.
    if "language" not in data:
        data["language"] = await _load_user_language(session, user.id)
    try:
        data["can_edit_templates"] = await TemplatePermissionService(session).can_edit_any(user.id)
    except Exception:  # noqa: BLE001 — never fail /me if tiqora tables missing
        data["can_edit_templates"] = False
        # Clear aborted transaction so callers (e.g. PUT language) can continue.
        with contextlib.suppress(Exception):
            await session.rollback()
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


def _clear_session_cookie(response: Response, settings: AppSettings) -> None:
    """Delete the session cookie with the same flags used when setting it (M-10)."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
    )


def _safe_next(next_path: str | None, *, default: str = "/") -> str:
    """Same-site absolute path only — reject protocol-relative (``//evil``),
    backslash tricks (``/\\evil``) and anything carrying a scheme/host, so the
    post-SSO redirect can never be turned into an open redirect."""
    if (
        isinstance(next_path, str)
        and next_path.startswith("/")
        and not next_path.startswith("//")
        and not next_path.startswith("/\\")
    ):
        return next_path
    return default


def _sso_failure_redirect(safe_next: str) -> RedirectResponse:
    """Land a failed browser SSO handshake on the login page (not a bare 401,
    which would trigger a native Negotiate popup / retry loop). ``sso_error=1``
    also stops the login page from auto-retrying SSO."""
    target = f"/login?sso_error=1&next={quote(safe_next, safe='')}"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


def _wants_html(request: Request) -> bool:
    """True for a real browser navigation (``Accept: text/html``). API clients
    and tests send ``application/json`` (or no ``Accept``), and keep the JSON
    error body so nothing downstream breaks."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()


def _spnego_html_error(
    status_code: int,
    title: str,
    message: str,
    *,
    next_path: str,
    extra_headers: dict[str, str] | None = None,
    auto_redirect: bool = False,
) -> HTMLResponse:
    """A self-contained, themed HTML error page for the SPNEGO endpoint.

    Shown only to browsers (see ``_wants_html``); API clients get JSON. All CSS
    is inline and there are no external resources, so it is CSP-safe. Bilingual
    (German primary, English secondary). When ``auto_redirect`` is set, a
    ``meta http-equiv=refresh`` sends a browser that cannot complete SPNEGO to
    the login page after a short delay — harmless for a browser that *can*
    complete SPNEGO, since it acts on the 401 + ``WWW-Authenticate`` and never
    renders this body.
    """
    login_url = f"/login?sso_error=1&next={quote(next_path, safe='')}"
    login_attr = html_escape(login_url, quote=True)
    meta_refresh = (
        f'<meta http-equiv="refresh" content="3;url={login_attr}">' if auto_redirect else ""
    )
    page = f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta_refresh}
<title>{html_escape(title)}</title>
<style>
  :root {{
    --bg: #f3f5fa; --surface: #ffffff; --ink: #1a1f2c; --muted: #66708a;
    --hairline: #e1e5ef; --accent: #3b6be8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0e1015; --surface: #151821; --ink: #e7eaf2; --muted: #8891a3;
      --hairline: #242a38; --accent: #5b8cff;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; margin: 0; }}
  body {{
    background: var(--bg); color: var(--ink);
    font-family: "Inter","Segoe UI",system-ui,sans-serif;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 24px; line-height: 1.55;
  }}
  .card {{
    background: var(--surface); border: 1px solid var(--hairline);
    border-radius: 14px; max-width: 440px; width: 100%;
    padding: 32px 32px 28px; box-shadow: 0 8px 30px rgba(0,0,0,.08);
  }}
  h1 {{ font-size: 20px; margin: 0 0 12px; font-weight: 650; }}
  p {{ margin: 0 0 14px; color: var(--ink); }}
  p.en {{ font-size: 13px; color: var(--muted); }}
  a.btn {{
    display: inline-block; margin-top: 6px; padding: 11px 20px;
    background: var(--accent); color: #fff; text-decoration: none;
    border-radius: 9px; font-weight: 600; font-size: 15px;
  }}
  a.btn:hover {{ filter: brightness(1.06); }}
</style>
</head>
<body>
  <main class="card">
    <h1>{html_escape(title)}</h1>
    <p>{html_escape(message)}</p>
    <p class="en">Automatic single sign-on could not be completed (for example an
      expired Kerberos ticket). Please sign in with your password to continue.</p>
    <a class="btn" href="{login_attr}">Zur Anmeldung / Go to login</a>
  </main>
</body>
</html>"""
    return HTMLResponse(content=page, status_code=status_code, headers=extra_headers)


def _rate_limit_http_exception(decision: object) -> HTTPException:
    retry_after = int(getattr(decision, "retry_after", 0) or 0)
    headers = {"Retry-After": str(max(1, retry_after))} if retry_after else None
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed login attempts; try again later",
        headers=headers,
    )


@router.get("/methods", response_model=AuthMethodsOut)
async def auth_methods(settings: AppSettings, session: DbSession) -> AuthMethodsOut:
    """Discovery endpoint the login page uses to decide which buttons to show."""
    return AuthMethodsOut(
        password=True,
        oidc=settings.oidc_enabled,
        spnego=settings.spnego_enabled,
        ldap=settings.ldap_enabled,
        webauthn=webauthn_enabled(settings),
        portal_enabled=await resolve_portal_enabled(session, settings),
    )


class PasswordSetupCheckOut(BaseModel):
    """Whether a setup link is still usable, so the page can say so before the
    visitor types a password. ``login`` is disclosed only for a valid token —
    holding the token already implies access to that account's mailbox."""

    valid: bool
    login: str | None = None


class PasswordSetupIn(BaseModel):
    token: str
    password: str


@router.get("/password-setup/{token}", response_model=PasswordSetupCheckOut)
async def check_password_setup(token: str, session: DbSession) -> PasswordSetupCheckOut:
    """Public: is this one-time link still good?"""
    user_id = await resolve_token(session, token)
    if user_id is None:
        return PasswordSetupCheckOut(valid=False)
    user = await session.get(Users, user_id)
    if user is None or user.valid_id != 1:
        # The account was invalidated or deleted after the link went out.
        return PasswordSetupCheckOut(valid=False)
    return PasswordSetupCheckOut(valid=True, login=user.login)


@router.post("/password-setup", status_code=status.HTTP_204_NO_CONTENT)
async def complete_password_setup(
    body: PasswordSetupIn, request: Request, settings: AppSettings, session: DbSession
) -> None:
    """Public: spend a one-time link and set the password.

    Rate-limited per IP like the login endpoint — the token is the only
    secret, so brute-forcing it must not be free. Keyed on a constant rather
    than a login because the caller has not identified themselves yet.
    """
    redis_client = await get_redis(request)
    limiter = AuthRateLimiter(redis_client, settings)
    ip = client_ip(request)
    pre = await limiter.check(login="__password_setup__", ip=ip)
    if not pre.allowed:
        raise _rate_limit_http_exception(pre)

    try:
        validate_password(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    user_id = await redeem_token(session, body.token, body.password)
    if user_id is None:
        await limiter.record_failure(login="__password_setup__", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This link is no longer valid. Please request a new one.",
        )
    await session.commit()
    await limiter.reset(login="__password_setup__", ip=ip)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    webauthn: WebAuthnServiceDep,
    settings: AppSettings,
    session: DbSession,
) -> LoginResponse:
    redis_client = await get_redis(request)
    limiter = AuthRateLimiter(redis_client, settings)
    ip = client_ip(request)
    pre = await limiter.check(login=body.login, ip=ip)
    if not pre.allowed:
        raise _rate_limit_http_exception(pre)

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
        locked = await limiter.record_failure(login=body.login, ip=ip)
        if locked is not None:
            raise _rate_limit_http_exception(locked)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    await limiter.reset(login=body.login, ip=ip)
    totp_enrolled = await totp.is_enabled(user.id)
    passkey_enrolled = await webauthn.has_passkey(user.id)
    if totp_enrolled or passkey_enrolled:
        pending_token = await auth.create_pending_session(user)
        _set_session_cookie(response, settings, pending_token)
        return LoginResponse(
            user=None,
            pending_2fa=True,
            totp_enrolled=totp_enrolled,
            passkey_enrolled=passkey_enrolled,
        )
    auth_config = AuthConfigService(session)
    if await auth_config.effective_enforce(user.id):
        # Forced enrollment: restricted ENROLL session only reaches enroll/
        # confirm (or passkey register) until the agent finishes 2FA setup.
        enroll_token = await auth.create_enroll_session(user)
        _set_session_cookie(response, settings, enroll_token)
        return LoginResponse(user=None, must_enroll_2fa=True)
    token = await auth.create_session(user)
    _set_session_cookie(response, settings, token)
    return LoginResponse(user=await _user_me(session, user))


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser, session: DbSession) -> UserMe:
    return await _user_me(session, user)


@router.put("/me/language", response_model=UserMe)
async def set_my_language(
    body: UserLanguageUpdate,
    user: CurrentUser,
    session: DbSession,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> UserMe:
    """Persist Znuny ``UserLanguage`` so UI choice matches notification language."""
    code = normalize_language_code(body.language)
    if code is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported language code: {body.language!r}",
        )
    await auth.set_user_language(user.id, code)
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
    _clear_session_cookie(response, settings)
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
    # Throttle second-factor guessing (security review M-1): 6-digit codes are
    # brute-forceable once the password is known, so cap wrong attempts too.
    redis_client = await get_redis(request)
    limiter = AuthRateLimiter(redis_client, settings)
    ip = client_ip(request)
    rl_key = f"2fa:{_login}"
    pre = await limiter.check(login=rl_key, ip=ip)
    if not pre.allowed:
        raise _rate_limit_http_exception(pre)
    if not await totp.verify(user_id, body.code):
        locked = await limiter.record_failure(login=rl_key, ip=ip)
        if locked is not None:
            raise _rate_limit_http_exception(locked)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")
    await limiter.reset(login=rl_key, ip=ip)
    promoted = await auth.promote_pending_session(token)
    if promoted is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    new_token, user = promoted
    _set_session_cookie(response, settings, new_token)
    return LoginResponse(user=await _user_me(session, user))


@router.post("/totp/enroll", response_model=TOTPEnrollOut)
async def totp_enroll(user: EnrollableUser, totp: TOTPServiceDep) -> TOTPEnrollOut:
    secret, uri = await totp.enroll(user.id, user.login)
    return TOTPEnrollOut(secret=secret, otpauth_uri=uri)


@router.get("/totp/enroll/qr")
async def totp_enroll_qr(user: EnrollableUser, totp: TOTPServiceDep) -> Response:
    """SVG QR code for the pending enrollment's ``otpauth://`` URI.

    404 if the caller has no pending enrollment (never called
    ``POST /totp/enroll``, or already confirmed one — re-enroll first).
    Accepts a full session or a restricted must-enroll (ENROLL) session.
    """
    uri = await totp.get_pending_provisioning_uri(user.id, user.login)
    if uri is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending TOTP enrollment"
        )
    return Response(content=totp_qr_svg(uri), media_type="image/svg+xml")


@router.post("/totp/confirm", response_model=TOTPStatusOut)
async def totp_confirm(
    body: TOTPCodeIn,
    request: Request,
    response: Response,
    user: EnrollableUser,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    totp: TOTPServiceDep,
    settings: AppSettings,
) -> TOTPStatusOut:
    """Confirm TOTP enrollment. Promotes an ENROLL session to a full session."""
    ok = await totp.confirm(user.id, body.code)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")
    enroll_token = getattr(request.state, "enroll_token", None)
    if enroll_token:
        promoted = await auth.promote_enroll_session(enroll_token)
        if promoted is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
        new_token, _promoted_user = promoted
        _set_session_cookie(response, settings, new_token)
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
# WebAuthn passkeys (alternative 2nd factor)
# ---------------------------------------------------------------------------


def _require_webauthn(settings: AppSettings) -> None:
    if not webauthn_enabled(settings):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WebAuthn is not enabled",
        )


def _session_token_from_request(request: Request, settings: AppSettings) -> str | None:
    token = getattr(request.state, "session_token", None) or getattr(
        request.state, "enroll_token", None
    )
    if token:
        return str(token)
    return request.cookies.get(settings.session_cookie_name)


@router.post("/passkey/register/begin")
async def passkey_register_begin(
    request: Request,
    user: EnrollableUser,
    webauthn: WebAuthnServiceDep,
    settings: AppSettings,
) -> dict[str, Any]:
    """Start passkey registration (full session or restricted ENROLL session)."""
    _require_webauthn(settings)
    token = _session_token_from_request(request, settings)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")
    return await webauthn.begin_registration(user_id=user.id, login=user.login, session_token=token)


@router.post("/passkey/register/finish", response_model=PasskeyStatusOut)
async def passkey_register_finish(
    body: PasskeyRegisterFinishIn,
    request: Request,
    response: Response,
    user: EnrollableUser,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    webauthn: WebAuthnServiceDep,
    settings: AppSettings,
) -> PasskeyStatusOut:
    """Finish passkey registration. Promotes an ENROLL session to a full session."""
    _require_webauthn(settings)
    token = _session_token_from_request(request, settings)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")
    row = await webauthn.finish_registration(
        user_id=user.id,
        session_token=token,
        credential=body.credential,
        name=body.name,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired passkey registration",
        )
    enroll_token = getattr(request.state, "enroll_token", None)
    if enroll_token:
        promoted = await auth.promote_enroll_session(enroll_token)
        if promoted is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
        new_token, _promoted_user = promoted
        _set_session_cookie(response, settings, new_token)
    return PasskeyStatusOut(id=int(row.id), name=row.name, enabled=True)


@router.get("/passkey", response_model=list[PasskeyOut])
async def passkey_list(
    user: CurrentUser, webauthn: WebAuthnServiceDep, settings: AppSettings
) -> list[PasskeyOut]:
    _require_webauthn(settings)
    rows = await webauthn.list(user.id)
    return [
        PasskeyOut(
            id=int(row.id),
            name=row.name,
            created=row.created,
            last_used_at=row.last_used_at,
        )
        for row in rows
    ]


@router.delete("/passkey/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def passkey_delete(
    passkey_id: int,
    user: CurrentUser,
    webauthn: WebAuthnServiceDep,
    totp: TOTPServiceDep,
    settings: AppSettings,
    session: DbSession,
) -> Response:
    """Delete a passkey. Blocked when it is the last remaining 2FA factor under enforce."""
    _require_webauthn(settings)
    row = await webauthn.get_by_id(user.id, passkey_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")

    remaining_after = await webauthn.count(user.id) - 1
    totp_on = await totp.is_enabled(user.id)
    if remaining_after <= 0 and not totp_on:
        auth_config = AuthConfigService(session)
        if await auth_config.effective_enforce(user.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last 2FA factor while 2FA is enforced",
            )

    ok = await webauthn.delete(user.id, passkey_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/passkey/authenticate/begin")
async def passkey_authenticate_begin(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    webauthn: WebAuthnServiceDep,
    settings: AppSettings,
) -> dict[str, Any]:
    """Start passkey assertion for a pending-2FA session."""
    _require_webauthn(settings)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    pending = await auth.get_pending_session(token)
    if pending is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    user_id, _login = pending
    options = await webauthn.begin_authentication(user_id=user_id, session_token=token)
    if options is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No passkeys registered for this account",
        )
    return options


@router.post("/passkey/authenticate/finish", response_model=LoginResponse)
async def passkey_authenticate_finish(
    body: PasskeyAuthenticateFinishIn,
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    webauthn: WebAuthnServiceDep,
    settings: AppSettings,
    session: DbSession,
) -> LoginResponse:
    """Verify passkey assertion and promote a pending-2FA session to a full session."""
    _require_webauthn(settings)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    pending = await auth.get_pending_session(token)
    if pending is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No pending session")
    user_id, _login = pending
    # Same second-factor throttle as TOTP (security review M-1).
    redis_client = await get_redis(request)
    limiter = AuthRateLimiter(redis_client, settings)
    ip = client_ip(request)
    rl_key = f"2fa:{_login}"
    pre = await limiter.check(login=rl_key, ip=ip)
    if not pre.allowed:
        raise _rate_limit_http_exception(pre)
    row = await webauthn.finish_authentication(
        user_id=user_id,
        session_token=token,
        credential=body.credential,
    )
    if row is None:
        locked = await limiter.record_failure(login=rl_key, ip=ip)
        if locked is not None:
            raise _rate_limit_http_exception(locked)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired passkey assertion",
        )
    await limiter.reset(login=rl_key, ip=ip)
    promoted = await auth.promote_pending_session(token)
    if promoted is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    new_token, user = promoted
    _set_session_cookie(response, settings, new_token)
    return LoginResponse(user=await _user_me(session, user))


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
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # Bind the state to THIS browser: the callback requires this cookie to match
    # the returned state, so an attacker cannot replay a state/code they minted
    # into a victim's session (OIDC login CSRF / session fixation — security review).
    resp.set_cookie(
        key=_OIDC_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",  # sent on the top-level IdP → callback redirect
        max_age=_OIDC_STATE_TTL,
        path="/",
    )
    return resp


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
    cookie_state = request.cookies.get(_OIDC_STATE_COOKIE)
    # State must exist server-side AND match the per-browser cookie set at /login.
    if (
        not cookie_state
        or not secrets.compare_digest(cookie_state, state)
        or not await redis_client.get(state_key)
    ):
        response.delete_cookie(_OIDC_STATE_COOKIE, path="/")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
        )
    await redis_client.delete(state_key)
    response.delete_cookie(_OIDC_STATE_COOKIE, path="/")

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

    # Per-account SSO gate, consistent with the SPNEGO path (security review L-1).
    if not (await AuthConfigService(session).get(user.id)).sso_eligible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO not enabled for this account",
        )

    # Best-effort Google/OIDC profile picture (userinfo ``picture`` claim).
    picture_raw = claims.get("picture")
    avatar_url: str | None = None
    if isinstance(picture_raw, str):
        picture = picture_raw.strip()
        if picture.startswith(("http://", "https://")):
            avatar_url = picture

    # SSO is the strong factor — skip the TOTP pending branch entirely.
    _ = totp
    token = await auth.create_session(user, avatar_url=avatar_url)
    _set_session_cookie(response, settings, token)
    return LoginResponse(user=await _user_me(session, user, extra={"avatar_url": avatar_url}))


# ---------------------------------------------------------------------------
# Kerberos / SPNEGO
# ---------------------------------------------------------------------------


@router.get("/spnego", response_model=None)
async def spnego(
    request: Request,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: AppSettings,
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
    next: Annotated[str | None, Query()] = None,
) -> RedirectResponse | HTMLResponse:
    """Kerberos/SPNEGO handshake. On success, 302 back to ``next`` (a same-site
    path, default ``/``) with a full session — this lets an expired-session
    agent re-auth transparently and land back on the page they were on.

    Per-agent ``sso_eligible`` must be true. SSO is the strong factor — 2FA
    is skipped even when the agent has TOTP enrolled. A failed handshake
    (expired/absent ticket) redirects to the login page instead of returning a
    bare 401, so the browser never shows a native Negotiate popup.
    """
    safe_next = _safe_next(next)
    wants_html = _wants_html(request)

    if not settings.spnego_enabled:
        if wants_html:
            return _spnego_html_error(
                status.HTTP_404_NOT_FOUND,
                "Single sign-on nicht verfügbar",
                "Die automatische Kerberos-Anmeldung ist auf diesem Server nicht aktiviert.",
                next_path=safe_next,
                auto_redirect=True,
            )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SPNEGO is not enabled")

    if not authorization or not authorization.startswith("Negotiate "):
        # First leg: challenge the browser. It re-sends the same URL (query
        # string, so ``next`` survives) with the ticket.
        if wants_html:
            # A browser that CAN do SPNEGO acts on the 401 + WWW-Authenticate
            # and never renders this body; one that CAN'T sees the page and is
            # nudged to /login by the meta-refresh. The header must stay so
            # SPNEGO-capable browsers keep negotiating.
            return _spnego_html_error(
                status.HTTP_401_UNAUTHORIZED,
                "Anmeldung wird fortgesetzt …",
                "Ihre automatische Kerberos-Anmeldung wird geprüft. Falls das nicht "
                "funktioniert, werden Sie gleich zur Anmeldeseite weitergeleitet.",
                next_path=safe_next,
                extra_headers={"WWW-Authenticate": "Negotiate"},
                auto_redirect=True,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Negotiate token required",
            headers={"WWW-Authenticate": "Negotiate"},
        )

    raw_token = authorization[len("Negotiate ") :].strip()
    try:
        token_bytes = base64.b64decode(raw_token)
    except (ValueError, TypeError) as exc:
        if wants_html:
            return _spnego_html_error(
                status.HTTP_400_BAD_REQUEST,
                "Anmeldung fehlgeschlagen",
                "Das Anmelde-Token war ungültig. Bitte melden Sie sich mit Ihrem Passwort an.",
                next_path=safe_next,
                auto_redirect=True,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed Negotiate token"
        ) from exc

    service = SpnegoService(settings)
    try:
        principal = await service.accept(token_bytes)
    except SpnegoAuthFailed:
        # Ticket bad/expired/keytab mismatch: the browser already tried its
        # ticket, so re-challenging would loop/popup — fall back to login.
        return _sso_failure_redirect(safe_next)
    except SpnegoUnavailable as exc:
        if wants_html:
            return _spnego_html_error(
                status.HTTP_501_NOT_IMPLEMENTED,
                "Single sign-on nicht verfügbar",
                "Die automatische Anmeldung ist auf diesem Server nicht verfügbar. "
                "Bitte melden Sie sich mit Ihrem Passwort an.",
                next_path=safe_next,
                auto_redirect=True,
            )
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    login_value = principal_to_login(principal)
    user = await auth.get_user_by_login(login_value, auth_method="spnego")
    if user is None:
        if wants_html:
            return _spnego_html_error(
                status.HTTP_403_FORBIDDEN,
                "Kein passendes Konto",
                "Zu Ihrer Kerberos-Kennung existiert kein Konto. Bitte melden Sie "
                "sich mit Ihrem Passwort an.",
                next_path=safe_next,
                auto_redirect=True,
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No local user matches Kerberos principal '{principal}'",
        )

    auth_config = AuthConfigService(session)
    cfg = await auth_config.get(user.id)
    if not cfg.sso_eligible:
        if wants_html:
            return _spnego_html_error(
                status.HTTP_403_FORBIDDEN,
                "Single sign-on nicht aktiviert",
                "Für Ihr Konto ist die automatische Anmeldung nicht aktiviert. "
                "Bitte melden Sie sich mit Ihrem Passwort an.",
                next_path=safe_next,
                auto_redirect=True,
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSO not enabled for this account",
        )

    # SSO is the strong factor — skip TOTP/pending entirely; issue a full session.
    token = await auth.create_session(user)
    redirect = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    _set_session_cookie(redirect, settings, token)
    return redirect
