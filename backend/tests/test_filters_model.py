"""Filter language: model + validation (pure, no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from tiqora.filters import (
    MAX_CONDITIONS,
    MAX_DEPTH,
    Filter,
    FilterError,
    FilterFieldSpec,
    iter_conditions,
    parse_datetime_value,
    relative_delta,
    validate_filter,
)

FIELDS = {
    "queue_id": FilterFieldSpec(key="queue_id", type="id"),
    "title": FilterFieldSpec(key="title", type="string"),
    "created": FilterFieldSpec(key="created", type="datetime"),
    "locked": FilterFieldSpec(key="locked", type="bool"),
    "channel": FilterFieldSpec(key="channel", type="enum", enum_values=("email", "phone")),
}


def _validate(raw: dict[str, Any]) -> Filter:
    flt = Filter.model_validate(raw)
    validate_filter(flt, FIELDS.get)
    return flt


def test_round_trip_with_not_alias() -> None:
    raw = {
        "version": 1,
        "where": {
            "all": [
                {"field": "queue_id", "op": "in", "value": [1, 2]},
                {"not": {"field": "locked", "op": "is_true"}},
                {"any": [{"field": "title", "op": "contains", "value": "Drucker"}]},
            ]
        },
    }
    flt = _validate(raw)
    assert flt.model_dump(by_alias=True, exclude_none=True) == raw


def test_condition_with_group_keys_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Filter.model_validate({"where": {"field": "title", "op": "eq", "value": "x", "all": []}})


@pytest.mark.parametrize(
    ("cond", "path_suffix"),
    [
        ({"field": "nope", "op": "eq", "value": 1}, "field"),
        ({"field": "queue_id", "op": "contains", "value": 1}, "op"),
        ({"field": "queue_id", "op": "in", "value": []}, "value"),
        ({"field": "queue_id", "op": "in", "value": 3}, "value"),
        ({"field": "locked", "op": "is_true", "value": 1}, "value"),
        ({"field": "channel", "op": "eq", "value": "fax"}, "value"),
        ({"field": "created", "op": "within_last", "value": {"amount": 0, "unit": "day"}}, "value"),
        ({"field": "created", "op": "between", "value": {"from": "2026-01-01"}}, "value"),
        ({"field": "created", "op": "before", "value": "gestern"}, "value"),
    ],
)
def test_invalid_conditions_name_the_path(cond: dict[str, Any], path_suffix: str) -> None:
    with pytest.raises(FilterError) as exc:
        _validate({"where": {"all": [cond]}})
    assert exc.value.path == f"where.all[0].{path_suffix}"


def test_depth_and_size_limits() -> None:
    node: dict[str, Any] = {"field": "locked", "op": "is_true"}
    for _ in range(MAX_DEPTH + 1):
        node = {"not": node}
    with pytest.raises(FilterError, match="tief"):
        _validate({"where": node})
    many = [{"field": "locked", "op": "is_true"}] * (MAX_CONDITIONS + 1)
    with pytest.raises(FilterError):
        _validate({"where": {"all": many}})


def test_datetime_values_become_naive_utc() -> None:
    assert parse_datetime_value("2026-03-29T03:30:00+02:00", path="p") == datetime(
        2026, 3, 29, 1, 30
    )
    assert parse_datetime_value("2026-03-29", path="p") == datetime(2026, 3, 29)


def test_relative_delta_units() -> None:
    assert relative_delta({"amount": 2, "unit": "week"}, path="p") == timedelta(days=14)


def test_valid_filter_at_depth_limit_and_iter_paths() -> None:
    node: dict[str, Any] = {"field": "locked", "op": "is_true"}
    for _ in range(MAX_DEPTH - 1):
        node = {"not": node}
    flt = _validate({"where": node})
    paths = [p for p, _ in iter_conditions(flt.where)]
    assert paths == ["where" + ".not" * (MAX_DEPTH - 1)]
    assert list(iter_conditions(None)) == []
