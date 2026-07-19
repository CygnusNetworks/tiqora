"""Read-only customer_user / customer_company lookup for ticket display."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.schemas import CustomerUserOut


class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_login(self, login: str) -> CustomerUserOut | None:
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.login == login)
        )
        cu = result.scalar_one_or_none()
        if cu is None:
            return None
        company_name: str | None = None
        if cu.customer_id:
            co = (
                await self._session.execute(
                    select(CustomerCompany).where(CustomerCompany.customer_id == cu.customer_id)
                )
            ).scalar_one_or_none()
            if co is not None:
                company_name = co.name
        return CustomerUserOut(
            login=cu.login,
            email=cu.email,
            customer_id=cu.customer_id,
            first_name=cu.first_name,
            last_name=cu.last_name,
            title=cu.title,
            phone=cu.phone,
            company_name=company_name,
        )
