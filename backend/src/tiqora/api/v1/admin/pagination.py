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

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

ValidFilter = Literal["valid", "invalid", "all"]
SortOrder = Literal["asc", "desc"]

# Customer-users "Alle" page-size option may request up to this many rows.
CUSTOMER_USER_PAGE_SIZE_MAX = 100_000


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
    """Optional column key from the caller's allowlist; unknown values are ignored."""
    sort: str | None = None
    order: SortOrder = "asc"


def list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 25,
    valid: Annotated[ValidFilter, Query()] = "valid",
    sort: Annotated[str | None, Query()] = None,
    order: Annotated[SortOrder, Query()] = "asc",
) -> ListParams:
    return ListParams(page=page, page_size=page_size, valid=valid, sort=sort, order=order)


def customer_user_list_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=CUSTOMER_USER_PAGE_SIZE_MAX)] = 25,
    valid: Annotated[ValidFilter, Query()] = "valid",
    sort: Annotated[str | None, Query()] = None,
    order: Annotated[SortOrder, Query()] = "asc",
) -> ListParams:
    """List params for customer-users — allows ``page_size`` up to 100_000
    so the admin UI can request an "Alle" (all rows) page without a separate
    unbounded endpoint."""
    return ListParams(page=page, page_size=page_size, valid=valid, sort=sort, order=order)


ListParamsDep = Annotated[ListParams, Depends(list_params)]
CustomerUserListParamsDep = Annotated[ListParams, Depends(customer_user_list_params)]


def apply_sort(
    stmt: Select[Any],
    allowed: Mapping[str, InstrumentedAttribute[Any]],
    params: ListParams,
    *,
    default: InstrumentedAttribute[Any],
    tiebreaker: InstrumentedAttribute[Any] | None = None,
) -> Select[Any]:
    """Apply ``ORDER BY`` from *params* against an allowlisted column map.

    Unknown or absent ``sort`` falls back to *default* ascending (caller may
    still pass ``order`` only when a valid sort is selected). A *tiebreaker*
    column is appended when distinct from the primary sort column so pagination
    order stays stable.
    """
    col = allowed.get(params.sort) if params.sort else None
    if col is None:
        col = default
        direction = "asc"
    else:
        direction = params.order

    primary = col.asc() if direction == "asc" else col.desc()
    if tiebreaker is not None and tiebreaker is not col:
        secondary = tiebreaker.asc() if direction == "asc" else tiebreaker.desc()
        return stmt.order_by(primary, secondary)
    return stmt.order_by(primary)


async def bulk_grouped_counts(
    session: AsyncSession,
    group_col: Any,
    *where_clauses: Any,
    restrict_ids: Sequence[int] | None = None,
) -> dict[int, int]:
    """One ``GROUP BY`` count query: ``{group_id: count}``.

    Optional *restrict_ids* limits the result to a page of anchors (avoids
    scanning the whole join table when only a page of list rows needs counts).
    Rows with zero assignments are omitted — callers map missing → 0.
    """
    stmt = select(group_col, func.count()).group_by(group_col)
    for clause in where_clauses:
        stmt = stmt.where(clause)
    if restrict_ids is not None:
        if not restrict_ids:
            return {}
        stmt = stmt.where(group_col.in_(list(restrict_ids)))
    result = await session.execute(stmt)
    return {int(row[0]): int(row[1]) for row in result.all()}


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
