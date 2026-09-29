"""Phone number matching against ``customer_user.phone`` / ``.mobile``.

Numbers are compared digits-only (``+``, spaces, dashes, slashes, dots and
brackets dropped) on their last :data:`SUFFIX_DIGITS` digits, so ``+49 228
555-0101``, ``0228/5550101`` and ``(0228) 555 0101`` are the same number
regardless of the country-code/trunk-prefix spelling.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser

#: Compared tail of a number: long enough to be unique within a country,
#: short enough to ignore ``+49`` vs ``0``.
SUFFIX_DIGITS = 9
#: Fewer digits than this are not treated as a phone number (a search for
#: "2024" must not match every number containing it).
MIN_DIGITS = 5

_STRIPPED = (" ", "-", "/", "+", "(", ")", ".")


def phone_digits(raw: str | None) -> str:
    """Digits only (``+`` dropped too)."""
    if not raw:
        return ""
    return "".join(ch for ch in raw if ch.isdigit())


def _digits_expr(col: Any) -> Any:
    expr: Any = func.coalesce(col, "")
    for ch in _STRIPPED:
        expr = func.replace(expr, ch, "")
    return expr


def phone_condition(raw: str, *, contains: bool = False) -> ColumnElement[bool] | None:
    """Condition on phone/mobile for *raw*; ``None`` below :data:`MIN_DIGITS`.

    ``contains=False`` (caller lookup) matches numbers *ending* in the last
    :data:`SUFFIX_DIGITS` digits; ``contains=True`` (search box) matches the
    typed digits anywhere, so a partial number finds its customer too.
    """
    digits = phone_digits(raw)
    if len(digits) < MIN_DIGITS:
        return None
    tail = digits[-SUFFIX_DIGITS:]
    pattern = f"%{tail}%" if contains else f"%{tail}"
    return or_(
        _digits_expr(CustomerUser.phone).like(pattern),
        _digits_expr(CustomerUser.mobile).like(pattern),
    )


async def find_customers_by_phone(
    session: AsyncSession, number: str, *, limit: int = 10, valid_only: bool = True
) -> list[CustomerUser]:
    """Customer users whose phone or mobile is *number* (suffix match)."""
    cond = phone_condition(number)
    if cond is None:
        return []
    stmt = select(CustomerUser).where(cond)
    if valid_only:
        stmt = stmt.where(CustomerUser.valid_id == 1)
    stmt = stmt.order_by(CustomerUser.last_name, CustomerUser.first_name, CustomerUser.login)
    return list((await session.execute(stmt.limit(limit))).scalars().all())
