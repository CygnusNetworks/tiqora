"""Customer user read + agent-create endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.admin.common import (
    CUSTOMER_COMPANY_CACHE_TYPES,
    CUSTOMER_USER_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.customer_service import CustomerService
from tiqora.domain.new_ticket_queue import (
    NewTicketScreen,
    QueueSuggestion,
    suggest_new_ticket_queue,
)
from tiqora.domain.schemas import CustomerUserOut

router = APIRouter(prefix="/customers", tags=["customers"])


class AgentCustomerCreateRequest(BaseModel):
    """Body for agent-side customer-user creation (Znuny AgentTicketCustomer).

    No password — agents create the contact record; portal auth is separate.
    ``email`` and ``first_name`` may be empty: the AI secretary creates
    callers it only knows by surname and number (the columns are NOT NULL,
    so empty means ``""``).
    """

    login: str = Field(..., min_length=1, max_length=200)
    email: str = Field("", max_length=150)
    first_name: str = Field("", max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    customer_id: str = Field(..., min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=150)
    mobile: str | None = Field(None, max_length=150)
    comments: str | None = Field(None, max_length=250)


class AgentCustomerCreateOut(BaseModel):
    """Created customer-user ref for the ticket Kunde dialog."""

    login: str
    email: str
    customer_id: str
    first_name: str
    last_name: str


@router.post(
    "",
    response_model=AgentCustomerCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer(
    body: AgentCustomerCreateRequest,
    user: CurrentUser,
    session: DbSession,
) -> AgentCustomerCreateOut:
    """Create a valid customer_user as any authenticated agent.

    Mirrors Znuny's AgentTicketCustomer "add customer" — not admin-gated.
    """
    login = body.login.strip()
    if not login:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="login is required",
        )
    existing = (
        await session.execute(select(CustomerUser.id).where(CustomerUser.login == login))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Customer user login already exists",
        )
    email = body.email.strip()
    if email:
        owner = (
            (
                await session.execute(
                    select(CustomerUser.login).where(
                        func.lower(CustomerUser.email) == email.lower(),
                        CustomerUser.valid_id == 1,
                    )
                )
            )
            .scalars()
            .first()
        )
        if owner is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Customer user e-mail already exists", "login": owner},
            )

    ts = now()
    cu = CustomerUser(
        login=login,
        email=email,
        customer_id=body.customer_id.strip(),
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        phone=body.phone.strip() if body.phone else None,
        mobile=body.mobile.strip() if body.mobile else None,
        comments=body.comments.strip() if body.comments else None,
        pw=None,
        valid_id=1,
        create_time=ts,
        create_by=user.id,
        change_time=ts,
        change_by=user.id,
    )
    session.add(cu)
    await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
    await session.commit()
    return AgentCustomerCreateOut(
        login=cu.login,
        email=cu.email,
        customer_id=cu.customer_id,
        first_name=cu.first_name,
        last_name=cu.last_name,
    )


class CompanyCreateRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, max_length=150)
    name: str = Field(..., min_length=1, max_length=200)


class CompanyCreateOut(BaseModel):
    customer_id: str
    name: str


@router.post(
    "/companies",
    response_model=CompanyCreateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_company(
    body: CompanyCreateRequest,
    user: CurrentUser,
    session: DbSession,
) -> CompanyCreateOut:
    """Create a customer company as any authenticated agent (the AI
    secretary files a caller's organisation that Tiqora does not know)."""
    customer_id = body.customer_id.strip()
    name = body.name.strip()
    if not customer_id or not name:
        raise HTTPException(status_code=422, detail="customer_id and name are required")
    existing = await session.get(CustomerCompany, customer_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Customer company already exists"
        )
    ts = now()
    session.add(
        CustomerCompany(
            customer_id=customer_id,
            name=name,
            valid_id=1,
            create_time=ts,
            create_by=user.id,
            change_time=ts,
            change_by=user.id,
        )
    )
    await invalidate_znuny_cache_types(session, CUSTOMER_COMPANY_CACHE_TYPES)
    await session.commit()
    return CompanyCreateOut(customer_id=customer_id, name=name)


@router.get("/{login}", response_model=CustomerUserOut)
async def get_customer(
    login: str,
    user: CurrentUser,
    session: DbSession,
) -> CustomerUserOut:
    # Auth required; agent must be logged in. No per-customer ACL in V1 read path.
    _ = user
    result = await CustomerService(session).get_by_login(login)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return result


_FILL_FIELDS = ("first_name", "last_name", "email", "phone", "mobile")
_COMMENTS_MAX = 250


class CustomerFillRequest(BaseModel):
    """Fill-only update: a field is written only when it is empty on the
    customer; nothing is overwritten or cleared (AI secretary, spec B1)."""

    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=150)
    phone: str | None = Field(None, max_length=150)
    mobile: str | None = Field(None, max_length=150)
    comments_append: str | None = Field(None, max_length=250)


class CustomerFillOut(BaseModel):
    login: str
    customer_id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    mobile: str | None
    comments: str | None
    changed: list[str]


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _append_comment(current: str | None, line: str) -> str:
    """Append ``line``; when the column (250) would overflow, drop the
    oldest lines first."""
    lines = [part for part in (current or "").split("\n") if part.strip()]
    lines.append(line)
    while len("\n".join(lines)) > _COMMENTS_MAX and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)[-_COMMENTS_MAX:]


@router.patch("/{login}", response_model=CustomerFillOut)
async def fill_customer(
    login: str,
    body: CustomerFillRequest,
    user: CurrentUser,
    session: DbSession,
) -> CustomerFillOut:
    cu = (
        await session.execute(select(CustomerUser).where(CustomerUser.login == login))
    ).scalar_one_or_none()
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    changed: list[str] = []
    for name in _FILL_FIELDS:
        raw = getattr(body, name)
        if raw is None or _blank(raw) or not _blank(getattr(cu, name)):
            continue
        value = raw.strip()
        if name == "email":
            owner = (
                (
                    await session.execute(
                        select(CustomerUser.login).where(
                            func.lower(CustomerUser.email) == value.lower(),
                            CustomerUser.valid_id == 1,
                            CustomerUser.login != cu.login,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if owner is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"message": "Customer user e-mail already exists", "login": owner},
                )
        setattr(cu, name, value)
        changed.append(name)
    if body.comments_append is not None and not _blank(body.comments_append):
        line = body.comments_append.strip()
        existing = [part.strip() for part in (cu.comments or "").split("\n")]
        if line not in existing:
            cu.comments = _append_comment(cu.comments, line)
            changed.append("comments")
    if changed:
        cu.change_time = now()
        cu.change_by = user.id
        await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
        await session.commit()
    return CustomerFillOut(
        login=cu.login,
        customer_id=cu.customer_id,
        first_name=cu.first_name,
        last_name=cu.last_name,
        email=cu.email,
        phone=cu.phone,
        mobile=cu.mobile,
        comments=cu.comments,
        changed=changed,
    )


@router.get("/{login}/suggested-queue", response_model=QueueSuggestion)
async def suggested_queue(
    login: str,
    user: CurrentUser,
    session: DbSession,
    screen: Annotated[NewTicketScreen, Query(description="New-ticket form variant")] = "phone",
) -> QueueSuggestion:
    """Queue for a new ticket of this customer user, for the current agent.

    Newest ticket of the customer user (``source=customer``) → newest ticket of
    their company (``company``) → the screen's ``QueueDefault`` sysconfig
    (``default``) → first queue that is not Junk/Raw/Postmaster (``fallback``).
    Only queues the agent may create tickets in; tickets in Junk/Raw/Postmaster
    never count. Unknown logins just skip the history steps. Both fields are
    null when the agent may create tickets nowhere.
    """
    return await suggest_new_ticket_queue(session, user.id, screen, login)
