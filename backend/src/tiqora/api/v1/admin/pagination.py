"""Shared list pagination + valid/invalid filtering for the admin CRUD API.

Every standard admin resource list is a potentially large master-data table
(``customer_user`` in particular runs to tens of thousands of rows). Returning
the whole table on every ``GET`` was both slow and — behind a proxy read
timeout — could surface as an *empty* list to the UI. These helpers give each
list endpoint uniform ``page`` / ``page_size`` / ``valid`` query params and a
``Page`` envelope, so the shared frontend table can paginate and filter
generically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

ValidFilter = Literal["valid", "invalid", "all"]


class Page[T](BaseModel):
    """Envelope for a paginated admin list response."""

    items: list[T]
    total: int
    page: int
    page_size: int


class ListParams(BaseModel):
    page: int = 1
    page_size: int = 25
    valid: ValidFilter = "valid"


def list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 25,
    valid: Annotated[ValidFilter, Query()] = "valid",
) -> ListParams:
    return ListParams(page=page, page_size=page_size, valid=valid)


ListParamsDep = Annotated[ListParams, Depends(list_params)]


def apply_valid_filter(
    stmt: Select[Any], column: InstrumentedAttribute[int], valid: ValidFilter
) -> Select[Any]:
    """Restrict *stmt* by validity. Defaults (``"valid"``) hide soft-deleted
    (``valid_id != 1``) rows — the common case for admin lists."""
    if valid == "valid":
        return stmt.where(column == 1)
    if valid == "invalid":
        return stmt.where(column != 1)
    return stmt


async def window(
    session: AsyncSession, stmt: Select[Any], params: ListParams
) -> tuple[list[Any], int]:
    """Return (page of ORM rows, total row count) for *stmt* under *params*."""
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt) or 0
    windowed = stmt.limit(params.page_size).offset((params.page - 1) * params.page_size)
    rows: Sequence[Any] = (await session.execute(windowed)).scalars().all()
    return list(rows), total


async def paginate[P: BaseModel](
    session: AsyncSession, cls: type[P], stmt: Select[Any], params: ListParams
) -> Page[P]:
    """Run *stmt* windowed by *params* and wrap the rows in a ``Page[cls]``.

    *cls* is the out-schema (``from_attributes=True``); the ORM rows are
    validated into it by the enclosing ``Page`` validation.
    """
    rows, total = await window(session, stmt, params)
    return Page[cls].model_validate(  # type: ignore[valid-type]
        {"items": rows, "total": total, "page": params.page, "page_size": params.page_size}
    )
