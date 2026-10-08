"""Generic agent SSO routes: one start and one OIDC callback per provider slug (D15)."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from starlette.datastructures import UploadFile

from tiqora.api.deps import AppSettings, DbSession
from tiqora.identity.http import login_error_redirect
from tiqora.identity.saml_runtime import saml_availability
from tiqora.identity.sso_routes import (
    finish_oidc_callback,
    finish_saml_acs,
    resolve_admin_id,
    saml_metadata_response,
    start_sso_login,
)
from tiqora.identity.store import get_provider_by_slug

MAX_ACS_BODY = 4 * 1024 * 1024

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/sso/{slug}/login", response_model=None, summary="Start an SSO login")
async def sso_login(
    slug: str,
    request: Request,
    settings: AppSettings,
    session: DbSession,
    next: Annotated[str | None, Query()] = None,
    test: Annotated[str | None, Query()] = None,
) -> Response:
    admin_id = await resolve_admin_id(request, session, settings) if test else None
    return await start_sso_login(
        request,
        slug=slug,
        user_kind="agent",
        next_path=next,
        test_id=test,
        session=session,
        settings=settings,
        current_admin_id=admin_id,
    )


@router.get("/oidc/{slug}/callback", response_model=None, summary="OIDC callback of a provider")
async def oidc_provider_callback(
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
        user_kind="agent",
        code=code,
        state=state,
        session=session,
        settings=settings,
    )


def _form_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


@router.post("/saml/{slug}/acs", response_model=None, summary="SAML assertion consumer service")
async def saml_acs(
    slug: str,
    request: Request,
    settings: AppSettings,
    session: DbSession,
) -> Response:
    """Receives the IdP's browser POST (``SAMLResponse``, ``RelayState``).

    CSRF-exempt by design (cross-site form POST); the XML signature, the stored request id
    and the per-browser cookie authenticate it. Every failure redirects to ``/login``.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_ACS_BODY:
        return login_error_redirect("agent", "saml_invalid", "/")
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - malformed/oversized form bodies are a failed login
        return login_error_redirect("agent", "saml_invalid", "/")
    saml_response = _form_text(form.get("SAMLResponse"))
    if not saml_response or isinstance(form.get("SAMLResponse"), UploadFile):
        return login_error_redirect("agent", "saml_invalid", "/")
    return await finish_saml_acs(
        request,
        slug=slug,
        user_kind="agent",
        saml_response=saml_response,
        relay_state=_form_text(form.get("RelayState")),
        session=session,
        settings=settings,
    )


@router.get(
    "/saml/{slug}/metadata",
    response_model=None,
    summary="SAML service provider metadata",
    responses={200: {"content": {"application/samlmetadata+xml": {}}}},
)
async def saml_metadata(slug: str, settings: AppSettings, session: DbSession) -> Response:
    if not saml_availability().available:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SAML unavailable")
    record = await get_provider_by_slug(session, slug, secret_key=settings.secret_key)
    if record is None or record.kind != "saml":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")
    # pysaml2 builds (and may shell out) on first use: never on the event loop.
    return await asyncio.get_running_loop().run_in_executor(
        None, saml_metadata_response, record, settings
    )
