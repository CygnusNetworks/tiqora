"""Read-only customer_user / customer_company lookup for ticket display."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.schemas import CustomerUserOut

# Znuny's "valid" list id — 1 == valid, everything else is invalid/temporary.
_VALID = 1


@dataclass
class CompanyMatch:
    customer_id: str
    name: str


@dataclass
class ContactMatch:
    login: str
    email: str
    first_name: str
    last_name: str
    customer_id: str
    company_name: str | None


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

    async def quick_search(
        self, q: str, limit: int = 10
    ) -> tuple[list[CompanyMatch], list[ContactMatch]]:
        """Search valid companies and contacts matching *q* for agent-side quick-search widgets.

        Below a 2-character query, returns empty results rather than a
        near-unbounded scan. Contacts additionally match a "first last"
        combination when *q* contains whitespace, so "Jane Doe" finds a
        contact whose first/last name only partially overlap the raw
        substring match. Each contact's company name is resolved via a single
        follow-up IN query (no N+1).
        """
        term = q.strip()
        if len(term) < 2:
            return [], []
        like = f"%{term}%"

        company_stmt = (
            select(CustomerCompany)
            .where(CustomerCompany.valid_id == _VALID)
            .where(or_(CustomerCompany.customer_id.ilike(like), CustomerCompany.name.ilike(like)))
            .order_by(CustomerCompany.name)
            .limit(limit)
        )
        companies = (await self._session.execute(company_stmt)).scalars().all()

        conditions: list[ColumnElement[bool]] = [
            CustomerUser.login.ilike(like),
            CustomerUser.email.ilike(like),
            CustomerUser.first_name.ilike(like),
            CustomerUser.last_name.ilike(like),
        ]
        parts = term.split(None, 1)
        if len(parts) == 2:
            first, last = parts
            conditions.append(
                and_(
                    CustomerUser.first_name.ilike(f"%{first}%"),
                    CustomerUser.last_name.ilike(f"%{last}%"),
                )
            )
        contact_stmt = (
            select(CustomerUser)
            .where(CustomerUser.valid_id == _VALID)
            .where(or_(*conditions))
            .order_by(CustomerUser.last_name, CustomerUser.first_name)
            .limit(limit)
        )
        contacts = (await self._session.execute(contact_stmt)).scalars().all()

        customer_ids = {c.customer_id for c in contacts if c.customer_id}
        company_names: dict[str, str] = {}
        if customer_ids:
            rows = (
                await self._session.execute(
                    select(CustomerCompany.customer_id, CustomerCompany.name).where(
                        CustomerCompany.customer_id.in_(customer_ids)
                    )
                )
            ).all()
            company_names = {row.customer_id: row.name for row in rows}

        return (
            [CompanyMatch(customer_id=c.customer_id, name=c.name) for c in companies],
            [
                ContactMatch(
                    login=c.login,
                    email=c.email,
                    first_name=c.first_name,
                    last_name=c.last_name,
                    customer_id=c.customer_id,
                    company_name=company_names.get(c.customer_id),
                )
                for c in contacts
            ],
        )
