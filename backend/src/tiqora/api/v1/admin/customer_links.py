"""Admin CRUD for ``tiqora_queue_customer_link`` — per-queue external
customer-management-tool link shown as a second button in the ticket-zoom
header (next to the existing internal "Kunde" link).

Tiqora-only table — no Znuny cache invalidation required. ``queue_id`` has
no FK (see model docstring); queue existence/name is resolved against the
Znuny ``queue`` table for display only, exactly like
``tiqora.api.v1.admin.users`` merges queue names by id.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    QueueCustomerLinkCreate,
    QueueCustomerLinkOut,
    QueueCustomerLinkUpdate,
)
from tiqora.db.legacy.queue import Queue
from tiqora.db.tiqora.models import TiqoraQueueCustomerLink

router = APIRouter(prefix="/queue-customer-links", tags=["admin:queue-customer-links"])

_ALLOWED_VISIBILITY: frozenset[str] = frozenset({"all", "admins"})


def _validate_visibility(value: str) -> None:
    if value not in _ALLOWED_VISIBILITY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="visibility must be 'all' or 'admins'",
        )


async def _queue_names(session: DbSession, queue_ids: list[int]) -> dict[int, str]:
    if not queue_ids:
        return {}
    rows = await session.execute(select(Queue.id, Queue.name).where(Queue.id.in_(queue_ids)))
    return {qid: name for qid, name in rows.all()}


def _to_out(row: TiqoraQueueCustomerLink, queue_name: str | None) -> QueueCustomerLinkOut:
    return QueueCustomerLinkOut(
        id=row.id,
        queue_id=row.queue_id,
        queue_name=queue_name,
        url_template=row.url_template,
        admin_url_template=row.admin_url_template,
        label=row.label,
        visibility=row.visibility,
        create_time=row.create_time,
        change_time=row.change_time,
    )


@router.get("", response_model=list[QueueCustomerLinkOut])
async def list_queue_customer_links(
    admin: AdminUser, session: DbSession
) -> list[QueueCustomerLinkOut]:
    """Full list (one row per queue at most — no pagination needed)."""
    _ = admin
    rows = (
        (
            await session.execute(
                select(TiqoraQueueCustomerLink).order_by(TiqoraQueueCustomerLink.queue_id)
            )
        )
        .scalars()
        .all()
    )
    names = await _queue_names(session, [row.queue_id for row in rows])
    return [_to_out(row, names.get(row.queue_id)) for row in rows]


@router.get("/{link_id}", response_model=QueueCustomerLinkOut)
async def get_queue_customer_link(
    link_id: int, admin: AdminUser, session: DbSession
) -> QueueCustomerLinkOut:
    _ = admin
    row = await session.get(TiqoraQueueCustomerLink, link_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer link not found")
    names = await _queue_names(session, [row.queue_id])
    return _to_out(row, names.get(row.queue_id))


@router.post("", response_model=QueueCustomerLinkOut, status_code=status.HTTP_201_CREATED)
async def create_queue_customer_link(
    body: QueueCustomerLinkCreate, admin: AdminUser, session: DbSession
) -> QueueCustomerLinkOut:
    _validate_visibility(body.visibility)
    existing = (
        await session.execute(
            select(TiqoraQueueCustomerLink).where(TiqoraQueueCustomerLink.queue_id == body.queue_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A customer link already exists for this queue",
        )
    row = TiqoraQueueCustomerLink(
        queue_id=body.queue_id,
        url_template=body.url_template,
        admin_url_template=body.admin_url_template,
        label=body.label,
        visibility=body.visibility,
        create_by=admin.id,
        change_by=admin.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    names = await _queue_names(session, [row.queue_id])
    return _to_out(row, names.get(row.queue_id))


@router.patch("/{link_id}", response_model=QueueCustomerLinkOut)
async def update_queue_customer_link(
    link_id: int,
    body: QueueCustomerLinkUpdate,
    admin: AdminUser,
    session: DbSession,
) -> QueueCustomerLinkOut:
    row = await session.get(TiqoraQueueCustomerLink, link_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer link not found")
    updates = body.model_dump(exclude_unset=True)
    if "visibility" in updates and updates["visibility"] is not None:
        _validate_visibility(updates["visibility"])
    for field, value in updates.items():
        setattr(row, field, value)
    row.change_by = admin.id
    row.change_time = now()
    await session.commit()
    await session.refresh(row)
    names = await _queue_names(session, [row.queue_id])
    return _to_out(row, names.get(row.queue_id))


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue_customer_link(link_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete (no soft-valid flag on this table, mirrors queue variables)."""
    _ = admin
    row = await session.get(TiqoraQueueCustomerLink, link_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer link not found")
    await session.delete(row)
    await session.commit()


__all__ = ["router"]
