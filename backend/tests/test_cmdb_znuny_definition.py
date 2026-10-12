"""Znuny class definition import (D2) on top of the step-0 Perl parser and yaml.safe_load."""

from __future__ import annotations

from pathlib import Path

import pytest

from tiqora.cmdb.definition import iter_attribute_paths, validate_definition
from tiqora.cmdb.znuny_definition import DefinitionParseError, parse_znuny_definition

FIXTURES = Path(__file__).parent / "fixtures" / "cmdb"


def test_perl_nested_structure_with_escapes_and_sub() -> None:
    res = parse_znuny_definition(
        "[ { Key => 'NIC', Name => 'Netzwerk\\'karte', CountMax => 3, "
        "Sub => [ { Key => 'IPAddress', Name => 'IP' } ] } ]",
        "perl",
    )
    nic = res.attributes[0]
    assert nic.name == "Netzwerk'karte"
    assert nic.count_max == 3
    assert [s.key for s in nic.sub] == ["IPAddress"]


@pytest.mark.parametrize(
    "bad", ["[ system('x') ]", "{ Key => $var }", "[ 'a' . 'b' ]", "[ qw(a b) ]"]
)
def test_perl_code_is_rejected(bad: str) -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition(bad, "perl")


def test_oversized_and_too_deep_input_is_rejected() -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("[" * 70 + "]" * 70, "perl")
    deep = "- " * 70 + "x"
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition(deep, "yaml")
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("#" * (1024 * 1024 + 1), "perl")


def test_yaml_is_loaded_safely() -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("- !!python/object/apply:os.system ['true']", "yaml")


def test_znuny65_fixture_maps_types_counts_and_flags() -> None:
    res = parse_znuny_definition(
        FIXTURES.joinpath("znuny65_computer_definition.pl").read_text(), "perl"
    )
    by_path = dict(iter_attribute_paths(res.attributes))
    assert by_path["Vendor"].type == "text" and by_path["Vendor"].searchable
    assert by_path["Vendor"].options.max_length == 50
    assert by_path["CPU"].count_max == 16
    assert by_path["NIC.IPAddress"].type == "text"  # Znuny 6.5 has no IP input type
    assert by_path["NIC.IPAddress"].searchable and by_path["NIC.IPAddress"].count_max == 20
    assert by_path["Type"].type == "catalog"
    assert by_path["Type"].options.catalog_class == "ITSM::ConfigItem::Computer::Type"
    assert by_path["Type"].required is True
    assert by_path["Location"].type == "ci_ref"
    assert by_path["Location"].options.class_keys == ["location"]
    assert res.catalog_classes_used == {"ITSM::ConfigItem::Computer::Type"}
    assert res.class_keys_used == {"location"}
    assert res.warnings == []
    assert (
        validate_definition(
            res.attributes,
            known_catalog_classes=res.catalog_classes_used,
            known_class_keys={"location"},
        )
        == []
    )


def test_znuny73_yaml_sections_are_merged_in_order() -> None:
    res = parse_znuny_definition(
        FIXTURES.joinpath("znuny73_computer_definition.yml").read_text(), "yaml"
    )
    assert [a.key for a in res.attributes] == [
        "Vendor",
        "Model",
        "Description",
        "SerialNumber",
        "Memory",
    ]
    memory = res.attributes[-1]
    assert memory.type == "integer"
    assert str(memory.options.min) == "1" and str(memory.options.max) == "4096"
    assert res.attributes[3].required is True


def test_unknown_input_type_becomes_text_with_warning() -> None:
    res = parse_znuny_definition("- Key: Foo\n  Name: Foo\n  Input:\n    Type: Fancy\n", "yaml")
    assert res.attributes[0].type == "text"
    assert res.warnings == ["Unbekannter Typ Fancy bei Foo: als Text übernommen"]


def test_perl_truthy_flags() -> None:
    res = parse_znuny_definition(
        "[ { Key => 'A', Name => 'A', Searchable => '0', "
        "Input => { Type => 'Text', Required => '1' } } ]",
        "perl",
    )
    assert res.attributes[0].searchable is False
    assert res.attributes[0].required is True


def test_count_defaults_follow_znuny() -> None:
    a = parse_znuny_definition("[ { Key => 'A', Name => 'A' } ]", "perl").attributes[0]
    assert (a.count_min, a.count_max, a.count_default) == (0, 1, 1)


def test_bad_keys_are_sanitised_with_warning() -> None:
    res = parse_znuny_definition("[ { Key => '1st-Disk', Name => 'Disk' } ]", "perl")
    assert res.attributes[0].key == "A_1st_Disk"
    assert "1st-Disk" in res.warnings[0]


def test_unknown_keys_warn_but_do_not_fail() -> None:
    res = parse_znuny_definition("[ { Key => 'A', Name => 'A', Frobnicate => 1 } ]", "perl")
    assert len(res.attributes) == 1
    assert any("Frobnicate" in w for w in res.warnings)


def test_document_without_attribute_list_is_rejected() -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("Foo: bar\n", "yaml")
