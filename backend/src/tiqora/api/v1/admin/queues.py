"""Admin CRUD for queues."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import invalidate_cache_for_queue, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import QueueCreate, QueueOut, QueueUpdate
from tiqora.db.legacy.queue import Queue

router = APIRouter(prefix="/queues", tags=["admin:queues"])


@router.get("", response_model=Page[QueueOut])
async def list_queues(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[QueueOut]:
    _ = admin
    stmt = apply_valid_filter(select(Queue), Queue.valid_id, params.valid).order_by(Queue.name)
    return await paginate(session, QueueOut, stmt, params)


@router.get("/{queue_id}", response_model=QueueOut)
async def get_queue(queue_id: int, admin: AdminUser, session: DbSession) -> Queue:
    _ = admin
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    return queue


@router.post("", response_model=QueueOut, status_code=status.HTTP_201_CREATED)
async def create_queue(body: QueueCreate, admin: AdminUser, session: DbSession) -> Queue:
    ts = now()
    queue = Queue(
        **body.model_dump(),
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(queue)
    await session.commit()
    await session.refresh(queue)
    return queue


@router.patch("/{queue_id}", response_model=QueueOut)
async def update_queue(
    queue_id: int, body: QueueUpdate, admin: AdminUser, session: DbSession
) -> Queue:
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(queue, field, value)
    queue.change_time = now()
    queue.change_by = admin.id
    # Queue config (escalation timers, salutation/signature, validity, ...)
    # is ticket-relevant for every ticket currently in the queue.
    await invalidate_cache_for_queue(session, queue_id)
    await session.commit()
    await session.refresh(queue)
    return queue


@router.delete("/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_queue(queue_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate (``valid_id = 2``) — queues with tickets are never hard-deleted."""
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    queue.valid_id = 2
    queue.change_time = now()
    queue.change_by = admin.id
    await invalidate_cache_for_queue(session, queue_id)
    await session.commit()
