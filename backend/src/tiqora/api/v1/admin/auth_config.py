"""Admin API for per-agent SSO eligibility + 2FA enforcement / reset."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page
from tiqora.api.v1.admin.schemas import (
    AuthConfigAgentOut,
    AuthConfigGlobalOut,
    AuthConfigGlobalUpdate,
    AuthConfigUpdate,
)
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraUserAuthConfig, TiqoraUserTotp
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.settings_store import KEY_TOTP_ENFORCE_ALL, get_setting_bool, set_setting
from tiqora.domain.totp import TOTPService

router = APIRouter(prefix="/auth-config", tags=["admin:auth-config"])


@router.get("/global", response_model=AuthConfigGlobalOut)
async def get_global_auth_config(admin: AdminUser, session: DbSession) -> AuthConfigGlobalOut:
    _ = admin
    enforce_all = await get_setting_bool(session, KEY_TOTP_ENFORCE_ALL, default=False)
    return AuthConfigGlobalOut(enforce_all=enforce_all)


@router.put("/global", response_model=AuthConfigGlobalOut)
async def put_global_auth_config(
    body: AuthConfigGlobalUpdate, admin: AdminUser, session: DbSession
) -> AuthConfigGlobalOut:
    _ = admin
    await set_setting(session, KEY_TOTP_ENFORCE_ALL, "1" if body.enforce_all else "0")
    return AuthConfigGlobalOut(enforce_all=body.enforce_all)


@router.get("", response_model=Page[AuthConfigAgentOut])
async def list_auth_config(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[AuthConfigAgentOut]:
    """Paginated agents with TOTP + SSO/enforce flags (valid users only by default)."""
    _ = admin
    stmt = (
        select(
            Users.id,
            Users.login,
            Users.first_name,
            Users.last_name,
            TiqoraUserTotp.enabled,
            TiqoraUserAuthConfig.sso_eligible,
            TiqoraUserAuthConfig.enforce_2fa,
        )
        .outerjoin(TiqoraUserTotp, TiqoraUserTotp.user_id == Users.id)
        .outerjoin(TiqoraUserAuthConfig, TiqoraUserAuthConfig.user_id == Users.id)
    )
    if params.valid == "valid":
        stmt = stmt.where(Users.valid_id == 1)
    elif params.valid == "invalid":
        stmt = stmt.where(Users.valid_id != 1)
    stmt = stmt.order_by(Users.login)

    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt) or 0
    windowed = stmt.limit(params.page_size).offset((params.page - 1) * params.page_size)
    rows = (await session.execute(windowed)).all()

    items: list[AuthConfigAgentOut] = []
    for user_id, login, first_name, last_name, totp_enabled, sso_eligible, enforce_2fa in rows:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or login
        items.append(
            AuthConfigAgentOut(
                user_id=int(user_id),
                login=login,
                full_name=full_name,
                totp_enabled=bool(totp_enabled),
                sso_eligible=bool(sso_eligible),
                enforce_2fa=bool(enforce_2fa),
            )
        )
    return Page(items=items, total=int(total), page=params.page, page_size=params.page_size)


@router.put("/{user_id}", response_model=AuthConfigAgentOut)
async def update_auth_config(
    user_id: int, body: AuthConfigUpdate, admin: AdminUser, session: DbSession
) -> AuthConfigAgentOut:
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if body.sso_eligible is None and body.enforce_2fa is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at least one of sso_eligible, enforce_2fa required",
        )
    svc = AuthConfigService(session)
    cfg = await svc.set(
        user_id,
        sso_eligible=body.sso_eligible,
        enforce_2fa=body.enforce_2fa,
    )
    totp_row = (
        await session.execute(select(TiqoraUserTotp).where(TiqoraUserTotp.user_id == user_id))
    ).scalar_one_or_none()
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.login
    return AuthConfigAgentOut(
        user_id=user.id,
        login=user.login,
        full_name=full_name,
        totp_enabled=bool(totp_row is not None and totp_row.enabled),
        sso_eligible=cfg.sso_eligible,
        enforce_2fa=cfg.enforce_2fa,
    )


@router.post("/{user_id}/reset-2fa", status_code=status.HTTP_204_NO_CONTENT)
async def reset_2fa(
    user_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Admin force-disable: delete ``tiqora_user_totp`` without a TOTP code."""
    _ = admin
    user = await session.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    totp = TOTPService(session, settings)
    await totp.force_disable(user_id)
