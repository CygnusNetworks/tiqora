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
    authorization: Annotated[str | None, Header()] = None,
    tiqora_session: Annotated[str | None, Cookie(alias="tiqora_session")] = None,
) -> AuthenticatedUser:
    """Resolve agent via session cookie or ``Authorization: Bearer`` API key."""
    # Cookie name may be customised via settings
    cookie_token = tiqora_session
    if cookie_token is None:
        cookie_token = request.cookies.get(settings.session_cookie_name)

    if cookie_token:
        user = await auth.resolve_session(cookie_token)
        if user is not None:
            request.state.session_token = cookie_token
            return user

    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
        if raw:
            user = await auth.resolve_api_key(raw)
            if user is not None:
                return user
            # Also accept session token as bearer (MCP / CLI convenience)
            user = await auth.resolve_session(raw)
            if user is not None:
                request.state.session_token = raw
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
