"""Admin CRUD for queues."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    QUEUE_CACHE_TYPES,
    invalidate_cache_for_queue,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    apply_valid_filter,
    bulk_grouped_counts,
    paginate,
)
from tiqora.api.v1.admin.schemas import (
    PhysicalQueueVariableOut,
    QueueCreate,
    QueueOut,
    QueueUpdate,
)
from tiqora.db.legacy.queue import Queue, QueueAutoResponse, QueueStandardTemplate

router = APIRouter(prefix="/queues", tags=["admin:queues"])

# Stock Znuny ``queue`` columns — everything else is a site-specific custom
# column (e.g. domain / phonenumber from a Znuny patch) available as
# ``<OTRS_QUEUE_...>`` via SELECT q.* in placeholder expansion.
_STANDARD_QUEUE_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "group_id",
        "unlock_timeout",
        "first_response_time",
        "first_response_notify",
        "update_time",
        "update_notify",
        "solution_time",
        "solution_notify",
        "system_address_id",
        "calendar_name",
        "default_sign_key",
        "salutation_id",
        "signature_id",
        "follow_up_id",
        "follow_up_lock",
        "comments",
        "valid_id",
        "create_time",
        "create_by",
        "change_time",
        "change_by",
    }
)
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@router.get("", response_model=Page[QueueOut])
async def list_queues(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[QueueOut]:
    _ = admin
    stmt = apply_valid_filter(select(Queue), Queue.valid_id, params.valid).order_by(Queue.name)
    return await paginate(session, QueueOut, stmt, params)


@router.get("/assignment-counts", response_model=dict[int, int])
async def queue_assignment_counts(
    admin: AdminUser,
    session: DbSession,
    side: Literal["templates", "auto-responses"] = Query(...),
) -> dict[int, int]:
    """Bulk assignment counts keyed by queue id (for AssignmentEditor badges)."""
    _ = admin
    if side == "templates":
        return await bulk_grouped_counts(session, QueueStandardTemplate.queue_id)
    return await bulk_grouped_counts(session, QueueAutoResponse.queue_id)


@router.get("/{queue_id}", response_model=QueueOut)
async def get_queue(queue_id: int, admin: AdminUser, session: DbSession) -> Queue:
    _ = admin
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    return queue


@router.get("/{queue_id}/physical-variables", response_model=list[PhysicalQueueVariableOut])
async def list_queue_physical_variables(
    queue_id: int, admin: AdminUser, session: DbSession
) -> list[PhysicalQueueVariableOut]:
    """Non-standard columns on the ``queue`` table for this queue (read-only).

    Stock Znuny installs with no custom columns return ``[]``. Missing queue → 404.
    """
    _ = admin
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")

    col_result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'queue'"
            " ORDER BY ordinal_position"
        ),
    )
    all_cols = [str(row[0]) for row in col_result.all()]
    custom = [
        c for c in all_cols if c.lower() not in _STANDARD_QUEUE_COLUMNS and _SAFE_IDENT.match(c)
    ]
    if not custom:
        return []

    # Identifiers already validated as [A-Za-z_][A-Za-z0-9_]* (from
    # information_schema only) — safe to interpolate as SQL column lists.
    col_list = ", ".join(custom)
    row_result = await session.execute(
        text(f"SELECT {col_list} FROM queue WHERE id = :qid LIMIT 1"),  # noqa: S608
        {"qid": queue_id},
    )
    mapping = row_result.mappings().first()
    if mapping is None:
        return []

    out: list[PhysicalQueueVariableOut] = []
    for col in custom:
        raw = mapping.get(col)
        if raw is None:
            # Drivers may return lowercased keys.
            raw = mapping.get(col.lower())
        value = "" if raw is None else str(raw)
        out.append(PhysicalQueueVariableOut(name=col, value=value))
    return out


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
    await invalidate_znuny_cache_types(session, QUEUE_CACHE_TYPES)
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
    # is ticket-relevant for every ticket currently in the queue. Also clears
    # Znuny CacheType 'Queue' for the master-data list itself.
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
