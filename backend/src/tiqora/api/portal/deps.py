"""FastAPI dependencies for the customer portal (mirrors ``api/deps.py``)."""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession, get_app_settings, get_db, get_redis
from tiqora.config import Settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.customer_auth import (
    AuthenticatedCustomer,
    CustomerAuthService,
    CustomerSessionStore,
)
from tiqora.domain.portal_gate import portal_enabled
from tiqora.domain.portal_ticket_service import PortalTicketService
from tiqora.znuny.sysconfig import SysConfig


async def get_customer_session_store(
    request: Request,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> CustomerSessionStore:
    settings = get_app_settings(request)
    return CustomerSessionStore(redis_client, settings)


async def get_customer_auth_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[CustomerSessionStore, Depends(get_customer_session_store)],
) -> CustomerAuthService:
    settings = get_app_settings(request)
    return CustomerAuthService(session, sessions, settings)


async def get_current_customer(
    request: Request,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    tiqora_customer_session: Annotated[str | None, Cookie(alias="tiqora_customer_session")] = None,
) -> AuthenticatedCustomer:
    """Resolve the customer via the portal session cookie only (no API keys)."""
    cookie_token = tiqora_customer_session
    if cookie_token is None:
        cookie_token = request.cookies.get(settings.customer_session_cookie_name)

    if cookie_token:
        customer = await auth.resolve_session(cookie_token)
        if customer is not None:
            request.state.customer_session_token = cookie_token
            return customer

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def get_portal_ticket_service(
    session: DbSession,
) -> PortalTicketService:
    factory = get_session_factory()
    sysconfig = SysConfig(session)
    return PortalTicketService(session, factory, sysconfig)


CurrentCustomer = Annotated[AuthenticatedCustomer, Depends(get_current_customer)]
PortalService = Annotated[PortalTicketService, Depends(get_portal_ticket_service)]


async def require_portal_enabled(session: DbSession, settings: AppSettings) -> None:
    """Router dependency: hide the whole portal API while the portal is off.

    404 rather than 403 — a disabled portal does not advertise its existence.
    """
    if not await portal_enabled(session, settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


__all__ = [
    "AppSettings",
    "CurrentCustomer",
    "DbSession",
    "PortalService",
    "get_current_customer",
    "get_customer_auth_service",
    "get_customer_session_store",
    "get_portal_ticket_service",
    "require_portal_enabled",
]
