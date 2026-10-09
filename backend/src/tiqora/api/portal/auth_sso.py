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

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.domain.schemas import PortalAuthMethodsOut
from tiqora.identity.login_providers import login_providers
from tiqora.identity.sso_routes import (
    finish_oidc_callback,
    handle_saml_acs_post,
    resolve_admin_id,
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
    test: Annotated[str | None, Query()] = None,
) -> Response:
    """Starts a customer login, or (``test=``) an admin's test run of a customer provider.

    A test run needs the admin's AGENT session cookie; without one the answer is 403 and
    never a real login. The run itself never creates a session (see ``finish_*``).
    """
    admin_id: int | None = None
    if test:
        admin_id = await resolve_admin_id(request, session, settings)
        if admin_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin session required for a test")
    return await start_sso_login(
        request,
        slug=slug,
        user_kind="customer",
        next_path=next,
        test_id=test,
        session=session,
        settings=settings,
        current_admin_id=admin_id,
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


@router.post(
    "/saml/{slug}/acs", response_model=None, summary="SAML assertion consumer service (portal)"
)
async def portal_saml_acs(
    slug: str, request: Request, settings: AppSettings, session: DbSession
) -> Response:
    """The IdP's browser POST; CSRF-exempt by design. Failures redirect to ``/portal/login``."""
    return await handle_saml_acs_post(
        request, slug=slug, user_kind="customer", session=session, settings=settings
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
