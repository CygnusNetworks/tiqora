"""Golden SQL for ``compile_conditions`` on both dialects (pure, no database)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, SmallInteger, String, Table
from sqlalchemy.dialects import mysql, postgresql

from tiqora.filters import Filter, FilterError, FilterFieldSpec
from tiqora.reporting.filter_sql import compile_conditions

NOW = datetime(2024, 6, 12, 10, 0)

_t = Table(
    "toy",
    MetaData(),
    Column("name", String(50), nullable=True),
    Column("queue_id", SmallInteger, nullable=False),
    Column("owner_id", Integer, nullable=True),
    Column("created", DateTime, nullable=False),
    Column("flag", Boolean, nullable=False),
    Column("kind", String(20), nullable=False),
)
_SPECS = {
    "name": FilterFieldSpec("name", "string"),
    "queue": FilterFieldSpec("queue", "id"),
    "owner": FilterFieldSpec("owner", "id"),
    "created": FilterFieldSpec("created", "datetime"),
    "flag": FilterFieldSpec("flag", "bool"),
    "kind": FilterFieldSpec("kind", "enum", enum_values=("a", "b")),
}
_COLS = {
    "name": _t.c.name,
    "queue": _t.c.queue_id,
    "owner": _t.c.owner_id,
    "created": _t.c.created,
    "flag": _t.c.flag,
    "kind": _t.c.kind,
}


def _resolve(key: str) -> tuple[FilterFieldSpec, Any] | None:
    return (_SPECS[key], _COLS[key]) if key in _SPECS else None


def _sql(where: dict[str, Any], dialect: Any) -> str:
    flt = Filter.model_validate({"version": 1, "where": where})
    expr = compile_conditions(flt, _resolve, now=NOW)
    assert expr is not None
    return str(expr.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))


DIALECTS = pytest.mark.parametrize("dialect", [mysql.dialect, postgresql.dialect])


def _c(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


@DIALECTS
def test_eq_and_in(dialect: Any) -> None:
    assert _sql(_c("queue", "eq", 5), dialect) == "toy.queue_id = 5"
    assert _sql(_c("queue", "in", [1, 2]), dialect) == "toy.queue_id IN (1, 2)"


@DIALECTS
def test_neq_and_not_in_include_null_rows(dialect: Any) -> None:
    assert _sql(_c("owner", "neq", 7), dialect) == "toy.owner_id IS NULL OR toy.owner_id != 7"
    assert (
        _sql(_c("owner", "not_in", [7, 8]), dialect)
        == "toy.owner_id IS NULL OR (toy.owner_id NOT IN (7, 8))"
    )
    # NOT NULL column: no IS NULL branch
    assert _sql(_c("queue", "neq", 7), dialect) == "toy.queue_id != 7"


@DIALECTS
def test_out_of_range_id_never_reaches_the_database(dialect: Any) -> None:
    # SMALLINT column: asyncpg refuses to bind 70000, so it is folded away
    assert _sql(_c("queue", "eq", 70000), dialect) == "false"
    assert _sql(_c("queue", "in", [1, 70000]), dialect) == "toy.queue_id IN (1)"
    assert _sql(_c("queue", "neq", 70000), dialect) == "true"


def test_contains_escapes_like_wildcards_and_ignores_case() -> None:
    my = _sql(_c("name", "contains", "a%b"), mysql.dialect).replace("%%", "%")
    assert "toy.name IS NOT NULL AND lower(toy.name) LIKE lower('%a\\\\%b%') ESCAPE '\\\\'" in my
    pg = _sql(_c("name", "contains", "a%b"), postgresql.dialect).replace("%%", "%")
    # literal rendering doubles the backslash: the bound value is a\\%b with ESCAPE '\\'
    assert "toy.name ILIKE '%a\\\\%b%' ESCAPE '\\\\'" in pg


@DIALECTS
def test_starts_with_escapes_underscore(dialect: Any) -> None:
    sql = _sql(_c("name", "starts_with", "a_b"), dialect)
    assert "a\\_b%" in sql.replace("\\\\", "\\")


@DIALECTS
def test_between_datetime_is_half_open(dialect: Any) -> None:
    sql = _sql(
        _c("created", "between", {"from": "2024-01-01", "to": "2024-02-01"}),
        dialect,
    )
    assert sql == ("toy.created >= '2024-01-01 00:00:00' AND toy.created < '2024-02-01 00:00:00'")


@DIALECTS
def test_within_last_uses_the_given_now(dialect: Any) -> None:
    sql = _sql(_c("created", "within_last", {"amount": 2, "unit": "day"}), dialect)
    assert sql == "toy.created >= '2024-06-10 10:00:00'"
    older = _sql(_c("created", "older_than", {"amount": 3, "unit": "hour"}), dialect)
    assert older == "toy.created < '2024-06-12 07:00:00'"


@DIALECTS
def test_is_true_and_is_false_on_a_bool(dialect: Any) -> None:
    assert _sql(_c("flag", "is_true"), dialect) == "toy.flag IS true"
    assert _sql(_c("flag", "is_false"), dialect) == "toy.flag IS false"


@DIALECTS
def test_nested_any_and_not(dialect: Any) -> None:
    sql = _sql(
        {
            "all": [
                {"any": [_c("queue", "eq", 1), _c("kind", "eq", "a")]},
                {"not": _c("flag", "is_true")},
            ]
        },
        dialect,
    )
    assert sql == "(toy.queue_id = 1 OR toy.kind = 'a') AND toy.flag IS NOT true"


@DIALECTS
def test_is_empty_on_string(dialect: Any) -> None:
    assert _sql(_c("name", "is_empty"), dialect) == "toy.name IS NULL OR toy.name = ''"


def test_empty_filter_compiles_to_none() -> None:
    assert compile_conditions(Filter(), _resolve, now=NOW) is None


@pytest.mark.parametrize(
    "where,path",
    [
        (_c("nope", "eq", 1), "where.field"),
        ({"all": [_c("queue", "eq", 1), _c("nope", "eq", 1)]}, "where.all[1].field"),
        (_c("queue", "contains", "x"), "where.op"),
        (_c("kind", "eq", "zzz"), "where.value"),
        ({"not": {"any": [_c("name", "eq", 3)]}}, "where.not.any[0].value"),
    ],
)
def test_invalid_conditions_raise_filter_error_with_path(where: dict[str, Any], path: str) -> None:
    flt = Filter.model_validate({"version": 1, "where": where})
    with pytest.raises(FilterError) as exc:
        compile_conditions(flt, _resolve, now=NOW)
    assert exc.value.path == path
