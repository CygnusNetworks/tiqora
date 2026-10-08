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
from tiqora.deployed.sync_tables import mark_acl_changed


def _stored(field: str, value: object) -> object:
    """Normalise like ACLUpdate's comparison (``|| ''`` / ``|| 0``)."""
    if field in ("comments", "description"):
        return value or ""
    if field == "stop_after_match":
        return value or 0
    return value


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
    await session.flush()  # the id is needed for acl_sync
    await mark_acl_changed(session, acl_id=row.id, sync_state="not_sync")
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
    updates = body.model_dump(exclude_unset=True)
    # ACLUpdate returns early when nothing stored changed (ACL/DB/ACL.pm:530-541):
    # no change_time bump, no acl_sync row, no cache invalidation.
    if not any(
        _stored(field, getattr(row, field)) != _stored(field, v) for field, v in updates.items()
    ):
        return row
    for field, value in updates.items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await mark_acl_changed(session, acl_id=row.id, sync_state="not_sync")
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
    await mark_acl_changed(session, acl_id=acl_id, sync_state="deleted")
    await invalidate_znuny_cache_types(session, ACL_CACHE_TYPES)
    await session.commit()
