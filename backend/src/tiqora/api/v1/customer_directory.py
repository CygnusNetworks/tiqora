"""Agent customer directory: searchable customer list and vCard list exports.

Every route needs the ``customer_directory`` feature (admins always have it;
others via a grant to them, a group they belong to, or a role). Mounted at
``/customer-directory`` rather than under ``/customers`` so no path can be
swallowed by ``GET /customers/{login}``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Final

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    ValidFilter,
    apply_sort,
    apply_valid_filter,
    window,
)
from tiqora.api.v1.customers import vcard_response
from tiqora.api.v1.feature_deps import CustomerDirectoryUser
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.vcard import build_vcard, vcard_filename

router = APIRouter(prefix="/customer-directory", tags=["customer-directory"])

#: Most contacts one export may contain; larger requests get a 422.
EXPORT_MAX: Final[int] = 1000

_SORT_COLUMNS = {
    "name": CustomerUser.last_name,
    "first_name": CustomerUser.first_name,
    "email": CustomerUser.email,
    "customer_id": CustomerUser.customer_id,
    "login": CustomerUser.login,
}

_SEARCH_COLUMNS = (
    CustomerUser.login,
    CustomerUser.email,
    CustomerUser.first_name,
    CustomerUser.last_name,
    CustomerUser.customer_id,
    CustomerUser.phone,
    CustomerUser.mobile,
)


class CustomerDirectoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    login: str
    email: str
    customer_id: str
    company_name: str | None = None
    title: str | None
    first_name: str
    last_name: str
    phone: str | None
    mobile: str | None
    city: str | None
    valid_id: int


class CustomerDirectoryCompany(BaseModel):
    customer_id: str
    name: str


def _filtered(search: str | None, customer_id: str | None, valid: ValidFilter) -> Select[Any]:
    """Customer users matching the directory filters.

    Every whitespace-separated word of *search* must match one column (or the
    company name), so "laura gomez" and "gomez northwind" both find her.
    """
    stmt = apply_valid_filter(select(CustomerUser), CustomerUser.valid_id, valid)
    if customer_id:
        stmt = stmt.where(CustomerUser.customer_id == customer_id)
    for word in (search or "").lower().split():
        pattern = f"%{word}%"
        company_ids = select(CustomerCompany.customer_id).where(
            func.lower(CustomerCompany.name).like(pattern)
        )
        stmt = stmt.where(
            or_(
                *(func.lower(col).like(pattern) for col in _SEARCH_COLUMNS),
                CustomerUser.customer_id.in_(company_ids),
            )
        )
    return stmt


async def _company_names(session: AsyncSession, customer_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({cid for cid in customer_ids if cid})
    if not ids:
        return {}
    rows = await session.execute(
        select(CustomerCompany.customer_id, CustomerCompany.name).where(
            CustomerCompany.customer_id.in_(ids)
        )
    )
    return {cid: name for cid, name in rows.all()}


_ORDER = (CustomerUser.last_name, CustomerUser.first_name, CustomerUser.login)


@router.get("", response_model=Page[CustomerDirectoryEntry])
async def list_directory(
    user: CustomerDirectoryUser,
    session: DbSession,
    params: ListParamsDep,
    search: str | None = None,
    customer_id: str | None = None,
) -> Page[CustomerDirectoryEntry]:
    """Customer users, name first. Default order: last name, first name."""
    _ = user
    stmt = _filtered(search, customer_id, params.valid)
    if params.sort in _SORT_COLUMNS:
        stmt = apply_sort(
            stmt,
            _SORT_COLUMNS,
            params,
            default=CustomerUser.last_name,
            tiebreaker=CustomerUser.login,
        )
    else:
        stmt = stmt.order_by(*_ORDER)
    rows, total = await window(session, stmt, params)
    names = await _company_names(session, (r.customer_id for r in rows))
    items = [
        CustomerDirectoryEntry.model_validate(r).model_copy(
            update={"company_name": names.get(r.customer_id)}
        )
        for r in rows
    ]
    return Page[CustomerDirectoryEntry](
        items=items, total=total, page=params.page, page_size=params.page_size
    )


@router.get("/companies", response_model=list[CustomerDirectoryCompany])
async def search_companies(
    user: CustomerDirectoryUser,
    session: DbSession,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[CustomerDirectoryCompany]:
    """Valid companies for the directory's company filter."""
    _ = user
    stmt = select(CustomerCompany.customer_id, CustomerCompany.name).where(
        CustomerCompany.valid_id == 1
    )
    if search and (term := search.strip().lower()):
        pattern = f"%{term}%"
        stmt = stmt.where(
            or_(
                func.lower(CustomerCompany.name).like(pattern),
                func.lower(CustomerCompany.customer_id).like(pattern),
            )
        )
    rows = await session.execute(stmt.order_by(CustomerCompany.name).limit(limit))
    return [CustomerDirectoryCompany(customer_id=cid, name=name) for cid, name in rows.all()]


class CustomerDirectoryExportRequest(BaseModel):
    """Exactly these contacts (a selection), in name order."""

    logins: list[str] = Field(min_length=1)


@router.get("/vcards")
async def export_filtered_vcards(
    user: CustomerDirectoryUser,
    session: DbSession,
    search: str | None = None,
    customer_id: str | None = None,
    valid: ValidFilter = "valid",
) -> Response:
    """Everything matching the list filters as one multi-card .vcf.

    A plain GET so the UI can offer it as a download link (session cookie).
    At most ``EXPORT_MAX`` contacts per file.
    """
    _ = user
    stmt = _filtered(search, customer_id, valid)
    customers, names = await _load_export(session, stmt)
    filename = (names.get(customer_id) or customer_id) if customer_id else "kunden"
    return _vcards(customers, names, filename)


@router.post("/vcards")
async def export_selected_vcards(
    body: CustomerDirectoryExportRequest,
    user: CustomerDirectoryUser,
    session: DbSession,
) -> Response:
    """A selection of contacts as one .vcf. POST because a selection of
    hundreds of logins would not fit into a URL."""
    _ = user
    logins = set(body.logins)
    if len(logins) > EXPORT_MAX:
        raise _too_many()
    stmt = select(CustomerUser).where(CustomerUser.login.in_(logins))
    customers, names = await _load_export(session, stmt)
    return _vcards(customers, names, "kontakte")


async def _load_export(
    session: AsyncSession, stmt: Select[Any]
) -> tuple[list[CustomerUser], dict[str, str]]:
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if total > EXPORT_MAX:
        raise _too_many()
    if total == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No contacts")
    customers = list((await session.execute(stmt.order_by(*_ORDER))).scalars().all())
    names = await _company_names(session, (cu.customer_id for cu in customers))
    return customers, names


def _vcards(customers: list[CustomerUser], names: dict[str, str], filename: str) -> Response:
    body = "".join(build_vcard(cu, names.get(cu.customer_id)) for cu in customers)
    return vcard_response(body, vcard_filename(filename))


def _too_many() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"At most {EXPORT_MAX} contacts per export; narrow the filter",
    )
