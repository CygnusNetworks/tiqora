"""Lock races of the dashboard writes: which database errors are worth one more attempt."""

from __future__ import annotations

from typing import Final

from sqlalchemy.exc import DBAPIError, IntegrityError

__all__ = ["is_race_error"]

_RETRY_CODES: Final = frozenset({1213, 1205})  # MariaDB: deadlock, lock wait timeout
_RETRY_STATES: Final = frozenset({"40001", "40P01"})  # PostgreSQL: serialization, deadlock


def is_race_error(exc: DBAPIError) -> bool:
    """Unique violation, deadlock, lock wait timeout or serialization failure."""
    if isinstance(exc, IntegrityError):
        return True
    orig = exc.orig
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int) and args[0] in _RETRY_CODES:
        return True
    for source in (orig, getattr(orig, "__cause__", None)):
        state = getattr(source, "sqlstate", None) or getattr(source, "pgcode", None)
        if state in _RETRY_STATES:
            return True
    return False
