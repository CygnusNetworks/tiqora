"""Class definition model: validation and change detection (pure, no database)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests._cmdb_helpers import ATTRS
from tiqora.cmdb.definition import (
    AttributeDef,
    definition_changes,
    dump_definition_json,
    iter_attribute_paths,
    parse_definition_json,
    validate_definition,
)


def _a(**kw: Any) -> AttributeDef:
    return AttributeDef(**{"key": "Vendor", "name": "Hersteller", "type": "text", **kw})


def _validate(attrs: list[AttributeDef]) -> list[Any]:
    return validate_definition(attrs, known_catalog_classes=set(), known_class_keys=set())


def test_duplicate_sibling_key_is_an_issue() -> None:
    issues = _validate([_a(), _a()])
    assert [i.code for i in issues] == ["duplicate_key"]


def test_same_key_on_different_levels_is_fine() -> None:
    nic = _a(key="NIC", sub=[_a(key="Vendor")])
    assert _validate([_a(), nic]) == []


def test_depth_limit() -> None:
    node = _a(key="L5")
    for i in range(4, -1, -1):
        node = _a(key=f"L{i}", type="group", sub=[node])
    issues = _validate([node])
    assert [(i.path, i.code) for i in issues] == [("L0.L1.L2.L3.L4.L5", "depth")]


def test_depth_five_is_allowed() -> None:
    node = _a(key="L4")
    for i in range(3, -1, -1):
        node = _a(key=f"L{i}", type="group", sub=[node])
    assert _validate([node]) == []


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"count_min": 2, "count_max": 1}, "count_range"),
        ({"count_default": 3, "count_max": 2}, "count_range"),
        ({"count_max": 101}, "count_range"),
        ({"type": "group"}, "group_without_sub"),
        ({"type": "attachment", "searchable": True}, "searchable_not_allowed"),
        ({"type": "catalog"}, "catalog_class_missing"),
        ({"type": "catalog", "options": {"catalog_class": "Nope"}}, "catalog_class_missing"),
        ({"type": "ci_ref", "options": {"class_keys": ["nope"]}}, "unknown_class_key"),
        ({"type": "money", "options": {"currency": "euro"}}, "bad_currency"),
        ({"type": "integer", "default": "x"}, "bad_default"),
        ({"key": "1bad"}, "bad_key"),
    ],
)
def test_definition_issue_codes(kw: dict[str, Any], code: str) -> None:
    issues = validate_definition(
        [_a(**kw)],
        known_catalog_classes={"ITSM::ConfigItem::YesNo"},
        known_class_keys={"cmdb4srv"},
    )
    assert code in [i.code for i in issues]


def test_known_catalog_and_class_keys_are_accepted() -> None:
    attrs = [
        _a(key="Flag", type="catalog", options={"catalog_class": "ITSM::ConfigItem::YesNo"}),
        _a(key="Loc", type="ci_ref", options={"class_keys": ["cmdb4srv"]}),
        _a(key="Cost", type="money", options={"currency": "EUR"}, default="12.5"),
    ]
    issues = validate_definition(
        attrs,
        known_catalog_classes={"ITSM::ConfigItem::YesNo"},
        known_class_keys={"cmdb4srv"},
    )
    assert issues == []


def test_server_attrs_are_a_valid_definition() -> None:
    assert _validate(ATTRS) == []


def test_unknown_field_is_rejected_by_the_model() -> None:
    with pytest.raises(ValueError):
        AttributeDef.model_validate({"key": "A", "name": "A", "type": "text", "bogus": 1})


def test_definition_changes_reports_removed_and_type_change() -> None:
    old = [_a(), _a(key="CPU", type="integer")]
    new = [_a(), _a(key="CPU", type="text"), _a(key="RAM", type="decimal")]
    kinds = {(c.path, c.kind) for c in definition_changes(old, new)}
    assert kinds == {("CPU", "type_changed"), ("RAM", "added")}


def test_definition_changes_removed_count_label_and_flags() -> None:
    old = [_a(), _a(key="CPU", type="integer")]
    new = [_a(name="Anbieter", required=True, count_max=2)]
    kinds = {(c.path, c.kind) for c in definition_changes(old, new)}
    assert kinds == {
        ("CPU", "removed"),
        ("Vendor", "renamed_label"),
        ("Vendor", "count_changed"),
        ("Vendor", "flags_changed"),
    }


def test_definition_changes_do_not_repeat_children_of_a_removed_parent() -> None:
    old = [_a(key="NIC", sub=[_a(key="Ip")])]
    assert [(c.path, c.kind) for c in definition_changes(old, [])] == [("NIC", "removed")]


def test_definition_changes_identical_is_empty() -> None:
    assert definition_changes(ATTRS, list(ATTRS)) == []


def test_iter_attribute_paths_is_depth_first() -> None:
    paths = [p for p, _ in iter_attribute_paths([_a(key="NIC", sub=[_a(key="Ip")]), _a()])]
    assert paths == ["NIC", "NIC.Ip", "Vendor"]


def test_json_round_trip_is_deterministic() -> None:
    dumped = dump_definition_json(ATTRS)
    assert parse_definition_json(dumped) == ATTRS
    assert dump_definition_json(parse_definition_json(dumped)) == dumped
    assert json.loads(dumped)[0]["key"] == "Vendor"


def test_broken_json_reads_as_empty() -> None:
    assert parse_definition_json("{not json") == []
    assert parse_definition_json('{"a": 1}') == []
    assert parse_definition_json('[{"key": "A"}]') == []
