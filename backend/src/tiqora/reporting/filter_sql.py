"""Compile the wave-0 JSON filter language onto arbitrary report columns.

Same JSON, validation and predicate semantics as the ticket compiler: both
build on :mod:`tiqora.filters.predicates`. The caller ANDs the viewer's
permission scope outside the returned expression, so no ``any`` / ``not``
branch can widen it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, not_, or_

from tiqora.filters import (
    AllOf,
    AnyOf,
    Condition,
    Filter,
    FilterError,
    FilterFieldSpec,
    FilterNode,
    validate_filter,
)
from tiqora.filters.predicates import column_predicate

Resolver = Callable[[str], tuple[FilterFieldSpec, ColumnElement[Any]] | None]


def compile_conditions(
    flt: Filter, resolve: Resolver, *, now: datetime
) -> ColumnElement[bool] | None:
    """SQL predicate for ``flt`` (``None`` when the filter has no condition).

    ``resolve(key)`` returns the field spec and column expression, or ``None``
    for an unknown key (-> :class:`FilterError` with the condition's path).
    Datetime conditions compare naive UTC values; ``neq`` / ``not_in`` include
    NULL rows; ``contains`` / ``starts_with`` are case-insensitive escaped LIKEs.
    """
    validate_filter(flt, lambda key: _spec(resolve, key))
    if flt.where is None:
        return None
    return _node(flt.where, "where", resolve, now)


def _spec(resolve: Resolver, key: str) -> FilterFieldSpec | None:
    hit = resolve(key)
    return None if hit is None else hit[0]


def _node(node: FilterNode, path: str, resolve: Resolver, now: datetime) -> ColumnElement[bool]:
    if isinstance(node, Condition):
        hit = resolve(node.field)
        if hit is None:  # pragma: no cover - validate_filter rejects unknown fields
            raise FilterError(f"Unbekanntes Feld „{node.field}“.", path=f"{path}.field")
        spec, col = hit
        return column_predicate(col, spec.type, node, f"{path}.value", now)
    if isinstance(node, AllOf):
        return and_(*[_node(c, f"{path}.all[{i}]", resolve, now) for i, c in enumerate(node.all)])
    if isinstance(node, AnyOf):
        return or_(*[_node(c, f"{path}.any[{i}]", resolve, now) for i, c in enumerate(node.any)])
    return not_(_node(node.not_, f"{path}.not", resolve, now))
