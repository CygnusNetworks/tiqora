"""Admin CRUD for Znuny ``mail_account`` (incoming IMAP/POP mailboxes).

Supports password and oauth2_token authentication when the schema profile
exposes those columns (Znuny 6.3+). Writes stay Znuny-compatible for
parallel operation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, window
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.db.legacy.profile import get_legacy_schema_profile, mail_account_load_options
from tiqora.domain.oauth2_mail import get_config

router = APIRouter(prefix="/mail-accounts", tags=["admin:mail-accounts"])

AccountType = Literal["IMAP", "IMAPS", "POP3", "POP3S"]
AuthType = Literal["password", "oauth2_token"]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _schema_has_oauth() -> bool:
    profile = get_legacy_schema_profile()
    return bool(profile and profile.mail_account_has_oauth)


class MailAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    host: str
    account_type: str
    queue_id: int
    trusted: bool
    imap_folder: str | None = None
    authentication_type: str = "password"
    oauth2_token_config_id: int | None = None
    comments: str | None = None
    valid: bool
    has_password: bool = False
    create_time: datetime | None = None
    change_time: datetime | None = None


class MailAccountCreate(BaseModel):
    login: str = Field(min_length=1, max_length=200)
    pw: str | None = None
    host: str = Field(min_length=1, max_length=200)
    account_type: AccountType = "IMAPS"
    queue_id: int
    trusted: bool = False
    imap_folder: str | None = Field(default="INBOX", max_length=250)
    authentication_type: AuthType = "password"
    oauth2_token_config_id: int | None = None
    comments: str | None = Field(default=None, max_length=250)
    valid: bool = True


class MailAccountUpdate(BaseModel):
    login: str | None = Field(default=None, min_length=1, max_length=200)
    pw: str | None = None  # write-only; empty/omit keeps stored
    host: str | None = Field(default=None, min_length=1, max_length=200)
    account_type: AccountType | None = None
    queue_id: int | None = None
    trusted: bool | None = None
    imap_folder: str | None = Field(default=None, max_length=250)
    authentication_type: AuthType | None = None
    oauth2_token_config_id: int | None = None
    comments: str | None = Field(default=None, max_length=250)
    valid: bool | None = None


def _to_out(row: MailAccount) -> MailAccountOut:
    auth = getattr(row, "authentication_type", None) or "password"
    oauth_id = getattr(row, "oauth2_token_config_id", None)
    return MailAccountOut(
        id=row.id,
        login=row.login,
        host=row.host,
        account_type=row.account_type,
        queue_id=row.queue_id,
        trusted=bool(row.trusted),
        imap_folder=row.imap_folder,
        authentication_type=str(auth),
        oauth2_token_config_id=oauth_id,
        comments=row.comments,
        valid=row.valid_id == 1,
        has_password=bool(row.pw),
        create_time=row.create_time,
        change_time=row.change_time,
    )


async def _validate_auth(
    session: DbSession,
    *,
    authentication_type: str,
    oauth2_token_config_id: int | None,
    pw: str | None,
    is_create: bool,
) -> None:
    if authentication_type == "oauth2_token":
        if not _schema_has_oauth():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="oauth2_token auth requires Znuny 6.3+ schema",
            )
        if not oauth2_token_config_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="oauth2_token_config_id is required for oauth2_token auth",
            )
        cfg = await get_config(session, int(oauth2_token_config_id))
        if cfg is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="oauth2_token_config_id not found",
            )
    elif authentication_type == "password":
        if is_create and not pw:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="password is required for password auth",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported authentication_type {authentication_type!r}",
        )


@router.get("", response_model=Page[MailAccountOut])
async def list_mail_accounts(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[MailAccountOut]:
    _ = admin
    stmt = select(MailAccount).options(*mail_account_load_options()).order_by(MailAccount.id)
    if params.valid == "valid":
        stmt = stmt.where(MailAccount.valid_id == 1)
    elif params.valid == "invalid":
        stmt = stmt.where(MailAccount.valid_id != 1)
    rows, total = await window(session, stmt, params)
    return Page(
        items=[_to_out(r) for r in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/{account_id}", response_model=MailAccountOut)
async def get_mail_account(
    account_id: int, admin: AdminUser, session: DbSession
) -> MailAccountOut:
    _ = admin
    row = (
        await session.execute(
            select(MailAccount)
            .where(MailAccount.id == account_id)
            .options(*mail_account_load_options())
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _to_out(row)


@router.post("", response_model=MailAccountOut, status_code=status.HTTP_201_CREATED)
async def create_mail_account(
    body: MailAccountCreate, admin: AdminUser, session: DbSession
) -> MailAccountOut:
    await _validate_auth(
        session,
        authentication_type=body.authentication_type,
        oauth2_token_config_id=body.oauth2_token_config_id,
        pw=body.pw,
        is_create=True,
    )
    now = _utcnow()
    row = MailAccount(
        login=body.login,
        pw=body.pw or "",
        host=body.host,
        account_type=body.account_type,
        queue_id=body.queue_id,
        trusted=1 if body.trusted else 0,
        imap_folder=body.imap_folder,
        comments=body.comments,
        valid_id=1 if body.valid else 2,
        create_time=now,
        create_by=admin.id,
        change_time=now,
        change_by=admin.id,
    )
    if _schema_has_oauth():
        row.authentication_type = body.authentication_type
        row.oauth2_token_config_id = (
            body.oauth2_token_config_id if body.authentication_type == "oauth2_token" else None
        )
    elif body.authentication_type != "password":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="oauth2_token auth requires Znuny 6.3+ schema",
        )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.patch("/{account_id}", response_model=MailAccountOut)
async def update_mail_account(
    account_id: int,
    body: MailAccountUpdate,
    admin: AdminUser,
    session: DbSession,
) -> MailAccountOut:
    row = (
        await session.execute(
            select(MailAccount)
            .where(MailAccount.id == account_id)
            .options(*mail_account_load_options())
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    auth_type = body.authentication_type or (
        getattr(row, "authentication_type", None) or "password"
    )
    oauth_id = (
        body.oauth2_token_config_id
        if body.oauth2_token_config_id is not None
        or body.authentication_type is not None
        else getattr(row, "oauth2_token_config_id", None)
    )
    if body.authentication_type == "password":
        oauth_id = None
    await _validate_auth(
        session,
        authentication_type=auth_type,
        oauth2_token_config_id=oauth_id,
        pw=body.pw if body.pw is not None else row.pw,
        is_create=False,
    )

    if body.login is not None:
        row.login = body.login
    if body.pw is not None and body.pw != "":
        row.pw = body.pw
    if body.host is not None:
        row.host = body.host
    if body.account_type is not None:
        row.account_type = body.account_type
    if body.queue_id is not None:
        row.queue_id = body.queue_id
    if body.trusted is not None:
        row.trusted = 1 if body.trusted else 0
    if body.imap_folder is not None:
        row.imap_folder = body.imap_folder
    if body.comments is not None:
        row.comments = body.comments
    if body.valid is not None:
        row.valid_id = 1 if body.valid else 2
    if _schema_has_oauth():
        if body.authentication_type is not None:
            row.authentication_type = body.authentication_type
        if body.authentication_type == "password":
            row.oauth2_token_config_id = None
        elif body.oauth2_token_config_id is not None or body.authentication_type == "oauth2_token":
            row.oauth2_token_config_id = oauth_id

    row.change_time = _utcnow()
    row.change_by = admin.id
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_mail_account(
    account_id: int, admin: AdminUser, session: DbSession
) -> None:
    """Soft-deactivate (valid_id=2); matches other admin resources."""
    _ = admin
    row = await session.get(MailAccount, account_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    row.valid_id = 2
    row.change_time = _utcnow()
    row.change_by = admin.id
    await session.commit()
