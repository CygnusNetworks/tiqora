"""Customer portal auth endpoints: login, me, logout (mirrors api/v1/auth.py)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tiqora.api.portal.deps import AppSettings, CurrentCustomer, get_customer_auth_service
from tiqora.domain.customer_auth import CustomerAuthService, customer_to_dict
from tiqora.domain.customer_auth_ldap import CustomerLdapAuthService
from tiqora.domain.schemas import CustomerLoginResponse, CustomerMe, LoginRequest

router = APIRouter(prefix="/auth", tags=["portal-auth"])


@router.post("/login", response_model=CustomerLoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    settings: AppSettings,
) -> CustomerLoginResponse:
    customer = await auth.authenticate_password(body.login, body.password)
    if customer is None and settings.customer_ldap_enabled:
        # LDAP fallback, mirroring Kernel::System::CustomerAuth::LDAP. No
        # auto-provisioning in v1: the resolved LDAP UID must match an
        # existing, valid `customer_user.login` row.
        ldap_service = CustomerLdapAuthService(settings)
        ldap_uid = await ldap_service.authenticate(body.login, body.password)
        if ldap_uid is not None:
            customer = await auth.get_customer_by_login(ldap_uid)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token = await auth.create_session(customer)
    response.set_cookie(
        key=settings.customer_session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return CustomerLoginResponse(customer=CustomerMe(**customer_to_dict(customer)))


@router.get("/me", response_model=CustomerMe)
async def me(customer: CurrentCustomer) -> CustomerMe:
    return CustomerMe(**customer_to_dict(customer))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    auth: Annotated[CustomerAuthService, Depends(get_customer_auth_service)],
    settings: AppSettings,
) -> Response:
    token = getattr(request.state, "customer_session_token", None) or request.cookies.get(
        settings.customer_session_cookie_name
    )
    if token:
        await auth.logout(token)
    response.delete_cookie(
        key=settings.customer_session_cookie_name,
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
