"""Customer user read + agent-create endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.admin.common import (
    CUSTOMER_USER_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.customer_service import CustomerService
from tiqora.domain.schemas import CustomerUserOut

router = APIRouter(prefix="/customers", tags=["customers"])


class AgentCustomerCreateRequest(BaseModel):
    """Body for agent-side customer-user creation (Znuny AgentTicketCustomer).

    No password — agents create the contact record; portal auth is separate.
    """

    login: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=1, max_length=150)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    customer_id: str = Field(..., min_length=1, max_length=150)
    phone: str | None = Field(None, max_length=150)


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

    ts = now()
    cu = CustomerUser(
        login=login,
        email=body.email.strip(),
        customer_id=body.customer_id.strip(),
        first_name=body.first_name.strip(),
        last_name=body.last_name.strip(),
        phone=body.phone.strip() if body.phone else None,
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
