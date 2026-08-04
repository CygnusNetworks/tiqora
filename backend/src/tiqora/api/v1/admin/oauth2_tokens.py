"""Admin CRUD for Znuny-compatible OAuth2 mail token configs.

Mounted at ``/api/v1/admin/oauth2-token-configs``. Tokens live in the shared
legacy tables ``oauth2_token_config`` / ``oauth2_token`` so Znuny and Tiqora
can operate in parallel against the same records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, window
from tiqora.config import get_settings
from tiqora.db.legacy.oauth2 import OAuth2TokenConfig
from tiqora.domain.oauth2_mail import (
    OAuth2MailError,
    OAuth2NotAvailableError,
    build_authorization_url,
    create_config,
    delete_config,
    ensure_oauth2_available,
    get_config,
    get_redirect_uri,
    get_token_row,
    list_provider_templates,
    parse_config_blob,
    public_config_view,
    request_token_by_refresh_token,
    state_for_config_id,
    update_config,
)

router = APIRouter(prefix="/oauth2-token-configs", tags=["admin:oauth2"])


class OAuth2TokenConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    config: dict[str, Any]
    client_id: str = ""
    has_client_secret: bool = False
    scope: str = ""
    valid: bool
    token_status: str
    token_expiration_date: datetime | None = None
    refresh_token_expiration_date: datetime | None = None
    has_token: bool = False
    has_refresh_token: bool = False
    error_message: str = ""
    create_time: datetime | None = None
    create_by: int | None = None
    change_time: datetime | None = None
    change_by: int | None = None
    redirect_uri: str = ""


class OAuth2TokenConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    config: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True
    # Convenience fields merged into config when set.
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    template_id: str | None = None


class OAuth2TokenConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=250)
    config: dict[str, Any] | None = None
    valid: bool | None = None
    client_id: str | None = None
    client_secret: str | None = None  # write-only; empty keeps stored secret
    scope: str | None = None


class AuthorizeUrlOut(BaseModel):
    url: str
    redirect_uri: str
    state: str


class ProviderTemplateOut(BaseModel):
    id: str
    name: str
    config: dict[str, Any]


def _raise_unavailable(exc: OAuth2NotAvailableError) -> None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _to_out(row: OAuth2TokenConfig, token: object | None) -> OAuth2TokenConfigOut:
    return OAuth2TokenConfigOut.model_validate(
        public_config_view(row, token, include_secret=False)  # type: ignore[arg-type]
    )


def _merge_convenience(
    config: dict[str, Any],
    *,
    client_id: str | None,
    client_secret: str | None,
    scope: str | None,
) -> dict[str, Any]:
    out = dict(config)
    if client_id is not None:
        out["ClientID"] = client_id
    if client_secret is not None and client_secret != "":
        out["ClientSecret"] = client_secret
    if scope is not None:
        out["Scope"] = scope
    return out


def _config_from_template(template_id: str | None) -> dict[str, Any]:
    if not template_id:
        return {}
    for tpl in list_provider_templates():
        if tpl["id"] == template_id:
            return dict(tpl["config"])
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"unknown template_id {template_id!r}",
    )


@router.get("/templates", response_model=list[ProviderTemplateOut])
async def get_templates(admin: AdminUser) -> list[ProviderTemplateOut]:
    _ = admin
    return [
        ProviderTemplateOut(id=t["id"], name=t["name"], config=t["config"])
        for t in list_provider_templates()
    ]


@router.get("", response_model=Page[OAuth2TokenConfigOut])
async def list_token_configs(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[OAuth2TokenConfigOut]:
    _ = admin
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)

    stmt = select(OAuth2TokenConfig).order_by(OAuth2TokenConfig.name)
    if params.valid == "valid":
        stmt = stmt.where(OAuth2TokenConfig.valid_id == 1)
    elif params.valid == "invalid":
        stmt = stmt.where(OAuth2TokenConfig.valid_id != 1)
    rows, total = await window(session, stmt, params)
    items: list[OAuth2TokenConfigOut] = []
    for row in rows:
        token = await get_token_row(session, row.id)
        items.append(_to_out(row, token))
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/{config_id}", response_model=OAuth2TokenConfigOut)
async def get_token_config(
    config_id: int, admin: AdminUser, session: DbSession
) -> OAuth2TokenConfigOut:
    _ = admin
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    token = await get_token_row(session, row.id)
    return _to_out(row, token)


@router.post("", response_model=OAuth2TokenConfigOut, status_code=status.HTTP_201_CREATED)
async def create_token_config(
    body: OAuth2TokenConfigCreate, admin: AdminUser, session: DbSession
) -> OAuth2TokenConfigOut:
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)

    base = body.config if body.config else _config_from_template(body.template_id)
    if body.template_id and body.config:
        # Explicit config wins over template; template only when config empty.
        base = body.config
    elif body.template_id and not body.config:
        base = _config_from_template(body.template_id)
    config = _merge_convenience(
        base,
        client_id=body.client_id,
        client_secret=body.client_secret,
        scope=body.scope,
    )
    try:
        row = await create_config(
            session,
            name=body.name,
            config=config,
            user_id=admin.id,
            valid=body.valid,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="name already exists"
        ) from exc
    token = await get_token_row(session, row.id)
    return _to_out(row, token)


@router.patch("/{config_id}", response_model=OAuth2TokenConfigOut)
async def patch_token_config(
    config_id: int,
    body: OAuth2TokenConfigUpdate,
    admin: AdminUser,
    session: DbSession,
) -> OAuth2TokenConfigOut:
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    config = body.config
    if config is not None or body.client_id is not None or body.scope is not None:
        base = config if config is not None else parse_config_blob(row.config)
        config = _merge_convenience(
            base,
            client_id=body.client_id,
            client_secret=None,  # handled separately
            scope=body.scope,
        )
    try:
        row = await update_config(
            session,
            row,
            user_id=admin.id,
            name=body.name,
            config=config,
            valid=body.valid,
            client_secret=body.client_secret,
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="name already exists"
        ) from exc
    token = await get_token_row(session, row.id)
    return _to_out(row, token)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_token_config(
    config_id: int, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Znuny hard-deletes; keep that for parallel-op parity.
    await delete_config(session, row, hard=True)


@router.get("/{config_id}/authorize-url", response_model=AuthorizeUrlOut)
async def authorize_url(
    config_id: int, admin: AdminUser, session: DbSession
) -> AuthorizeUrlOut:
    _ = admin
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    settings = get_settings()
    try:
        url = build_authorization_url(row, settings=settings)
    except OAuth2MailError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return AuthorizeUrlOut(
        url=url,
        redirect_uri=get_redirect_uri(settings),
        state=state_for_config_id(row.id),
    )


@router.post("/{config_id}/refresh", response_model=OAuth2TokenConfigOut)
async def refresh_token(
    config_id: int, admin: AdminUser, session: DbSession
) -> OAuth2TokenConfigOut:
    try:
        ensure_oauth2_available()
    except OAuth2NotAvailableError as exc:
        _raise_unavailable(exc)
    row = await get_config(session, config_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        await request_token_by_refresh_token(
            session, config_id=config_id, user_id=admin.id, settings=get_settings()
        )
    except OAuth2MailError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    token = await get_token_row(session, row.id)
    return _to_out(row, token)
