"""Sort orders for customer-user lists (admin list and agent directory).

Each sort key maps to a primary column plus tiebreakers, so equal values
(same last name, same company) still come back in a stable, readable order
and pagination never shuffles rows. Unknown or absent keys fall back to the
name order (last name, first name, login) ascending.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, Select

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser

_NAME: Final = (CustomerUser.last_name, CustomerUser.first_name, CustomerUser.login)

#: sort key → (primary, *tiebreakers). ``last_name`` is kept as an alias of
#: ``name`` for clients that sent the column name.
CUSTOMER_USER_SORTS: Final[dict[str, tuple[Any, ...]]] = {
    "name": _NAME,
    "last_name": _NAME,
    "first_name": (CustomerUser.first_name, CustomerUser.last_name, CustomerUser.login),
    "email": (CustomerUser.email, CustomerUser.login),
    "login": (CustomerUser.login,),
    "customer_id": (CustomerUser.customer_id, *_NAME),
    "phone": (CustomerUser.phone, *_NAME),
    "city": (CustomerUser.city, *_NAME),
    "valid_id": (CustomerUser.valid_id, *_NAME),
    "create_time": (CustomerUser.create_time, CustomerUser.login),
    "change_time": (CustomerUser.change_time, CustomerUser.login),
}

#: Sort by company *name* (what the column shows), not by customer_id.
COMPANY_SORT: Final = "company"

SORT_KEYS: Final[frozenset[str]] = frozenset({*CUSTOMER_USER_SORTS, COMPANY_SORT})


def order_customer_users(
    stmt: Select[Any], sort: str | None, order: Literal["asc", "desc"]
) -> Select[Any]:
    """ORDER BY for a ``select(CustomerUser)`` statement.

    The direction applies to every key; contacts without a company record
    always sort after those with one, in both directions.
    """
    if sort == COMPANY_SORT:
        stmt = stmt.outerjoin(
            CustomerCompany, CustomerCompany.customer_id == CustomerUser.customer_id
        )
        keys: tuple[Any, ...] = (CustomerCompany.name, CustomerUser.customer_id, *_NAME)
        no_company: list[ColumnElement[bool]] = [CustomerCompany.name.is_(None)]
    else:
        keys = CUSTOMER_USER_SORTS.get(sort or "", _NAME)
        no_company = []
        if sort not in CUSTOMER_USER_SORTS:
            order = "asc"
    ordered = [k.desc() if order == "desc" else k.asc() for k in keys]
    return stmt.order_by(*no_company, *ordered)
