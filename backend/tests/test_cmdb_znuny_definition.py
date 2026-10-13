"""Znuny class definition import (D2) on the ITSM core's definition parser (one parser, R8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tiqora.cmdb.definition import iter_attribute_paths, validate_definition
from tiqora.cmdb.values import normalize_values
from tiqora.cmdb.znuny_definition import DefinitionParseError, parse_znuny_definition

FIXTURES = Path(__file__).parent / "fixtures" / "cmdb"
#: the stock class definitions of the behaviour survey (identical in Znuny 6.5 and 7.3)
SURVEY = Path(__file__).parent / "fixtures" / "znuny-itsm" / "definitions"


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


@pytest.mark.parametrize("line", ["itsm-6", "itsm-7"])
def test_stock_znuny_definitions_import_without_loss(line: str) -> None:
    """Znuny 6.5 and 7.3 store the same YAML attribute list (survey question 7): no 7.x layout."""
    res = parse_znuny_definition(SURVEY.joinpath(line, "22_Computer.yml").read_text(), "yaml")
    by_path = dict(iter_attribute_paths(res.attributes))
    assert [a.key for a in res.attributes][:4] == ["Vendor", "Model", "Description", "Type"]
    assert by_path["Type"].type == "catalog" and by_path["CustomerID"].type == "customer_company"
    assert by_path["Owner"].type == "customer_user"
    assert by_path["NIC.IPAddress"].count_max == 20
    assert (by_path["NIC.IPAddress"].count_min, by_path["NIC.IPAddress"].count_default) == (0, 0)
    # CountMin is absent for Vendor, but Vendor is not Required: optional (ruling CMDB-PM-I1)
    assert by_path["Note"].count_min == 0 and by_path["Vendor"].count_min == 0
    assert by_path["Vendor"].count_default == 1  # Znuny still shows one input row
    assert res.warnings == []
    assert res.catalog_classes_used == {
        "ITSM::ConfigItem::Computer::Type",
        "ITSM::ConfigItem::YesNo",
    }
    assert (
        validate_definition(
            res.attributes, known_catalog_classes=res.catalog_classes_used, known_class_keys=set()
        )
        == []
    )
    # every stock class reads (Location's "E-Mail" key is sanitised with a warning)
    for path in sorted(SURVEY.joinpath(line).glob("*.yml")):
        assert parse_znuny_definition(path.read_text(), "yaml").attributes, path.name


def test_seven_x_sections_document_is_not_a_definition() -> None:
    """The ``Sections``/``Content`` layout the plan assumed for 7.x does not exist in Znuny."""
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition(
            FIXTURES.joinpath("znuny73_computer_definition.yml").read_text(), "yaml"
        )


def test_yaml_without_document_marker_is_still_read() -> None:
    """Pasted text: no ``---`` is not Znuny's legacy-Perl marker here (``fmt`` is explicit)."""
    res = parse_znuny_definition("- Key: Foo\n  Name: Foo\n", "yaml")
    assert [a.key for a in res.attributes] == ["Foo"]
    assert parse_znuny_definition("\n  ---\n- Key: Bar\n", "yaml").attributes[0].key == "Bar"


def test_ci_reference_types_of_znuny() -> None:
    """``CI`` and ``CISimple`` reference one class through ``Input.CIClassName``."""
    res = parse_znuny_definition(
        "---\n- Key: Room\n  Input:\n    Type: CI\n    CIClassName: Location\n"
        "- Key: Peer\n  Input:\n    Type: CISimple\n    CIClassName: Computer\n",
        "yaml",
    )
    assert [(a.key, a.type, a.options.class_keys) for a in res.attributes] == [
        ("Room", "ci_ref", ["location"]),
        ("Peer", "ci_ref", ["computer"]),
    ]
    assert res.class_keys_used == {"location", "computer"} and res.warnings == []


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
    """Ruling CMDB-PM-I1: Znuny's ``CountMin`` (``_DefinitionPrepare``: 1 when absent, survey
    question 7) is the number of input rows shown; ``Input.Required`` decides whether a value
    is mandatory. So Tiqora's ``count_min`` takes ``CountMin`` only for required inputs."""
    a = parse_znuny_definition("[ { Key => 'A', Name => 'A' } ]", "perl").attributes[0]
    assert (a.required, a.count_min, a.count_max, a.count_default) == (False, 0, 1, 1)
    r = parse_znuny_definition(
        "[ { Key => 'R', Input => { Type => 'Text', Required => 1 } } ]", "perl"
    ).attributes[0]
    assert (r.required, r.count_min, r.count_max, r.count_default) == (True, 1, 1, 1)
    r0 = parse_znuny_definition(
        "[ { Key => 'R', CountMin => 0, Input => { Type => 'Text', Required => 1 } } ]", "perl"
    ).attributes[0]
    assert (r0.required, r0.count_min, r0.count_default) == (True, 0, 1)
    b = parse_znuny_definition(
        "[ { Key => 'B', CountMin => 0, CountMax => 0, CountDefault => 5 } ]", "perl"
    ).attributes[0]
    assert (b.count_min, b.count_max, b.count_default) == (0, 1, 1)
    c = parse_znuny_definition(
        "[ { Key => 'C', CountMin => 2, CountMax => 3 } ]", "perl"
    ).attributes[0]
    assert (c.count_min, c.count_max, c.count_default) == (0, 3, 2)


@pytest.mark.parametrize("line", ["itsm-6", "itsm-7"])
def test_imported_stock_class_accepts_only_vendor(line: str) -> None:
    """The stock Computer class: a CI with only Vendor filled is valid, the optional
    attributes without ``CountMin`` are not mandatory (13 ``too_few`` before CMDB-PM-I1)."""
    res = parse_znuny_definition(SURVEY.joinpath(line, "22_Computer.yml").read_text(), "yaml")
    values, issues = normalize_values(res.attributes, {"Vendor": [{"v": "Example"}]})
    assert issues == []
    assert values == {"Vendor": [{"v": "Example"}]}


def test_keys_znuny_rejects_fail_the_import() -> None:
    """``DefinitionCheck``: keys with whitespace or non-ASCII characters are no definition."""
    for bad in ("- Key: Serial Number\n", "- Key: Größe\n", "- Name: no key\n"):
        with pytest.raises(DefinitionParseError):
            parse_znuny_definition(bad, "yaml")


def test_bad_keys_are_sanitised_with_warning() -> None:
    res = parse_znuny_definition("[ { Key => '1st-Disk', Name => 'Disk' } ]", "perl")
    assert res.attributes[0].key == "A_1st_Disk"
    assert "1st-Disk" in res.warnings[0]


def test_unknown_keys_do_not_fail() -> None:
    """Znuny ignores attribute keys it does not know; so does the shared parser."""
    res = parse_znuny_definition("[ { Key => 'A', Name => 'A', Frobnicate => 1 } ]", "perl")
    assert [a.key for a in res.attributes] == ["A"] and res.warnings == []


def test_document_without_attribute_list_is_rejected() -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("Foo: bar\n", "yaml")


def test_yaml_flow_nesting_bomb_is_a_parse_error() -> None:
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("[" * 100000, "yaml")


def test_yaml_alias_bomb_is_rejected_quickly() -> None:
    import time

    lines = ["a0: &a0 [x, x]"]
    for i in range(1, 10):
        lines.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 9) + "]")
    started = time.monotonic()
    with pytest.raises(DefinitionParseError):
        parse_znuny_definition("\n".join(lines) + "\n", "yaml")
    assert time.monotonic() - started < 2


def test_sub_that_is_not_a_list_is_dropped() -> None:
    """``_DefinitionPrepare`` removes a ``Sub`` that is not a list; a ``Dummy`` group without
    children is kept as text with a warning."""
    res = parse_znuny_definition("- Key: Foo\n  Name: Foo\n  Sub: x\n", "yaml")
    assert res.attributes[0].sub == [] and res.attributes[0].type == "text"
    grp = parse_znuny_definition("- Key: G\n  Input:\n    Type: Dummy\n  Sub: x\n", "yaml")
    assert grp.attributes[0].type == "text"
    assert grp.warnings == ["Gruppe ohne Unterattribute bei G: als Text übernommen"]
