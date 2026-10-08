"""Perl number semantics shared by the domain layer and the GenericInterface compat layer.

Znuny (Perl/DBI) hands numeric DB values to JSON/XML as Perl scalars: ``3.00`` prints as ``3``,
``1.50`` as ``1.5``. These helpers reproduce that numification and stringification (``%.15g``).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Perl numifies the leading numeric prefix of a string ("12abc" -> 12, "abc" -> 0).
_NUM_PREFIX = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_INT_LIMIT = 1e15


def _normalise(number: float) -> int | float:
    if number != number or number in (float("inf"), float("-inf")):
        return number
    if number.is_integer() and abs(number) < _INT_LIMIT:
        return int(number)
    return number


def perl_num(value: Decimal | float | int | str | None) -> int | float:
    """Perl numeric value: ``Decimal("3.00")`` -> 3, ``"1.50"`` -> 1.5, ``None`` -> 0."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        try:
            return _normalise(float(value))
        except (OverflowError, InvalidOperation):
            return 0
    if isinstance(value, float):
        return _normalise(value)
    match = _NUM_PREFIX.match(str(value))
    if match is None:
        return 0
    return _normalise(float(match.group(1)))


def perl_num_str(value: Decimal | float | int | str | None) -> str:
    """Perl stringification of :func:`perl_num` (``%.15g``): 3 -> "3", 1.5 -> "1.5"."""
    number = perl_num(value)
    if isinstance(number, int):
        return str(number)
    return format(number, ".15g")
