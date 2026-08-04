"""Admin CRUD for Znuny Ticket ACLs (``acl`` table).

YAML ``config_match`` / ``config_change`` are stored as text 1:1 with the DB.
Runtime evaluation lives in :mod:`tiqora.domain.ticket_acl`.

Znuny ``ACLDelete`` hard-deletes the row (and writes ``acl_sync``); we hard-delete
here for 1:1 compatibility rather than the soft ``valid_id=2`` used for other
master-data resources.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import ACL_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import AclCreate, AclOut, AclUpdate
from tiqora.db.legacy.config import Acl

router = APIRouter(prefix="/acl", tags=["admin:acl"])


@router.get("", response_model=list[AclOut])
async def list_acls(admin: AdminUser, session: DbSession) -> list[Acl]:
    _ = admin
    result = await session.execute(select(Acl).order_by(Acl.name))
    return list(result.scalars().all())


@router.get("/{acl_id}", response_model=AclOut)
async def get_acl(acl_id: int, admin: AdminUser, session: DbSession) -> Acl:
    _ = admin
    row = await session.get(Acl, acl_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    return row


@router.post("", response_model=AclOut, status_code=status.HTTP_201_CREATED)
async def create_acl(body: AclCreate, admin: AdminUser, session: DbSession) -> Acl:
    ts = now()
    row = Acl(
        name=body.name,
        comments=body.comments,
        description=body.description,
        valid_id=body.valid_id,
        stop_after_match=body.stop_after_match if body.stop_after_match is not None else 0,
        config_match=body.config_match,
        config_change=body.config_change,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, ACL_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.put("/{acl_id}", response_model=AclOut)
@router.patch("/{acl_id}", response_model=AclOut)
async def update_acl(acl_id: int, body: AclUpdate, admin: AdminUser, session: DbSession) -> Acl:
    row = await session.get(Acl, acl_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, ACL_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_acl(acl_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete the ACL row (Znuny ``ACLDelete`` semantics)."""
    _ = admin
    row = await session.get(Acl, acl_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    await session.delete(row)
    await invalidate_znuny_cache_types(session, ACL_CACHE_TYPES)
    await session.commit()
