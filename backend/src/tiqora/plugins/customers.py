"""Read-only customer lookups for plugins (database only, never plugin customer sources)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.engine import get_session_factory
from tiqora.domain.customer_service import CustomerService

__all__ = ["CustomerSummary", "PluginCustomers"]


@dataclass(frozen=True, slots=True)
class CustomerSummary:
    login: str
    email: str
    first_name: str
    last_name: str
    customer_id: str | None
    company_name: str | None
    valid: bool


class PluginCustomers:
    """Look up customer contacts; plugins cannot change customers through this class."""

    async def get(
        self, login: str, *, session: AsyncSession | None = None
    ) -> CustomerSummary | None:
        if session is None:
            async with get_session_factory()() as own:
                return await self.get(login, session=own)
        out = await CustomerService(session).get_by_login(login)
        if out is None:
            return None
        return CustomerSummary(
            login=out.login,
            email=out.email,
            first_name=out.first_name,
            last_name=out.last_name,
            customer_id=out.customer_id or None,
            company_name=out.company_name,
            valid=out.valid_id == 1,
        )

    async def search(
        self, q: str, *, limit: int = 10, session: AsyncSession | None = None
    ) -> list[CustomerSummary]:
        """Valid contacts matching ``q`` (at least two characters); never asks plugin sources."""
        if session is None:
            async with get_session_factory()() as own:
                return await self.search(q, limit=limit, session=own)
        _companies, contacts = await CustomerService(session).quick_search(q, limit=limit)
        return [
            CustomerSummary(
                login=c.login,
                email=c.email,
                first_name=c.first_name,
                last_name=c.last_name,
                customer_id=c.customer_id or None,
                company_name=c.company_name,
                valid=True,
            )
            for c in contacts
        ]
