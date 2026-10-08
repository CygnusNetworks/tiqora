"""Filter language: field catalog, limits and validation. Pure, no database access."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from tiqora.filters.model import AllOf, AnyOf, Condition, Filter, FilterNode, Not

FieldType = Literal["id", "string", "int", "datetime", "bool", "enum"]

OPS_BY_TYPE: dict[FieldType, frozenset[str]] = {
    "id": frozenset({"eq", "neq", "in", "not_in"}),
    "string": frozenset(
        {"eq", "neq", "in", "not_in", "contains", "starts_with", "is_empty", "is_not_empty"}
    ),
    "int": frozenset({"eq", "neq", "lt", "lte", "gt", "gte", "between", "in"}),
    "datetime": frozenset(
        {"before", "after", "between", "within_last", "older_than", "is_empty", "is_not_empty"}
    ),
    "bool": frozenset({"is_true", "is_false"}),
    "enum": frozenset({"eq", "neq", "in", "not_in"}),
}

MAX_DEPTH = 6
MAX_CONDITIONS = 100
MAX_IN_VALUES = 1000

_NO_VALUE_OPS = frozenset({"is_empty", "is_not_empty", "is_true", "is_false"})
_LIST_OPS = frozenset({"in", "not_in"})
_RELATIVE_OPS = frozenset({"within_last", "older_than"})
_SCALAR_OPS = frozenset(
    {"eq", "neq", "contains", "starts_with", "lt", "lte", "gt", "gte", "before", "after"}
)
_UNIT_SECONDS = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}


@dataclass(frozen=True, slots=True)
class FilterFieldSpec:
    key: str
    type: FieldType
    label_key: str | None = None  # frontend i18n key ("ticketFilter.fields.queue_id")
    label: str | None = None  # literal label (dynamic fields use the DF label)
    ops: frozenset[str] | None = None  # None -> OPS_BY_TYPE[type]
    enum_values: tuple[str, ...] = ()
    options_source: str | None = None  # key for POST /reference/field-options

    def allowed_ops(self) -> frozenset[str]:
        return self.ops if self.ops is not None else OPS_BY_TYPE[self.type]


class FilterError(ValueError):
    def __init__(self, message: str, *, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def parse_datetime_value(value: Any, *, path: str) -> datetime:
    """ISO-8601 string -> naive UTC datetime (offsets converted, naive taken as UTC)."""
    if not isinstance(value, str):
        raise FilterError("Bitte ein Datum im Format JJJJ-MM-TT angeben.", path=path)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise FilterError(f"„{value}“ ist kein gültiges Datum.", path=path) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def relative_delta(value: Any, *, path: str) -> timedelta:
    if (
        not isinstance(value, dict)
        or set(value) != {"amount", "unit"}
        or isinstance(value["amount"], bool)
        or not isinstance(value["amount"], int)
        or value["amount"] < 1
        or value["unit"] not in _UNIT_SECONDS
    ):
        raise FilterError(
            "Bitte eine Anzahl (mindestens 1) und eine Einheit "
            "(Minute, Stunde, Tag oder Woche) angeben.",
            path=path,
        )
    return timedelta(seconds=value["amount"] * _UNIT_SECONDS[value["unit"]])


def iter_conditions(
    node: FilterNode | None, path: str = "where"
) -> Iterator[tuple[str, Condition]]:
    """Yield ``(path, condition)`` for every leaf, depth first."""
    if node is None:
        return
    if isinstance(node, Condition):
        yield path, node
    elif isinstance(node, AllOf):
        for i, child in enumerate(node.all):
            yield from iter_conditions(child, f"{path}.all[{i}]")
    elif isinstance(node, AnyOf):
        for i, child in enumerate(node.any):
            yield from iter_conditions(child, f"{path}.any[{i}]")
    else:
        yield from iter_conditions(node.not_, f"{path}.not")


def _check_depth(node: FilterNode, path: str, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise FilterError("Der Filter ist zu tief verschachtelt.", path=path)
    if isinstance(node, AllOf):
        for i, child in enumerate(node.all):
            _check_depth(child, f"{path}.all[{i}]", depth + 1)
    elif isinstance(node, AnyOf):
        for i, child in enumerate(node.any):
            _check_depth(child, f"{path}.any[{i}]", depth + 1)
    elif isinstance(node, Not):
        _check_depth(node.not_, f"{path}.not", depth + 1)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_item(spec: FilterFieldSpec, v: Any, path: str) -> None:
    if spec.type in ("id", "int"):
        if not _is_int(v):
            raise FilterError("Bitte eine ganze Zahl angeben.", path=path)
    elif spec.type == "string":
        if not isinstance(v, str):
            raise FilterError("Bitte einen Text angeben.", path=path)
    elif spec.type == "enum":
        if v not in spec.enum_values:
            raise FilterError(
                f"„{v}“ ist kein erlaubter Wert. Erlaubt: {', '.join(spec.enum_values)}.",
                path=path,
            )
    elif spec.type == "datetime":
        parse_datetime_value(v, path=path)


def _check_value(spec: FilterFieldSpec, cond: Condition, path: str) -> None:
    op, value = cond.op, cond.value
    if op in _NO_VALUE_OPS:
        if value is not None:
            raise FilterError(f"Für „{op}“ darf kein Wert angegeben werden.", path=path)
    elif op in _LIST_OPS:
        if not isinstance(value, list) or not value:
            raise FilterError("Bitte mindestens einen Wert als Liste angeben.", path=path)
        if len(value) > MAX_IN_VALUES:
            raise FilterError(f"Es sind höchstens {MAX_IN_VALUES} Werte erlaubt.", path=path)
        for i, item in enumerate(value):
            _check_item(spec, item, f"{path}[{i}]")
    elif op == "between":
        if not isinstance(value, dict) or set(value) != {"from", "to"}:
            raise FilterError("Bitte „von“ und „bis“ angeben.", path=path)
        _check_item(spec, value["from"], f"{path}.from")
        _check_item(spec, value["to"], f"{path}.to")
    elif op in _RELATIVE_OPS:
        relative_delta(value, path=path)
    elif op in _SCALAR_OPS:
        if value is None or isinstance(value, (list, dict)):
            raise FilterError("Bitte genau einen Wert angeben.", path=path)
        _check_item(spec, value, path)


def validate_filter(flt: Filter, resolve_field: Callable[[str], FilterFieldSpec | None]) -> None:
    """Raise ``FilterError`` (with a path like ``where.all[2].value``) on the first problem."""
    if flt.where is None:
        return
    _check_depth(flt.where, "where", 1)
    for count, (path, cond) in enumerate(iter_conditions(flt.where), start=1):
        if count > MAX_CONDITIONS:
            raise FilterError(
                f"Der Filter hat zu viele Bedingungen (höchstens {MAX_CONDITIONS}).", path=path
            )
        spec = resolve_field(cond.field)
        if spec is None:
            raise FilterError(f"Unbekanntes Feld „{cond.field}“.", path=f"{path}.field")
        if cond.op not in spec.allowed_ops():
            raise FilterError(f"Für dieses Feld ist „{cond.op}“ nicht möglich.", path=f"{path}.op")
        _check_value(spec, cond, f"{path}.value")
