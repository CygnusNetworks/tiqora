"""Auth endpoints: login, me, logout."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tiqora.api.deps import AppSettings, CurrentUser, get_auth_service
from tiqora.domain.auth import AuthService, user_to_dict
from tiqora.domain.schemas import LoginRequest, LoginResponse, UserMe

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    settings: AppSettings,
) -> LoginResponse:
    user = await auth.authenticate_password(body.login, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token = await auth.create_session(user)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return LoginResponse(user=UserMe(**user_to_dict(user)))


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser) -> UserMe:
    return UserMe(**user_to_dict(user))


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
