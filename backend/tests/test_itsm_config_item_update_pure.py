"""ConfigItemUpdate attribute merge (vectors from Znuny's ConfigItemUpdate.t)."""

from __future__ import annotations

from typing import Any

from tiqora.itsm.config_item_update import HEADER_KEYS, CiUpdate, build_update


def _attr(key: str, type_: str = "text", *, count_max: int = 1, sub: Any = None) -> dict[str, Any]:
    return {
        "key": key,
        "name": key,
        "type": type_,
        "required": False,
        "searchable": False,
        "portal": False,
        "count_min": 0,
        "count_max": count_max,
        "count_default": 1,
        "default": None,
        "help": None,
        "options": {},
        "sub": sub or [],
    }


COMPUTER_SCHEMA: dict[str, Any] = {
    "attributes": [
        _attr("Vendor"),
        _attr("CPU", count_max=16),
        _attr("Ram", count_max=4),
        _attr("HardDisk", count_max=4, sub=[_attr("Capacity")]),
        _attr("Owner", "customer_user"),
    ]
}


def _attrs(values: dict[str, list[str]]) -> dict[str, Any]:
    return {k: [{"v": v} for v in vs] for k, vs in values.items()}


def _values(attrs: dict[str, Any], key: str) -> list[str]:
    return ["" if e["v"] is None else str(e["v"]) for e in attrs.get(key, [])]


def test_header_keys() -> None:
    assert HEADER_KEYS == (
        "ConfigItemID",
        "Name",
        "ClassName",
        "ClassID",
        "DeplStateID",
        "InciStateID",
        "DeplStateName",
        "InciStateName",
    )


def test_numbered_keys_with_sub_attribute() -> None:
    upd = build_update(
        {
            "ConfigItemID": "5",
            "Vendor::1": "NEW-Vendor",
            "CPU::1": "NEW-CPU",
            "Ram::1": "NEW-Ram",
            "HardDisk::1": "NEW-HardDisk1",
            "HardDisk::1::Capacity::1": "12",
            "HardDisk::2": "NEW-HardDisk2",
            "HardDisk::2::Capacity::1": "44",
            "UserID": "1",
        },
        current_attributes=_attrs({"Vendor": ["old"], "CPU": ["old-cpu"]}),
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
    )
    assert isinstance(upd, CiUpdate)
    assert _values(upd.attributes, "Vendor") == ["NEW-Vendor"]
    assert _values(upd.attributes, "CPU") == ["NEW-CPU"]
    assert _values(upd.attributes, "Ram") == ["NEW-Ram"]
    assert _values(upd.attributes, "HardDisk") == ["NEW-HardDisk1", "NEW-HardDisk2"]
    assert [_values(e["s"], "Capacity") for e in upd.attributes["HardDisk"]] == [["12"], ["44"]]
    assert upd.name is None and upd.depl_state_name is None


def test_cpu_list_and_vendor_delete() -> None:
    upd = build_update(
        {
            "ConfigItemID": "5",
            "Vendor::1": "",
            "CPU::": "NEW-2-CPU, NEW-2-CPU2",
            "Ram::1": "NEW-2-Ram",
            "DeplStateName": "Planned",
            "InciStateName": "Operational",
            "UserID": "1",
        },
        current_attributes=_attrs(
            {"Vendor": ["Vendor-Test"], "CPU": ["CPU-Test"], "Ram": ["Ram-Test"]}
        ),
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
    )
    assert upd.depl_state_name == "Planned"
    assert upd.inci_state_name == "Operational"
    assert _values(upd.attributes, "CPU") == ["NEW-2-CPU", "NEW-2-CPU2"]
    assert _values(upd.attributes, "Vendor") == [""]
    assert _values(upd.attributes, "Ram") == ["NEW-2-Ram"]


def test_key_without_separator_is_a_list_and_untouched_attributes_survive() -> None:
    upd = build_update(
        {"ConfigItemID": "5", "CPU": "a,b", "Name": "srv-01.example.test", "DeplStateID": "7"},
        current_attributes=_attrs({"Vendor": ["Keep"]}),
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
    )
    assert _values(upd.attributes, "CPU") == ["a", "b"]
    assert _values(upd.attributes, "Vendor") == ["Keep"]
    assert upd.name == "srv-01.example.test"
    assert upd.depl_state_id == 7
    assert upd.inci_state_id is None


def test_keys_not_in_definition_are_ignored() -> None:
    upd = build_update(
        {
            "ConfigItemID": "5",
            "Unknown::1": "x",
            "Bogus": "y",
            "Ram::1": "r",
            "ConfigItemNumber": "1",
        },
        current_attributes={},
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
    )
    assert set(upd.attributes) == {"Ram"}


def test_separator_override() -> None:
    upd = build_update(
        {"ConfigItemID": "5", "CPU::": "a, b;c , d", "Separator": ";"},
        current_attributes={},
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
    )
    assert _values(upd.attributes, "CPU") == ["a, b", "c , d"]
    default = build_update(
        {"ConfigItemID": "5", "CPU::": "a;b"},
        current_attributes={},
        schema=COMPUTER_SCHEMA,
        customer_lookup=None,
        separator=";",
    )
    assert _values(default.attributes, "CPU") == ["a", "b"]


def test_customer_lookup_unique_match_only() -> None:
    def lookup(value: str) -> list[str]:
        return {"Anna": ["anna.example"], "Dup": ["a", "b"]}.get(value, [])

    unique = build_update(
        {"ConfigItemID": "5", "Owner::1": "Anna"},
        current_attributes=_attrs({"Owner": ["old.login"]}),
        schema=COMPUTER_SCHEMA,
        customer_lookup=lookup,
    )
    assert _values(unique.attributes, "Owner") == ["anna.example"]
    ambiguous = build_update(
        {"ConfigItemID": "5", "Owner::1": "Dup"},
        current_attributes=_attrs({"Owner": ["old.login"]}),
        schema=COMPUTER_SCHEMA,
        customer_lookup=lookup,
    )
    assert _values(ambiguous.attributes, "Owner") == ["old.login"]
