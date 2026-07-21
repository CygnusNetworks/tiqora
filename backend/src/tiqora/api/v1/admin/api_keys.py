"""Admin CRUD for Tiqora API keys (Bearer authentication)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, paginate
from tiqora.api.v1.admin.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, ApiKeyUpdate
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.domain.auth import generate_api_key, hash_api_key

router = APIRouter(prefix="/api-keys", tags=["admin:api-keys"])


def _naive_utc(value: datetime | None) -> datetime | None:
    """Store DateTime columns as naive UTC (matches ``_utcnow`` / server default)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


@router.get("", response_model=Page[ApiKeyOut])
async def list_api_keys(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[ApiKeyOut]:
    _ = admin
    stmt = select(TiqoraApiKey)
    if params.valid == "valid":
        stmt = stmt.where(TiqoraApiKey.valid.is_(True))
    elif params.valid == "invalid":
        stmt = stmt.where(TiqoraApiKey.valid.is_(False))
    stmt = stmt.order_by(TiqoraApiKey.created.desc())
    return await paginate(session, ApiKeyOut, stmt, params)


@router.get("/{key_id}", response_model=ApiKeyOut)
async def get_api_key(key_id: int, admin: AdminUser, session: DbSession) -> ApiKeyOut:
    _ = admin
    row = await session.get(TiqoraApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    return ApiKeyOut.model_validate(row)


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(body: ApiKeyCreate, admin: AdminUser, session: DbSession) -> ApiKeyCreated:
    user_result = await session.execute(
        select(Users).where(Users.id == body.user_id, Users.valid_id == 1)
    )
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="target user not found or invalid",
        )
    raw = generate_api_key()
    ts = now()
    row = TiqoraApiKey(
        name=body.name,
        key_hash=hash_api_key(raw),
        user_id=body.user_id,
        valid=True,
        created=ts,
        expires_at=_naive_utc(body.expires_at),
        created_by=admin.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ApiKeyCreated(
        id=row.id,
        name=row.name,
        user_id=row.user_id,
        valid=row.valid,
        created=row.created,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_by=row.created_by,
        key=raw,
    )


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def update_api_key(
    key_id: int, body: ApiKeyUpdate, admin: AdminUser, session: DbSession
) -> ApiKeyOut:
    _ = admin
    row = await session.get(TiqoraApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    updates = body.model_dump(exclude_unset=True)
    if "expires_at" in updates:
        updates["expires_at"] = _naive_utc(updates["expires_at"])
    for field, value in updates.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return ApiKeyOut.model_validate(row)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(key_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete the API-key row. Use PATCH valid=false to revoke instead."""
    _ = admin
    row = await session.get(TiqoraApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    await session.delete(row)
    await session.commit()
