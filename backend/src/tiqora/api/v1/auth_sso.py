"""Generic agent SSO routes: one start and one OIDC callback per provider slug (D15)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from tiqora.api.deps import AppSettings, DbSession
from tiqora.identity.sso_routes import finish_oidc_callback, resolve_admin_id, start_sso_login

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
