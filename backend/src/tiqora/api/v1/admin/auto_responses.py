"""Admin CRUD for auto_response + queue_auto_response assignment."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import AUTO_RESPONSE_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import (
    AutoResponseCreate,
    AutoResponseOut,
    AutoResponseUpdate,
    QueueAutoResponseAssignment,
    QueueOut,
)
from tiqora.db.legacy.queue import AutoResponse, Queue, QueueAutoResponse

router = APIRouter(tags=["admin:auto-responses"])


@router.get("/auto-responses", response_model=Page[AutoResponseOut])
async def list_auto_responses(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[AutoResponseOut]:
    _ = admin
    stmt = apply_valid_filter(select(AutoResponse), AutoResponse.valid_id, params.valid).order_by(
        AutoResponse.name
    )
    return await paginate(session, AutoResponseOut, stmt, params)


@router.get("/auto-responses/{auto_response_id}", response_model=AutoResponseOut)
async def get_auto_response(
    auto_response_id: int, admin: AdminUser, session: DbSession
) -> AutoResponse:
    _ = admin
    row = await session.get(AutoResponse, auto_response_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto response not found")
    return row


@router.post("/auto-responses", response_model=AutoResponseOut, status_code=status.HTTP_201_CREATED)
async def create_auto_response(
    body: AutoResponseCreate, admin: AdminUser, session: DbSession
) -> AutoResponse:
    ts = now()
    row = AutoResponse(
        **body.model_dump(), create_time=ts, create_by=admin.id, change_time=ts, change_by=admin.id
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, AUTO_RESPONSE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/auto-responses/{auto_response_id}", response_model=AutoResponseOut)
async def update_auto_response(
    auto_response_id: int, body: AutoResponseUpdate, admin: AdminUser, session: DbSession
) -> AutoResponse:
    row = await session.get(AutoResponse, auto_response_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto response not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, AUTO_RESPONSE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/auto-responses/{auto_response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_auto_response(
    auto_response_id: int, admin: AdminUser, session: DbSession
) -> None:
    row = await session.get(AutoResponse, auto_response_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto response not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, AUTO_RESPONSE_CACHE_TYPES)
    await session.commit()


@router.get("/queues/{queue_id}/auto-responses", response_model=list[AutoResponseOut])
async def get_queue_auto_responses(
    queue_id: int, admin: AdminUser, session: DbSession
) -> list[AutoResponse]:
    """Auto-responses currently assigned to *queue_id* (read side for the editor)."""
    _ = admin
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    result = await session.execute(
        select(AutoResponse)
        .join(QueueAutoResponse, QueueAutoResponse.auto_response_id == AutoResponse.id)
        .where(QueueAutoResponse.queue_id == queue_id)
        .order_by(AutoResponse.name)
    )
    return list(result.scalars().all())


@router.get("/auto-responses/{auto_response_id}/queues", response_model=list[QueueOut])
async def get_auto_response_queues(
    auto_response_id: int, admin: AdminUser, session: DbSession
) -> list[Queue]:
    """Queues this auto-response is assigned to (reverse read side)."""
    _ = admin
    ar = await session.get(AutoResponse, auto_response_id)
    if ar is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Auto response not found")
    result = await session.execute(
        select(Queue)
        .join(QueueAutoResponse, QueueAutoResponse.queue_id == Queue.id)
        .where(QueueAutoResponse.auto_response_id == auto_response_id)
        .order_by(Queue.name)
    )
    return list(result.scalars().all())


@router.put("/queues/{queue_id}/auto-responses", status_code=status.HTTP_204_NO_CONTENT)
async def assign_queue_auto_response(
    queue_id: int, body: QueueAutoResponseAssignment, admin: AdminUser, session: DbSession
) -> None:
    ts = now()
    session.add(
        QueueAutoResponse(
            queue_id=queue_id,
            auto_response_id=body.auto_response_id,
            create_time=ts,
            create_by=admin.id,
            change_time=ts,
            change_by=admin.id,
        )
    )
    await invalidate_znuny_cache_types(session, AUTO_RESPONSE_CACHE_TYPES)
    await session.commit()


@router.delete(
    "/queues/{queue_id}/auto-responses/{auto_response_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_queue_auto_response(
    queue_id: int, auto_response_id: int, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    result = await session.execute(
        select(QueueAutoResponse).where(
            QueueAutoResponse.queue_id == queue_id,
            QueueAutoResponse.auto_response_id == auto_response_id,
        )
    )
    for row in result.scalars().all():
        await session.delete(row)
    await invalidate_znuny_cache_types(session, AUTO_RESPONSE_CACHE_TYPES)
    await session.commit()
