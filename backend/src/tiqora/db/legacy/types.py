"""SQLAlchemy column types for Znuny legacy tables.

MariaDB historically allowed "zero dates" (``'0000-00-00'`` /
``'0000-00-00 00:00:00'``) for NOT NULL datetime columns. Znuny and older
imports still store those values (customer_user alone has 100k+ such rows in
production). PyMySQL/aiomysql leave unparseable zero-dates as raw strings
instead of raising or returning None; Pydantic v2 then rejects them when
out-schemas declare ``create_time: datetime`` — which surfaces as HTTP 500
on admin list endpoints.

We coerce at the ORM result-processor boundary so every legacy model that
uses :class:`LegacyDateTime` is covered without per-field adapters. Driver-
level ``conv`` overrides were rejected because:

* they only affect MySQL connections (tests also run against Postgres),
* raw SQL paths would silently change too, and
* a TypeDecorator documents the legacy quirk next to the models that need it.

Bind path is left alone — Tiqora always writes real timestamps (``now()``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

# MariaDB / PyMySQL surface forms for invalid zero-dates.
_ZERO_DATE_PREFIXES = ("0000-00-00",)


def _is_zero_date(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.startswith(_ZERO_DATE_PREFIXES) or value in _ZERO_DATE_PREFIXES
    # Extremely defensive: some drivers may produce date(0, 0, 0)-like objects.
    if isinstance(value, datetime):
        return value.year == 0
    if isinstance(value, date):
        return value.year == 0
    return False


class LegacyDateTime(TypeDecorator[datetime | None]):
    """``DateTime`` that maps MariaDB zero-dates to ``None`` on read.

    Use this for every Znuny datetime/timestamp column instead of plain
    :class:`sqlalchemy.DateTime`. Column nullability in the DB is unchanged
    (still NOT NULL where Znuny declares it); only the Python value is coerced
    so API serializers can emit JSON null instead of 500ing.
    """

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:  # noqa: ARG002
        if value is None or _is_zero_date(value):
            return None
        if isinstance(value, datetime):
            return value
        # Unexpected non-datetime leftover (should not happen after PyMySQL
        # convert_datetime) — treat as missing rather than blow up later.
        if isinstance(value, str):
            return None
        return value  # type: ignore[no-any-return]


__all__ = ["LegacyDateTime"]
