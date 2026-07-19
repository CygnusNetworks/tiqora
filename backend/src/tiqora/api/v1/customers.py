"""Customer user read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.customer_service import CustomerService
from tiqora.domain.schemas import CustomerUserOut

router = APIRouter(prefix="/customers", tags=["customers"])


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
