"""Agent customer directory: searchable customer list and vCard list exports.

Every route needs the ``customer_directory`` feature (admins always have it;
others via a grant to them, a group they belong to, or a role). Mounted at
``/customer-directory`` rather than under ``/customers`` so no path can be
swallowed by ``GET /customers/{login}``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
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
    apply_valid_filter,
    window,
)
from tiqora.api.v1.customer_sort import order_customer_users
from tiqora.api.v1.customers import vcard_response
from tiqora.api.v1.feature_deps import CustomerDirectoryUser
from tiqora.db.legacy.article import Article, CommunicationChannel
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.tiqora.models import TiqoraCustomerFavorite
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.vcard import build_vcard, vcard_filename

router = APIRouter(prefix="/customer-directory", tags=["customer-directory"])

#: Most contacts one export may contain; larger requests get a 422.
EXPORT_MAX: Final[int] = 1000

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
    """Customer users, name first. Sort keys: see ``customer_sort``; default name order."""
    _ = user
    stmt = _filtered(search, customer_id, params.valid)
    stmt = order_customer_users(stmt, params.sort, params.order)
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


#: How far back the shortlist looks at the agent's own articles.
SHORTLIST_DAYS: Final[int] = 90
#: Entries per shortlist section.
SHORTLIST_SIZE: Final[int] = 6
#: Most favorites one agent may keep.
FAVORITES_MAX: Final[int] = 50


class CustomerShortlistEntry(BaseModel):
    login: str
    email: str
    customer_id: str
    company_name: str | None = None
    first_name: str
    last_name: str
    phone: str | None
    mobile: str | None
    #: Newest article this agent wrote on a ticket of this customer user.
    last_at: UtcDateTime
    #: Communication channel of that article (``Email``, ``Phone``, ``Internal`` …).
    last_channel: str | None
    #: Distinct tickets of this customer user the agent wrote on in the window.
    ticket_count: int


class CustomerFavoriteEntry(BaseModel):
    login: str
    email: str
    customer_id: str
    company_name: str | None = None
    first_name: str
    last_name: str
    phone: str | None
    mobile: str | None


class CustomerShortlist(BaseModel):
    #: Customer users the agent starred, by name.
    favorites: list[CustomerFavoriteEntry]
    recent: list[CustomerShortlistEntry]
    frequent: list[CustomerShortlistEntry]


async def _favorites(session: AsyncSession, user_id: int) -> list[CustomerFavoriteEntry]:
    # Two queries, no JOIN: on MariaDB the tiqora_* table gets the server's
    # default collation, Znuny's customer_user.login often another one, and
    # comparing the two columns fails with "Illegal mix of collations".
    logins = list(
        (
            await session.execute(
                select(TiqoraCustomerFavorite.customer_login).where(
                    TiqoraCustomerFavorite.user_id == user_id
                )
            )
        ).scalars()
    )
    if not logins:
        return []
    customers = list(
        (
            await session.execute(
                select(CustomerUser)
                .where(CustomerUser.login.in_(logins), CustomerUser.valid_id == 1)
                .order_by(*_ORDER)
            )
        ).scalars()
    )
    names = await _company_names(session, (cu.customer_id for cu in customers))
    return [
        CustomerFavoriteEntry(
            login=cu.login,
            email=cu.email,
            customer_id=cu.customer_id,
            company_name=names.get(cu.customer_id),
            first_name=cu.first_name,
            last_name=cu.last_name,
            phone=cu.phone,
            mobile=cu.mobile,
        )
        for cu in customers
    ]


@router.get("/shortlist", response_model=CustomerShortlist)
async def customer_shortlist(
    user: CustomerDirectoryUser,
    session: DbSession,
) -> CustomerShortlist:
    """The agent's favorites and own recent and most frequent customer users.

    Recent and frequent are derived from the articles the agent wrote in the
    last ``SHORTLIST_DAYS`` days (mails, phone notes, internal notes), grouped
    by the ticket's customer user. Invalid customer users are left out.
    """
    favorites = await _favorites(session, user.id)
    since = datetime.now() - timedelta(days=SHORTLIST_DAYS)
    last_at = func.max(Article.create_time).label("last_at")
    tickets = func.count(func.distinct(Ticket.id)).label("tickets")
    base = (
        select(Ticket.customer_user_id, last_at, tickets)
        .select_from(Article)
        .join(Ticket, Ticket.id == Article.ticket_id)
        .join(CustomerUser, CustomerUser.login == Ticket.customer_user_id)
        .where(
            Article.create_by == user.id,
            Article.create_time >= since,
            CustomerUser.valid_id == 1,
        )
        .group_by(Ticket.customer_user_id)
    )
    recent_rows = (await session.execute(base.order_by(last_at.desc()).limit(SHORTLIST_SIZE))).all()
    frequent_rows = (
        await session.execute(base.order_by(tickets.desc(), last_at.desc()).limit(SHORTLIST_SIZE))
    ).all()
    stats = {row[0]: (row[1], int(row[2])) for row in (*recent_rows, *frequent_rows)}
    if not stats:
        return CustomerShortlist(favorites=favorites, recent=[], frequent=[])

    channel_rows = await session.execute(
        select(Ticket.customer_user_id, Article.create_time, CommunicationChannel.name)
        .select_from(Article)
        .join(Ticket, Ticket.id == Article.ticket_id)
        .join(CommunicationChannel, CommunicationChannel.id == Article.communication_channel_id)
        .where(
            Article.create_by == user.id,
            Article.create_time >= min(at for at, _ in stats.values()),
            Ticket.customer_user_id.in_(list(stats)),
        )
    )
    last_channel: dict[str, tuple[datetime, str]] = {}
    for login, created, channel in channel_rows.all():
        seen = last_channel.get(login)
        if seen is None or created > seen[0]:
            last_channel[login] = (created, channel)

    customers = {
        cu.login: cu
        for cu in (
            await session.execute(select(CustomerUser).where(CustomerUser.login.in_(list(stats))))
        ).scalars()
    }
    names = await _company_names(session, (cu.customer_id for cu in customers.values()))

    def entries(rows: Iterable[Any]) -> list[CustomerShortlistEntry]:
        out = []
        for row in rows:
            cu = customers.get(row[0])
            if cu is None:
                continue
            at, count = stats[row[0]]
            out.append(
                CustomerShortlistEntry(
                    login=cu.login,
                    email=cu.email,
                    customer_id=cu.customer_id,
                    company_name=names.get(cu.customer_id),
                    first_name=cu.first_name,
                    last_name=cu.last_name,
                    phone=cu.phone,
                    mobile=cu.mobile,
                    last_at=at,
                    last_channel=last_channel.get(cu.login, (at, None))[1],
                    ticket_count=count,
                )
            )
        return out

    return CustomerShortlist(
        favorites=favorites, recent=entries(recent_rows), frequent=entries(frequent_rows)
    )


# `:path`: logins may contain "/".
@router.put("/favorites/{login:path}", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(login: str, user: CustomerDirectoryUser, session: DbSession) -> None:
    """Star a customer user for this agent (idempotent)."""
    if await session.get(TiqoraCustomerFavorite, (user.id, login)) is not None:
        return
    exists = await session.scalar(select(CustomerUser.id).where(CustomerUser.login == login))
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    count = await session.scalar(
        select(func.count())
        .select_from(TiqoraCustomerFavorite)
        .where(TiqoraCustomerFavorite.user_id == user.id)
    )
    if int(count or 0) >= FAVORITES_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"At most {FAVORITES_MAX} favorites",
        )
    session.add(TiqoraCustomerFavorite(user_id=user.id, customer_login=login))
    await session.commit()


@router.delete("/favorites/{login:path}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(login: str, user: CustomerDirectoryUser, session: DbSession) -> None:
    """Unstar a customer user for this agent (idempotent)."""
    row = await session.get(TiqoraCustomerFavorite, (user.id, login))
    if row is not None:
        await session.delete(row)
        await session.commit()


class CustomerDirectoryCompanyDetail(BaseModel):
    customer_id: str
    name: str
    street: str | None
    zip: str | None
    city: str | None
    country: str | None
    url: str | None
    comments: str | None
    valid_id: int
    #: Valid contacts of the company.
    contact_count: int


# `:path`: company ids may contain "/".
@router.get("/companies/{customer_id:path}", response_model=CustomerDirectoryCompanyDetail)
async def company_detail(
    customer_id: str,
    user: CustomerDirectoryUser,
    session: DbSession,
) -> CustomerDirectoryCompanyDetail:
    """One company's master data for the company view of the "Kunden" page."""
    _ = user
    co = await session.get(CustomerCompany, customer_id)
    if co is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    contacts = await session.scalar(
        select(func.count())
        .select_from(CustomerUser)
        .where(CustomerUser.customer_id == customer_id, CustomerUser.valid_id == 1)
    )
    return CustomerDirectoryCompanyDetail(
        customer_id=co.customer_id,
        name=co.name,
        street=co.street,
        zip=co.zip,
        city=co.city,
        country=co.country,
        url=co.url,
        comments=co.comments,
        valid_id=co.valid_id,
        contact_count=int(contacts or 0),
    )


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
