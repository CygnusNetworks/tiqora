"""Customer portal SSO routes (D15): sign in through OIDC/SAML identity providers.

Mounted under ``/api/portal`` behind ``require_portal_enabled``: a disabled portal answers 404 on
every route here. Sessions are customer sessions only (cookie ``customer_session_cookie_name``,
Redis namespace ``tiqora:csession:*``); the shared flow bodies in ``identity.sso_routes`` run with
``user_kind="customer"``, which also restricts the provider audience to ``customer``/``both``
and refuses state, ACS and logout records minted for the agent login. SAML metadata, SLS and the
OIDC back-channel stay shared under ``/api/v1`` (one SP entity / OIDC client per provider).
Customers get no role/group sync (agent-only) and are never agents.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.auth import login_providers
from tiqora.domain.schemas import PortalAuthMethodsOut
from tiqora.identity.http import login_error_redirect
from tiqora.identity.saml_sp import MAX_RESPONSE_BYTES
from tiqora.identity.sso_routes import (
    finish_oidc_callback,
    finish_saml_acs,
    sso_logout,
    start_sso_login,
)

router = APIRouter(prefix="/auth", tags=["portal-auth"])


@router.get("/methods", response_model=PortalAuthMethodsOut, summary="Portal sign-in options")
async def portal_auth_methods(settings: AppSettings, session: DbSession) -> PortalAuthMethodsOut:
    """Discovery endpoint of the portal login page (customer-audience providers only)."""
    return PortalAuthMethodsOut(
        password=True,
        providers=await login_providers(session, settings, "customer"),
        webauthn=False,
    )


@router.get("/sso/{slug}/login", response_model=None, summary="Start a portal SSO login")
async def portal_sso_login(
    slug: str,
    request: Request,
    settings: AppSettings,
    session: DbSession,
    next: Annotated[str | None, Query()] = None,
) -> Response:
    return await start_sso_login(
        request,
        slug=slug,
        user_kind="customer",
        next_path=next,
        test_id=None,  # admin test logins are agent-only
        session=session,
        settings=settings,
        current_admin_id=None,
    )


@router.get(
    "/oidc/{slug}/callback", response_model=None, summary="OIDC callback of a portal provider"
)
async def portal_oidc_callback(
    slug: str,
    request: Request,
    settings: AppSettings,
    session: DbSession,
    code: str | None = None,
    state: str | None = None,
) -> Response:
    return await finish_oidc_callback(
        request,
        slug=slug,
        user_kind="customer",
        code=code,
        state=state,
        session=session,
        settings=settings,
    )


def _form_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


@router.post(
    "/saml/{slug}/acs", response_model=None, summary="SAML assertion consumer service (portal)"
)
async def portal_saml_acs(
    slug: str, request: Request, settings: AppSettings, session: DbSession
) -> Response:
    """The IdP's browser POST; CSRF-exempt by design, authenticated by signature, stored request
    id and the per-browser cookie. Every failure redirects to ``/portal/login``."""
    portal_home = "/portal"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES * 2:
        return login_error_redirect("customer", "saml_invalid", portal_home)
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - malformed/oversized form bodies are a failed login
        return login_error_redirect("customer", "saml_invalid", portal_home)
    saml_response = _form_text(form.get("SAMLResponse"))
    if not saml_response:
        return login_error_redirect("customer", "saml_invalid", portal_home)
    return await finish_saml_acs(
        request,
        slug=slug,
        user_kind="customer",
        saml_response=saml_response,
        relay_state=_form_text(form.get("RelayState")),
        session=session,
        settings=settings,
    )


@router.post(
    "/sso/logout",
    response_model=None,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Log out of the portal and, where supported, of the identity provider",
)
async def portal_sso_logout(
    request: Request, settings: AppSettings, session: DbSession
) -> Response:
    return await sso_logout(request, user_kind="customer", session=session, settings=settings)
