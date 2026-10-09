"""Fill-only update of a customer user's contact fields.

A field is written only when it is empty on the customer — nothing is
overwritten or cleared. Shared by ``PATCH /customers/{login}`` (AI secretary)
and the vCard import. The vCard import may additionally replace fields an
agent picked one by one in its conflict dialog (:func:`replace_fields`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

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


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[2:] if digits.startswith("00") else digits


def same_value(name: str, current: str, incoming: str) -> bool:
    """Whether two values of field *name* mean the same.

    Case and spacing never count; phone numbers compare by digits, and
    "0228 5550121" equals "+49 228 401200" (the shorter, without its trunk
    zero, ends the longer one).
    """
    if name in ("phone", "mobile"):
        a, b = _digits(current).lstrip("0"), _digits(incoming).lstrip("0")
        if a and b:
            short, long_ = sorted((a, b), key=len)
            return short == long_ or (len(short) >= 6 and long_.endswith(short))
    return " ".join(current.split()).casefold() == " ".join(incoming.split()).casefold()


def differing_fields(cu: CustomerUser, values: Mapping[str, str | None]) -> list[str]:
    """Fields set on *cu* to something other than *values* has — the conflicts."""
    out: list[str] = []
    for name in FILL_FIELDS:
        incoming, current = values.get(name), getattr(cu, name)
        if is_blank(incoming) or is_blank(current):
            continue
        if not same_value(name, current, (incoming or "").strip()):
            out.append(name)
    return out


async def _email_owner(session: AsyncSession, cu: CustomerUser, email: str) -> str | None:
    return (
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


async def replace_fields(
    session: AsyncSession,
    cu: CustomerUser,
    values: Mapping[str, str | None],
    names: Iterable[str],
) -> list[str]:
    """Overwrite the chosen *names* of *cu* with *values*; return the names written.

    Only fields that really differ (:func:`differing_fields`) are touched, so
    a stale choice cannot clear or rewrite anything else. Raises
    :class:`CustomerEmailTakenError` before writing anything when the new
    e-mail belongs to another valid customer user. Does not commit.
    """
    wanted = set(names)
    chosen = [name for name in differing_fields(cu, values) if name in wanted]
    if "email" in chosen:
        owner = await _email_owner(session, cu, (values.get("email") or "").strip())
        if owner is not None:
            raise CustomerEmailTakenError(owner)
    for name in chosen:
        setattr(cu, name, (values.get(name) or "").strip())
    return chosen


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
        owner = await _email_owner(session, cu, (values.get("email") or "").strip())
        if owner is not None:
            raise CustomerEmailTakenError(owner)
    for name in names:
        setattr(cu, name, (values.get(name) or "").strip())
    return names
