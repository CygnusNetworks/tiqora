"""Class definition parser: shipped definitions, Znuny count defaults, bounds (pure)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tiqora.itsm.znuny_adapter.definitions import AttrDef, DefinitionError, parse_definition
from tiqora.itsm.znuny_adapter.profiles import PROFILES

FIXTURES = Path(__file__).parent / "fixtures" / "znuny-itsm"
CLASSES = ("Computer", "Hardware", "Location", "Network", "Software")
SHIPPED_TYPES = {
    "Text",
    "TextArea",
    "GeneralCatalog",
    "CustomerCompany",
    "Customer",
    "Date",
    "Integer",
}


def _behaviour(profile: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "behaviour" / f"{profile}.json").read_text())["questions"]["7"][
        "expected"
    ]


def _walk(defs: tuple[AttrDef, ...]) -> list[AttrDef]:
    out: list[AttrDef] = []
    for d in defs:
        out.append(d)
        out.extend(_walk(d.sub))
    return out


def _shipped(profile: str, cls: str) -> str:
    (path,) = (FIXTURES / "definitions" / profile).glob(f"*_{cls}.yml")
    return path.read_text()


@pytest.mark.parametrize("profile", sorted(PROFILES))
@pytest.mark.parametrize("cls", CLASSES)
def test_default_class_definitions_parse(profile: str, cls: str) -> None:
    fmt = PROFILES[profile].definition_format
    defs = parse_definition(_shipped(profile, cls), fmt)
    assert defs, cls
    assert all(d.key and d.name for d in _walk(defs))
    assert {d.input_type for d in _walk(defs)} <= SHIPPED_TYPES
    assert {d.input_type for d in _walk(defs)} <= set(_behaviour(profile)["input_types"])


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_shipped_definitions_equal_the_fixture_rows(profile: str) -> None:
    sql = (FIXTURES / profile / "initial_insert.sql").read_text()
    for cls in CLASSES:
        assert _shipped(profile, cls) in sql


def test_computer_attribute_details() -> None:
    defs = {d.key: d for d in parse_definition(_shipped("itsm-6", "Computer"), "yaml")}
    assert defs["Vendor"].searchable is True
    assert defs["Vendor"].input_type == "Text"
    assert defs["Vendor"].input == {"Size": 50, "MaxLength": 50}
    assert defs["OperatingSystem"].searchable is False
    assert defs["Type"].input == {"Class": "ITSM::ConfigItem::Computer::Type", "Translation": 1}
    assert defs["Type"].name == "Type"
    assert defs["CustomerID"].name == "Customer Company"
    assert (defs["CPU"].count_min, defs["CPU"].count_max, defs["CPU"].count_default) == (1, 16, 1)


def test_sub_attributes_nested() -> None:
    defs = {d.key: d for d in parse_definition(_shipped("itsm-6", "Computer"), "yaml")}
    nic = defs["NIC"]
    assert (nic.count_min, nic.count_max, nic.count_default) == (0, 10, 1)
    assert [s.key for s in nic.sub] == ["IPoverDHCP", "IPAddress"]
    assert nic.sub[0].input_type == "GeneralCatalog"
    assert nic.sub[0].input["Class"] == "ITSM::ConfigItem::YesNo"
    assert [s.key for s in defs["HardDisk"].sub] == ["Capacity"]
    assert defs["HardDisk"].sub[0].sub == ()


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_count_defaults_from_behaviour_vectors(profile: str) -> None:
    cases = _behaviour(profile)["prepare_examples"]
    assert len(cases) == 11
    for case in cases:
        attr = {"Key": "A", **case["in"]}
        if isinstance(attr.get("Sub"), list):  # the vector exercises prepare, not the key check
            attr["Sub"] = [{"Key": "B", **child} for child in attr["Sub"]]
        text = "---\n" + json.dumps([attr])  # JSON is YAML
        (got,) = parse_definition(text, "yaml")
        out = case["out"]
        assert (got.count_min, got.count_max, got.count_default) == (
            out["CountMin"],
            out["CountMax"],
            out["CountDefault"],
        ), case["name"]
        assert bool(got.sub) or ("sub_child" in out) == bool(got.sub), case["name"]
        if "sub_child" in out:
            child = got.sub[0]
            assert (child.count_min, child.count_max, child.count_default) == (
                out["sub_child"]["CountMin"],
                out["sub_child"]["CountMax"],
                out["sub_child"]["CountDefault"],
            )


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_check_examples_from_behaviour_vectors(profile: str) -> None:
    checks = _behaviour(profile)["check_examples"]
    texts = {
        "valid_yaml": "---\n- Key: A\n",
        "sub_valid": "---\n- Key: A\n  Sub:\n  - Key: B\n",
        "key_with_space": "---\n- Key: A B\n",
        "key_non_ascii": "---\n- Key: Größe\n",
        "sub_key_space": "---\n- Key: A\n  Sub:\n  - Key: 'B C'\n",
        "not_a_list": "---\nKey: A\n",
    }
    for name, text in texts.items():
        if checks[name]:
            assert parse_definition(text, "yaml"), name
        else:
            with pytest.raises(DefinitionError):
                parse_definition(text, "yaml")
    # Znuny: a stored text without the "---" prefix is the legacy Perl format -> "--- []"
    assert checks["legacy_perl"] is False
    assert _behaviour(profile)["legacy_perl_definition_reads_as"] == []
    assert parse_definition("[ { Key => 'A' } ]", "yaml") == ()


def test_empty_definitions() -> None:
    assert parse_definition("--- []\n", "yaml") == ()
    assert parse_definition("---\n", "yaml") == ()
    assert parse_definition("[]", "perl") == ()


def test_sub_not_a_list_is_dropped_and_empty_list_kept() -> None:
    (a,) = parse_definition("---\n- Key: A\n  Sub: x\n", "yaml")
    assert a.sub == ()
    (b,) = parse_definition("---\n- Key: A\n  Sub: []\n", "yaml")
    assert b.sub == ()


def test_yaml_and_perl_same_semantics() -> None:
    yaml_text = """---
- Key: Vendor
  Name: Vendor
  Searchable: 1
  Input:
    Type: Text
    Size: 50
- Key: NIC
  Name: Network Adapter
  Input:
    Type: Text
    Required: 1
  CountMin: 0
  CountMax: 10
  Sub:
  - Key: IPAddress
    Name: IP Address
    Input:
      Type: Text
    CountMax: 100
"""
    perl_text = """[
  { Key => 'Vendor', Name => 'Vendor', Searchable => 1,
    Input => { Type => 'Text', Size => 50 } },
  { Key => 'NIC', Name => 'Network Adapter', # comment
    Input => { Type => 'Text', Required => 1 },
    CountMin => 0, CountMax => 10,
    Sub => [ { Key => 'IPAddress', Name => 'IP Address',
               Input => { Type => 'Text' }, CountMax => 100 }, ], },
]"""
    from_yaml = parse_definition(yaml_text, "yaml")
    from_perl = parse_definition(perl_text, "perl")
    assert from_yaml == from_perl
    assert from_yaml[1].sub[0].count_max == 100


def test_perl_errors_become_definition_errors() -> None:
    with pytest.raises(DefinitionError, match="Perl"):
        parse_definition("[ sub { 1 } ]", "perl")
    with pytest.raises(DefinitionError):
        parse_definition("{ Key => 'A' }", "perl")


@pytest.mark.parametrize(
    "text",
    [
        "---\n- Key: A\n  CountMax: many\n",
        "---\n- just a string\n",
        "---\n- Name: no key\n",
        "---\n- [unclosed\n",
    ],
)
def test_invalid_yaml_definitions_raise(text: str) -> None:
    with pytest.raises(DefinitionError):
        parse_definition(text, "yaml")


def test_yaml_bounds() -> None:
    with pytest.raises(DefinitionError, match="depth"):
        parse_definition("---\n" + "[" * 100 + "]" * 100, "yaml")
    with pytest.raises(DefinitionError, match="larger"):
        parse_definition("---\n# " + "x" * (1024 * 1024), "yaml")
    node: dict[str, Any] = {"Key": "K"}
    for _ in range(70):
        node = {"Key": "K", "Sub": [node]}
    deep_sub = "---\n" + json.dumps([node])
    with pytest.raises(DefinitionError, match="depth"):
        parse_definition(deep_sub, "yaml")


def test_yaml_aliases_and_python_tags_are_refused() -> None:
    bomb = "---\n- &a {Key: A}\n- *a\n"
    with pytest.raises(DefinitionError, match="alias"):
        parse_definition(bomb, "yaml")
    with pytest.raises(DefinitionError):
        parse_definition("---\n- !!python/object/apply:os.system ['true']\n", "yaml")
