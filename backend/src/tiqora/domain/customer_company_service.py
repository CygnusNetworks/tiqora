"""Customer company writes shared by the admin route and plugin customer sources (D13)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.v1.admin.common import (
    CUSTOMER_COMPANY_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.db.legacy.customer import CustomerCompany

__all__ = ["ensure_customer_company"]


async def ensure_customer_company(
    session: AsyncSession,
    *,
    customer_id: str,
    name: str,
    actor_id: int,
    street: str | None = None,
    zip: str | None = None,
    city: str | None = None,
    country: str | None = None,
    url: str | None = None,
    comments: str | None = None,
    valid_id: int = 1,
) -> bool:
    """Insert a ``customer_company`` row; ``False`` (nothing changed) when the id exists.

    Writes the Znuny ``CustomerCompanyAdd`` columns (create/change time and user) and
    invalidates the Znuny CustomerCompany caches. Does not commit.
    """
    if await session.get(CustomerCompany, customer_id) is not None:
        return False
    ts = now()
    session.add(
        CustomerCompany(
            customer_id=customer_id,
            name=name,
            street=street,
            zip=zip,
            city=city,
            country=country,
            url=url,
            comments=comments,
            valid_id=valid_id,
            create_time=ts,
            create_by=actor_id,
            change_time=ts,
            change_by=actor_id,
        )
    )
    await invalidate_znuny_cache_types(session, CUSTOMER_COMPANY_CACHE_TYPES)
    return True
