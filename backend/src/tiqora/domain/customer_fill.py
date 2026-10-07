"""Fill-only update of a customer user's contact fields.

A field is written only when it is empty on the customer — nothing is
overwritten or cleared. Shared by ``PATCH /customers/{login}`` (AI secretary)
and the vCard import.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser

FILL_FIELDS = ("first_name", "last_name", "email", "phone", "mobile")

#: Column sizes of the fill fields in ``customer_user``.
FILL_MAX_LENGTHS: Mapping[str, int] = {
    "first_name": 100,
    "last_name": 100,
    "email": 150,
    "phone": 150,
    "mobile": 150,
}


class CustomerEmailTakenError(Exception):
    """Another valid customer user already has the address."""

    def __init__(self, owner: str) -> None:
        super().__init__(owner)
        self.owner = owner


def is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def fillable_fields(cu: CustomerUser, values: Mapping[str, str | None]) -> list[str]:
    """Fields that :func:`fill_blank_fields` would write (no e-mail owner check)."""
    return [
        name
        for name in FILL_FIELDS
        if not is_blank(values.get(name)) and is_blank(getattr(cu, name))
    ]


async def fill_blank_fields(
    session: AsyncSession, cu: CustomerUser, values: Mapping[str, str | None]
) -> list[str]:
    """Set every blank field of *cu* that *values* has; return the names written.

    Raises :class:`CustomerEmailTakenError` when the e-mail to fill belongs to
    another valid customer user. Does not commit.
    """
    names = fillable_fields(cu, values)
    if "email" in names:
        # Checked before anything is set, so a conflict leaves *cu* untouched.
        email = (values.get("email") or "").strip()
        owner = (
            (
                await session.execute(
                    select(CustomerUser.login).where(
                        func.lower(CustomerUser.email) == email.lower(),
                        CustomerUser.valid_id == 1,
                        CustomerUser.login != cu.login,
                    )
                )
            )
            .scalars()
            .first()
        )
        if owner is not None:
            raise CustomerEmailTakenError(owner)
    for name in names:
        setattr(cu, name, (values.get(name) or "").strip())
    return names
