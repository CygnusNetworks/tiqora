"""Admin CRUD for configurable placeholder variables.

- Queue variables (``tiqora_queue_variable``): per-queue or global name/value
  pairs resolved as ``<OTRS_QUEUE_X>`` / ``<TIQORA_QUEUE_X>``.
- Customer fields (``tiqora_placeholder_field``): registry of
  customer_user/company columns for the variable picker + optional allow-list.

Tiqora-only tables — no Znuny cache invalidation required.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, paginate
from tiqora.api.v1.admin.schemas import (
    PlaceholderFieldCreate,
    PlaceholderFieldOut,
    PlaceholderFieldUpdate,
    QueueVariableCreate,
    QueueVariableOut,
    QueueVariableUpdate,
)
from tiqora.db.tiqora.models import TiqoraPlaceholderField, TiqoraQueueVariable

queue_variables_router = APIRouter(prefix="/queue-variables", tags=["admin:queue-variables"])
customer_fields_router = APIRouter(prefix="/customer-fields", tags=["admin:customer-fields"])

_ALLOWED_SOURCES: frozenset[str] = frozenset({"customer_user", "customer_company"})


# ---------------------------------------------------------------------------
# Queue variables
# ---------------------------------------------------------------------------


@queue_variables_router.get("", response_model=Page[QueueVariableOut])
async def list_queue_variables(
    admin: AdminUser,
    session: DbSession,
    params: ListParamsDep,
    queue_id: Annotated[int | None, Query()] = None,
    global_only: Annotated[bool, Query()] = False,
) -> Page[QueueVariableOut]:
    """List queue variables.

    * ``queue_id`` set → only that queue's rows (not globals).
    * ``global_only=true`` → only ``queue_id IS NULL`` rows.
    * both omitted → all rows.
    """
    _ = admin
    stmt = select(TiqoraQueueVariable)
    if global_only:
        stmt = stmt.where(TiqoraQueueVariable.queue_id.is_(None))
    elif queue_id is not None:
        stmt = stmt.where(TiqoraQueueVariable.queue_id == queue_id)
    stmt = stmt.order_by(TiqoraQueueVariable.queue_id, TiqoraQueueVariable.name)
    return await paginate(session, QueueVariableOut, stmt, params)


@queue_variables_router.get("/{variable_id}", response_model=QueueVariableOut)
async def get_queue_variable(
    variable_id: int, admin: AdminUser, session: DbSession
) -> TiqoraQueueVariable:
    _ = admin
    row = await session.get(TiqoraQueueVariable, variable_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Queue variable not found"
        )
    return row


@queue_variables_router.post(
    "", response_model=QueueVariableOut, status_code=status.HTTP_201_CREATED
)
async def create_queue_variable(
    body: QueueVariableCreate, admin: AdminUser, session: DbSession
) -> TiqoraQueueVariable:
    _ = admin
    row = TiqoraQueueVariable(
        queue_id=body.queue_id,
        name=body.name,
        value=body.value,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@queue_variables_router.patch("/{variable_id}", response_model=QueueVariableOut)
async def update_queue_variable(
    variable_id: int,
    body: QueueVariableUpdate,
    admin: AdminUser,
    session: DbSession,
) -> TiqoraQueueVariable:
    _ = admin
    row = await session.get(TiqoraQueueVariable, variable_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Queue variable not found"
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.changed = now()
    await session.commit()
    await session.refresh(row)
    return row


@queue_variables_router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue_variable(variable_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete a queue variable (no soft-valid flag on this table)."""
    _ = admin
    row = await session.get(TiqoraQueueVariable, variable_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Queue variable not found"
        )
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Customer fields
# ---------------------------------------------------------------------------


@customer_fields_router.get("/available-columns", response_model=list[str])
async def list_available_customer_columns(
    admin: AdminUser,
    session: DbSession,
    source: Annotated[str, Query()],
) -> list[str]:
    """Column names for ``customer_user`` / ``customer_company`` via information_schema."""
    _ = admin
    if source not in _ALLOWED_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source must be 'customer_user' or 'customer_company'",
        )
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = :t"
            " ORDER BY ordinal_position"
        ),
        {"t": source},
    )
    return [str(row[0]) for row in result.all()]


@customer_fields_router.get("", response_model=Page[PlaceholderFieldOut])
async def list_customer_fields(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[PlaceholderFieldOut]:
    _ = admin
    stmt = select(TiqoraPlaceholderField).order_by(
        TiqoraPlaceholderField.source_table, TiqoraPlaceholderField.tag_name
    )
    return await paginate(session, PlaceholderFieldOut, stmt, params)


@customer_fields_router.get("/{field_id}", response_model=PlaceholderFieldOut)
async def get_customer_field(
    field_id: int, admin: AdminUser, session: DbSession
) -> TiqoraPlaceholderField:
    _ = admin
    row = await session.get(TiqoraPlaceholderField, field_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer field not found"
        )
    return row


@customer_fields_router.post(
    "", response_model=PlaceholderFieldOut, status_code=status.HTTP_201_CREATED
)
async def create_customer_field(
    body: PlaceholderFieldCreate, admin: AdminUser, session: DbSession
) -> TiqoraPlaceholderField:
    _ = admin
    if body.source_table not in _ALLOWED_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_table must be 'customer_user' or 'customer_company'",
        )
    row = TiqoraPlaceholderField(
        source_table=body.source_table,
        column_name=body.column_name,
        tag_name=body.tag_name,
        label=body.label,
        enabled=body.enabled,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@customer_fields_router.patch("/{field_id}", response_model=PlaceholderFieldOut)
async def update_customer_field(
    field_id: int,
    body: PlaceholderFieldUpdate,
    admin: AdminUser,
    session: DbSession,
) -> TiqoraPlaceholderField:
    _ = admin
    row = await session.get(TiqoraPlaceholderField, field_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer field not found"
        )
    updates = body.model_dump(exclude_unset=True)
    if "source_table" in updates and updates["source_table"] not in _ALLOWED_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_table must be 'customer_user' or 'customer_company'",
        )
    for field, value in updates.items():
        setattr(row, field, value)
    row.changed = now()
    await session.commit()
    await session.refresh(row)
    return row


@customer_fields_router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_field(field_id: int, admin: AdminUser, session: DbSession) -> None:
    """Hard-delete a registry row (no soft-valid flag on this table)."""
    _ = admin
    row = await session.get(TiqoraPlaceholderField, field_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Customer field not found"
        )
    await session.delete(row)
    await session.commit()


__all__ = ["customer_fields_router", "queue_variables_router"]
