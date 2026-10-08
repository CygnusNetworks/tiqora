"""Provider-independent SSO login flow shared by the agent and portal routes (D15).

``start_sso_login`` sends the browser to the IdP, ``finish_oidc_callback`` turns the
IdP answer into an :class:`ExternalIdentity`, and ``complete_login`` plans/applies the
login and issues the session. The SAML branch arrives with Task 10.
"""

from __future__ import annotations

import contextlib
import secrets
from typing import Any
from urllib.parse import quote

import httpx
import structlog
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import get_redis
from tiqora.config import Settings
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.domain.customer_auth import CustomerAuthService, CustomerSessionStore
from tiqora.domain.oidc import (
    OidcClientConfig,
    OIDCError,
    OIDCOutboundError,
    OIDCService,
    oidc_client_config,
)
from tiqora.identity import flow_state
from tiqora.identity.config_models import OidcProviderConfig
from tiqora.identity.env_providers import (
    ensure_env_providers,
    runtime_config,
    sync_env_providers,
)
from tiqora.identity.flow_state import LoginState, TestResultData, TestRun
from tiqora.identity.http import (
    login_error_redirect,
    login_landing_response,
    safe_next,
    safe_portal_next,
    set_session_cookie,
)
from tiqora.identity.login import (
    REJECT_MESSAGES,
    ExternalIdentity,
    LoginRejected,
    SsoSessionDraft,
    apply_login,
    plan_login,
    plan_to_dict,
)
from tiqora.identity.mapping import normalize_attributes
from tiqora.identity.sso_sessions import SsoSessionInfo
from tiqora.identity.store import ProviderRecord, get_provider_by_slug
from tiqora.identity.types import (
    ENV_OIDC_SLUG,
    SSO_KINDS,
    UserKind,
    audience_includes,
    subject_hash,
)
from tiqora.identity.urls import IdentityUrlsUnavailable, provider_urls

logger = structlog.get_logger(__name__)

OIDC_STATE_COOKIE = "tiqora_oidc_state"


def new_oidc_service(config: OidcClientConfig) -> OIDCService:
    """Factory seam: tests replace it to inject a fake IdP transport."""
    return OIDCService(config)


class _Fail(Exception):
    """A rejected/failed login step; converted to a redirect or an HTTP error."""

    def __init__(self, code: str, *, http_status: int, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.detail = detail


def _reject_fail(code: str, *, value: str = "") -> _Fail:
    if code == "no_match":
        return _Fail(
            code,
            http_status=status.HTTP_403_FORBIDDEN,
            detail=f"No local user matches SSO identity '{value}'",
        )
    if code == "sso_not_enabled":
        return _Fail(
            code, http_status=status.HTTP_403_FORBIDDEN, detail="SSO not enabled for this account"
        )
    return _Fail(
        code,
        http_status=status.HTTP_403_FORBIDDEN,
        detail=REJECT_MESSAGES.get(code, "Login rejected"),
    )


def _error_response(
    exc: _Fail, *, user_kind: UserKind, next_path: str, json_response: bool
) -> Response:
    if json_response:
        raise HTTPException(status_code=exc.http_status, detail=exc.detail)
    return login_error_redirect(user_kind, exc.code, next_path)


def _default_next(user_kind: UserKind, next_path: str | None) -> str:
    return safe_next(next_path) if user_kind == "agent" else safe_portal_next(next_path)


async def _ensure_env(session: AsyncSession, settings: Settings) -> None:
    """Env provider rows are best effort here: login must not die on a failed sync."""
    try:
        await ensure_env_providers(session, settings)
    except Exception as exc:  # noqa: BLE001 - any DB trouble is non-fatal for env SSO
        logger.warning("env_provider_sync_failed", error=str(exc))
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback_after_failed_env_sync_failed")


async def _load_provider(
    session: AsyncSession, settings: Settings, slug: str
) -> ProviderRecord | None:
    """Provider row by slug. The env OIDC row is re-synced when missing or stale.

    Production env OIDC must not fail because its row was deleted or its ``enabled``
    flag disagrees with Settings; a failed sync is non-fatal.
    """
    record = await get_provider_by_slug(session, slug, secret_key=settings.secret_key)
    if slug == ENV_OIDC_SLUG and (record is None or record.enabled != settings.oidc_enabled):
        try:
            await sync_env_providers(session, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("env_provider_resync_failed", error=str(exc))
            with contextlib.suppress(Exception):
                await session.rollback()
        else:
            record = await get_provider_by_slug(session, slug, secret_key=settings.secret_key)
    return record


async def resolve_admin_id(
    request: Request, session: AsyncSession, settings: Settings
) -> int | None:
    """Agent id of the (admin's) agent session cookie, or None."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    redis = await get_redis(request)
    user = await AuthService(session, SessionStore(redis, settings), settings).resolve_session(
        token
    )
    return user.id if user else None


def _admin_test_redirect(provider_id: int, test_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/admin/identity-providers/{provider_id}?test={quote(test_id, safe='')}",
        status_code=status.HTTP_302_FOUND,
    )


async def _load_test(
    redis: Any, test_id: str, record: ProviderRecord, user_kind: UserKind
) -> TestRun | None:
    run = await flow_state.get_test(redis, test_id)
    if run is None or run.provider_id != record.id or run.audience != user_kind:
        return None
    return run


def _redirect_uri(
    settings: Settings, record: ProviderRecord, cfg: OidcProviderConfig, user_kind: UserKind
) -> str:
    urls = provider_urls(settings, record)
    uri = (
        (cfg.redirect_uri or urls.agent_redirect_uri)
        if user_kind == "agent"
        else (urls.customer_redirect_uri)
    )
    if not uri:
        raise IdentityUrlsUnavailable("no redirect URI available")
    return uri


async def start_sso_login(
    request: Request,
    *,
    slug: str,
    user_kind: UserKind,
    next_path: str | None,
    test_id: str | None,
    session: AsyncSession,
    settings: Settings,
    current_admin_id: int | None,
) -> Response:
    target = _default_next(user_kind, next_path)

    def fail(code: str) -> Response:
        return login_error_redirect(user_kind, code, target)

    await _ensure_env(session, settings)
    try:
        record = await _load_provider(session, settings, slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sso_provider_lookup_failed", slug=slug, error=str(exc))
        return fail("config")
    if (
        record is None
        or record.kind not in SSO_KINDS
        or not audience_includes(record.audience, user_kind)
    ):
        return fail("unknown_provider")

    redis = await get_redis(request)
    run: TestRun | None = None
    if test_id:
        run = await _load_test(redis, test_id, record, user_kind)
        if (
            run is None
            or run.status != "started"
            or current_admin_id is None
            or run.admin_id != current_admin_id
        ):
            return fail("state")
    if not record.enabled and run is None:
        return fail("provider_disabled")

    if record.kind == "saml":
        return fail("saml_unavailable")  # SAML start: Task 10

    cfg = runtime_config(record, settings)
    if not isinstance(cfg, OidcProviderConfig):
        return fail("config")
    try:
        client_config = oidc_client_config(
            cfg, redirect_uri=_redirect_uri(settings, record, cfg, user_kind)
        )
    except IdentityUrlsUnavailable:
        return fail("config")
    oidc = new_oidc_service(client_config)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    try:
        # PKCE per provider setting; the verifier lives server-side under the state key,
        # so it never touches the browser and only the initiating flow can redeem the code.
        verifier = secrets.token_urlsafe(64) if await oidc.use_pkce() else None
        url = await oidc.authorize_url(state, code_verifier=verifier, nonce=nonce)
    except (OIDCError, httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("oidc_discovery_failed", slug=slug, error=str(exc))
        return fail("idp_error")
    await flow_state.put_oidc_state(
        redis,
        state,
        LoginState(
            provider_id=record.id,
            next_path=target,
            user_kind=user_kind,
            test_id=test_id,
            code_verifier=verifier,
            nonce=nonce,
        ),
    )
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # Bind the state to THIS browser: the callback requires this cookie to match
    # the returned state, so an attacker cannot replay a state/code they minted
    # into a victim's session (OIDC login CSRF / session fixation).
    resp.set_cookie(
        key=OIDC_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",  # sent on the top-level IdP -> callback redirect
        max_age=flow_state.OIDC_STATE_TTL,
        path="/",
    )
    return resp


def _picture(claims: dict[str, Any]) -> str | None:
    raw = claims.get("picture")
    if isinstance(raw, str):
        picture = raw.strip()
        if picture.startswith(("http://", "https://")):
            return picture
    return None


async def finish_oidc_callback(
    request: Request,
    *,
    slug: str,
    user_kind: UserKind,
    code: str | None,
    state: str | None,
    session: AsyncSession,
    settings: Settings,
    json_response: bool = False,
) -> Response:
    response = await _finish_oidc_callback(
        request,
        slug=slug,
        user_kind=user_kind,
        code=code,
        state=state,
        session=session,
        settings=settings,
        json_response=json_response,
    )
    response.delete_cookie(OIDC_STATE_COOKIE, path="/")
    return response


async def _finish_oidc_callback(
    request: Request,
    *,
    slug: str,
    user_kind: UserKind,
    code: str | None,
    state: str | None,
    session: AsyncSession,
    settings: Settings,
    json_response: bool,
) -> Response:
    target = _default_next(user_kind, None)

    def fail(exc: _Fail, next_path: str | None = None) -> Response:
        return _error_response(
            exc, user_kind=user_kind, next_path=next_path or target, json_response=json_response
        )

    state_fail = _Fail(
        "state", http_status=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
    )
    if not state:
        return fail(
            _Fail("state", http_status=status.HTTP_400_BAD_REQUEST, detail="Missing code/state")
        )
    redis = await get_redis(request)
    cookie_state = request.cookies.get(OIDC_STATE_COOKIE)
    # State must exist server-side AND match the per-browser cookie set at login start.
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        return fail(state_fail)

    await _ensure_env(session, settings)
    try:
        record = await _load_provider(session, settings, slug)
        env_row = (
            record
            if slug == ENV_OIDC_SLUG
            else await get_provider_by_slug(session, ENV_OIDC_SLUG, secret_key=settings.secret_key)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sso_provider_lookup_failed", slug=slug, error=str(exc))
        return fail(
            _Fail(
                "config", http_status=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO unavailable"
            )
        )
    login_state = await flow_state.pop_oidc_state(
        redis, state, env_oidc_provider_id=env_row.id if env_row else None
    )
    if (
        login_state is None
        or record is None
        or login_state.provider_id != record.id
        or login_state.user_kind != user_kind
    ):
        return fail(state_fail)
    next_path = login_state.next_path
    if record.kind != "oidc" or not audience_includes(record.audience, user_kind):
        return fail(
            _Fail(
                "unknown_provider", http_status=status.HTTP_404_NOT_FOUND, detail="Unknown provider"
            ),
            next_path,
        )

    run: TestRun | None = None
    if login_state.test_id:
        run = await _load_test(redis, login_state.test_id, record, user_kind)
        if run is None or run.status != "started":
            return fail(state_fail, next_path)
    if not record.enabled and run is None:
        return fail(
            _Fail(
                "provider_disabled",
                http_status=status.HTTP_404_NOT_FOUND,
                detail="Provider disabled",
            ),
            next_path,
        )

    async def failed(exc: _Fail) -> Response:
        if run is not None and login_state.test_id:
            run.status = "failed"
            run.error = exc.code
            await flow_state.put_test(redis, login_state.test_id, run)
            return _admin_test_redirect(record.id, login_state.test_id)
        return fail(exc, next_path)

    if not code:
        return await failed(
            _Fail(
                "oidc_failed", http_status=status.HTTP_400_BAD_REQUEST, detail="Missing code/state"
            )
        )
    cfg = runtime_config(record, settings)
    if not isinstance(cfg, OidcProviderConfig):
        return await failed(
            _Fail(
                "config", http_status=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO unavailable"
            )
        )
    try:
        client_config = oidc_client_config(
            cfg, redirect_uri=_redirect_uri(settings, record, cfg, user_kind)
        )
    except IdentityUrlsUnavailable:
        return await failed(
            _Fail(
                "config", http_status=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO unavailable"
            )
        )
    try:
        result = await new_oidc_service(client_config).fetch_tokens_and_claims(
            code, code_verifier=login_state.code_verifier
        )
    except OIDCOutboundError as exc:
        logger.warning("oidc_exchange_blocked", slug=slug, error=str(exc))
        return await failed(
            _Fail("idp_error", http_status=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
        )
    except (OIDCError, httpx.HTTPError, ValueError) as exc:
        logger.warning("oidc_exchange_failed", slug=slug, error=str(exc))
        return await failed(
            _Fail("oidc_failed", http_status=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
        )

    claims = result.claims
    sub_raw = claims.get("sub")
    subject = (
        str(sub_raw)
        if isinstance(sub_raw, (str, int)) and not isinstance(sub_raw, bool) and str(sub_raw)
        else ""
    )
    attributes = dict(normalize_attributes(claims))
    if record.source == "env":
        # Production env OIDC: the configured login claim is mandatory, and a
        # provider without "sub" falls back to its value as the link subject.
        login_value = claims.get(settings.oidc_claim)
        if not login_value or not isinstance(login_value, str):
            return await failed(
                _Fail(
                    "no_subject",
                    http_status=status.HTTP_401_UNAUTHORIZED,
                    detail=f"OIDC claim '{settings.oidc_claim}' missing from userinfo response",
                )
            )
        subject = subject or login_value
    if subject:
        attributes["oidc:sub"] = (subject,)
    verified = claims.get("email_verified")
    identity = ExternalIdentity(
        provider_id=record.id,
        subject=subject,
        attributes=attributes,
        email_verified=verified if isinstance(verified, bool) else None,
        sso=SsoSessionDraft(id_token_hint=result.id_token),
    )
    return await complete_login(
        request,
        record=record,
        identity=identity,
        user_kind=user_kind,
        next_path=next_path,
        test_id=login_state.test_id,
        session=session,
        settings=settings,
        avatar_url=_picture(claims),
        json_response=json_response,
    )


async def complete_login(
    request: Request,
    *,
    record: ProviderRecord,
    identity: ExternalIdentity,
    user_kind: UserKind,
    next_path: str,
    test_id: str | None,
    session: AsyncSession,
    settings: Settings,
    avatar_url: str | None = None,
    landing_html: bool = False,
    json_response: bool = False,
) -> Response:
    """Plan and (unless it is an admin test) apply the login, then issue the session."""
    try:
        return await _complete_login(
            request,
            record=record,
            identity=identity,
            user_kind=user_kind,
            next_path=next_path,
            test_id=test_id,
            session=session,
            settings=settings,
            avatar_url=avatar_url,
            landing_html=landing_html,
            json_response=json_response,
        )
    except _Fail as exc:
        return _error_response(
            exc, user_kind=user_kind, next_path=next_path, json_response=json_response
        )


async def _complete_login(
    request: Request,
    *,
    record: ProviderRecord,
    identity: ExternalIdentity,
    user_kind: UserKind,
    next_path: str,
    test_id: str | None,
    session: AsyncSession,
    settings: Settings,
    avatar_url: str | None,
    landing_html: bool,
    json_response: bool,
) -> Response:
    redis = await get_redis(request)
    plan = await plan_login(session, record, identity, user_kind=user_kind, settings=settings)

    if test_id:
        # Admin test: show what WOULD happen; never apply, never create a session.
        run = await _load_test(redis, test_id, record, user_kind)
        if run is None:
            raise _Fail(
                "state", http_status=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state"
            )
        run.status = "done"
        run.result = TestResultData(
            subject=identity.subject,
            attributes={k: list(v) for k, v in identity.attributes.items()},
            plan=plan_to_dict(plan),
        )
        await flow_state.put_test(redis, test_id, run)
        return _admin_test_redirect(record.id, test_id)

    if plan.decision == "reject":
        raise _reject_fail(
            plan.reject_code or "no_match", value=plan.profile.login or identity.subject
        )
    try:
        user_id, _login = await apply_login(session, record, identity, plan, settings=settings)
    except LoginRejected as exc:
        raise _reject_fail(exc.reject_code, value=plan.profile.login or identity.subject) from exc

    draft = identity.sso or SsoSessionDraft()
    sso = SsoSessionInfo(
        provider_id=record.id,
        slug=record.slug,
        kind=record.kind,
        subject_hash=subject_hash(identity.subject),
        session_index=draft.session_index,
        name_id=draft.name_id,
        name_id_format=draft.name_id_format,
        name_qualifier=draft.name_qualifier,
        sp_name_qualifier=draft.sp_name_qualifier,
        sid=draft.sid,
        id_token_hint=draft.id_token_hint,
    )
    # SSO is the strong factor: no TOTP/passkey step, a full session right away.
    agent_user = None
    if user_kind == "agent":
        auth = AuthService(session, SessionStore(redis, settings), settings)
        agent_user = await auth.get_user_by_id(user_id)
        if agent_user is None:
            raise _reject_fail("user_invalid")
        token = await auth.create_session(agent_user, avatar_url=avatar_url, sso=sso)
    else:
        customer_auth = CustomerAuthService(
            session, CustomerSessionStore(redis, settings), settings
        )
        customer = await customer_auth.get_customer_by_id(user_id)
        if customer is None:
            raise _reject_fail("user_invalid")
        token = await customer_auth.create_session(customer, sso=sso)

    response: Response
    if json_response and agent_user is not None:
        from tiqora.api.v1.auth import _user_me  # lazy: auth.py imports this module
        from tiqora.domain.schemas import LoginResponse

        body = LoginResponse(
            user=await _user_me(session, agent_user, extra={"avatar_url": avatar_url})
        )
        response = JSONResponse(body.model_dump(mode="json"))
    elif landing_html:
        response = login_landing_response(next_path)
    else:
        response = RedirectResponse(next_path, status_code=status.HTTP_302_FOUND)
    set_session_cookie(response, settings, token, user_kind=user_kind)
    return response
