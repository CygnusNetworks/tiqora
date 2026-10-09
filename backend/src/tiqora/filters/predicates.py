"""SQL predicate helpers shared by every filter-language compiler.

The ticket compiler (:mod:`tiqora.filters.tickets`) and the reporting compiler
(:mod:`tiqora.reporting.filter_sql`) build on these, so one filter has one
meaning everywhere: ``contains`` is a case-insensitive escaped ``LIKE``,
out-of-range ids never reach the database (asyncpg refuses to bind them),
and negations are two-valued (they include NULL rows).
"""

from __future__ import annotations

import calendar
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    SmallInteger,
    and_,
    false,
    not_,
    or_,
    true,
)

from tiqora.filters.catalog import FilterError, parse_datetime_value, relative_delta
from tiqora.filters.model import Condition

INT16 = (-(2**15), 2**15 - 1)
INT32 = (-(2**31), 2**31 - 1)
INT64 = (-(2**63), 2**63 - 1)

#: Negated ops are compiled as NOT(positive op), so NULL rows are included.
NEGATED_OPS = {"neq": "eq", "not_in": "in"}


def epoch_of(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


def like_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for ``LIKE ... ESCAPE '\\'``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_nullable(col: Any) -> bool:
    """Whether ``col`` (ORM attribute, Core column or expression) can be NULL."""
    if hasattr(col, "property"):
        return bool(getattr(col.property.columns[0], "nullable", True))
    return bool(getattr(col, "nullable", True))


def int_bounds(col: Any) -> tuple[int, int]:
    """Value range the column's integer type can hold (asyncpg refuses to bind more)."""
    ctype = col.property.columns[0].type if hasattr(col, "property") else col.type
    if isinstance(ctype, SmallInteger):
        return INT16
    if isinstance(ctype, BigInteger):
        return INT64
    return INT32


def datetime_predicate(
    col: Any, cond: Condition, path: str, now: datetime, *, epoch: bool
) -> ColumnElement[bool]:
    """Positive datetime comparison. ``epoch`` columns hold seconds, 0 = unset."""

    def cmp(op_: str, dt: datetime) -> ColumnElement[bool]:
        if not epoch:
            return {"lt": col < dt, "lte": col <= dt, "gt": col > dt, "gte": col >= dt}[op_]  # type: ignore[no-any-return]
        return compare(col, op_, epoch_of(dt), *INT32)

    op, value = cond.op, cond.value
    if op == "before":
        pred = cmp("lt", parse_datetime_value(value, path=path))
    elif op == "after":
        pred = cmp("gt", parse_datetime_value(value, path=path))
    elif op == "between":
        pred = and_(
            cmp("gte", parse_datetime_value(value["from"], path=f"{path}.from")),
            cmp("lt", parse_datetime_value(value["to"], path=f"{path}.to")),
        )
    elif op in ("within_last", "older_than"):
        try:
            limit = now - relative_delta(value, path=path)
        except OverflowError:
            raise FilterError("Der Zeitraum ist zu groß.", path=path) from None
        pred = cmp("gte" if op == "within_last" else "lt", limit)
    else:  # pragma: no cover - validate_filter limits the ops
        raise FilterError(f"„{op}“ ist für Datumsfelder nicht möglich.", path=path)
    return and_(col > 0, pred) if epoch else pred


def positive_predicate(
    col: Any, spec_type: str, cond: Condition, path: str, now: datetime, *, epoch: bool = False
) -> ColumnElement[bool]:
    """Condition on a value column, for ops that mean "a value that ...".

    ``neq`` / ``not_in`` / ``is_empty`` are handled by the callers (they differ
    between plain columns and EXISTS-based dynamic fields).
    """
    op, value = cond.op, cond.value
    if spec_type == "datetime":
        if op == "is_not_empty":
            return (col > 0) if epoch else col.is_not(None)  # type: ignore[no-any-return]
        return datetime_predicate(col, cond, path, now, epoch=epoch)
    if op == "eq":
        return col == value  # type: ignore[no-any-return]
    if op == "in":
        return col.in_(value)  # type: ignore[no-any-return]
    if op == "contains":
        return col.ilike(f"%{like_escape(value)}%", escape="\\")  # type: ignore[no-any-return]
    if op == "starts_with":
        return col.ilike(f"{like_escape(value)}%", escape="\\")  # type: ignore[no-any-return]
    if op == "is_not_empty":
        return and_(col.is_not(None), col != "")
    if op == "lt":
        return col < value  # type: ignore[no-any-return]
    if op == "lte":
        return col <= value  # type: ignore[no-any-return]
    if op == "gt":
        return col > value  # type: ignore[no-any-return]
    if op == "gte":
        return col >= value  # type: ignore[no-any-return]
    if op == "between":
        return and_(col >= value["from"], col <= value["to"])
    raise FilterError(f"„{op}“ ist hier nicht möglich.", path=path)  # pragma: no cover


def const_compare(op: str, v: int, lo: int, hi: int) -> bool | None:
    """Constant answer of ``col <op> v`` for a column in ``[lo, hi]``, else None."""
    if op == "lt":
        return True if v > hi else False if v <= lo else None
    if op == "lte":
        return True if v >= hi else False if v < lo else None
    if op == "gt":
        return True if v < lo else False if v >= hi else None
    if op == "gte":
        return True if v <= lo else False if v > hi else None
    raise ValueError(op)  # pragma: no cover


def compare(col: Any, op: str, v: int, lo: int, hi: int) -> ColumnElement[bool]:
    """``col <op> v`` for an integer column; out-of-range values are never bound."""
    const = const_compare(op, v, lo, hi)
    if const is not None:
        return true() if const else false()
    return {"lt": col < v, "lte": col <= v, "gt": col > v, "gte": col >= v}[op]  # type: ignore[no-any-return]


def fit_int_condition(cond: Condition, lo: int, hi: int) -> Condition | ColumnElement[bool]:
    """Keep out-of-range values away from the database.

    Returns the condition (possibly with values dropped/clamped) or a constant
    predicate when the answer does not depend on the column.
    """
    op, value = cond.op, cond.value

    def fits(v: int) -> bool:
        return lo <= v <= hi

    if op == "eq":
        return cond if fits(value) else false()
    if op == "neq":
        return cond if fits(value) else true()
    if op in ("in", "not_in"):
        kept = [v for v in value if fits(v)]
        if kept:
            return cond.model_copy(update={"value": kept})
        return false() if op == "in" else true()
    if op in ("lt", "lte", "gt", "gte"):
        const = const_compare(op, value, lo, hi)
        if const is None:
            return cond
        return true() if const else false()
    if op == "between":
        low, high = value["from"], value["to"]
        if low > hi or high < lo:
            return false()
        return cond.model_copy(update={"value": {"from": max(low, lo), "to": min(high, hi)}})
    return cond


def non_empty(col: Any) -> ColumnElement[bool]:
    return and_(col.is_not(None), col != "")


def column_predicate(
    col: Any,
    spec_type: str,
    cond: Condition,
    vpath: str,
    now: datetime,
    *,
    epoch: bool = False,
    nullable: bool | None = None,
) -> ColumnElement[bool]:
    """Two-valued predicate for ``cond`` on a plain value column.

    Handles int-range fitting, ``is_empty``, negation including NULL rows, and
    ``is_true`` / ``is_false`` on booleans. ``nullable`` defaults to
    :func:`is_nullable` (never for epoch columns, where 0 means unset).
    """
    if nullable is None:
        nullable = not epoch and is_nullable(col)
    if spec_type == "bool":
        return col.is_(true()) if cond.op == "is_true" else col.is_(false())  # type: ignore[no-any-return]
    if spec_type in ("id", "int"):
        fitted = fit_int_condition(cond, *int_bounds(col))
        if not isinstance(fitted, Condition):
            if nullable and cond.op not in NEGATED_OPS:
                return and_(col.is_not(None), fitted)
            return fitted
        cond = fitted
    op = cond.op
    if op == "is_empty":
        if spec_type == "datetime":
            return col == 0 if epoch else col.is_(None)  # type: ignore[no-any-return]
        return or_(col.is_(None), col == "")
    if op in NEGATED_OPS:
        positive = positive_predicate(
            col, spec_type, cond.model_copy(update={"op": NEGATED_OPS[op]}), vpath, now
        )
        return or_(col.is_(None), not_(positive)) if nullable else not_(positive)
    positive = positive_predicate(col, spec_type, cond, vpath, now, epoch=epoch)
    return and_(col.is_not(None), positive) if nullable else positive
