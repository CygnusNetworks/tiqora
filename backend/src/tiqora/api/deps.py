"""FastAPI dependencies: DB session, Redis, auth, current user."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.auth import AuthenticatedUser, AuthService, SessionStore
from tiqora.domain.totp import TOTPService

_redis_client: redis.Redis | None = None


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


async def get_db(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_redis(request: Request) -> redis.Redis:
    global _redis_client
    client = getattr(request.app.state, "redis", None)
    if client is not None:
        return client  # type: ignore[no-any-return]
    if _redis_client is None:
        settings = get_app_settings(request)
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def get_session_store(
    request: Request,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> SessionStore:
    settings = get_app_settings(request)
    return SessionStore(redis_client, settings)


async def get_auth_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    sessions: Annotated[SessionStore, Depends(get_session_store)],
) -> AuthService:
    settings = get_app_settings(request)
    return AuthService(session, sessions, settings)


async def get_current_user(
    request: Request,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header()] = None,
    tiqora_session: Annotated[str | None, Cookie(alias="tiqora_session")] = None,
) -> AuthenticatedUser:
    """Resolve agent via session cookie or ``Authorization: Bearer`` API key.

    The auth lookups read the DB on the *shared* request session, which
    autobegins a transaction. We roll it back before returning so endpoints can
    open their own ``async with session.begin()`` without hitting
    "A transaction is already begun on this Session" (the lookups are
    read-only, so nothing is lost).

    On a successful resolve we also refresh the global online-presence key
    (``tiqora:online:<user_id>``) — best-effort and non-fatal if Redis is down.
    """
    # Cookie name may be customised via settings
    cookie_token = tiqora_session
    if cookie_token is None:
        cookie_token = request.cookies.get(settings.session_cookie_name)

    resolved: AuthenticatedUser | None = None
    token_for_state: str | None = None

    if cookie_token:
        resolved = await auth.resolve_session(cookie_token)
        if resolved is not None:
            token_for_state = cookie_token

    if resolved is None and authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
        if raw:
            resolved = await auth.resolve_api_key(raw)
            if resolved is None:
                # Also accept session token as bearer (MCP / CLI convenience)
                resolved = await auth.resolve_session(raw)
                if resolved is not None:
                    token_for_state = raw

    # Discard the read-only transaction the auth lookups opened on the shared
    # request session so downstream endpoints start with a clean session.
    await session.rollback()

    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token_for_state is not None:
        request.state.session_token = token_for_state

    # Sliding global presence: any authenticated request counts as activity.
    # Import lazily to avoid a circular import with the agents router module.
    from tiqora.api.v1.agents import touch_online_presence

    await touch_online_presence(redis_client, resolved)
    return resolved


async def get_totp_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TOTPService:
    return TOTPService(session, settings)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
TOTPServiceDep = Annotated[TOTPService, Depends(get_totp_service)]
