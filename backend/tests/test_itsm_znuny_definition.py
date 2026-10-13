"""Znuny definition vectors against cmdb's parser (R8) and the importer's issue-aware wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tiqora.cmdb.definition import iter_attribute_paths
from tiqora.cmdb.znuny_definition import parse_znuny_definition as cmdb_parse
from tiqora.itsm.znuny_format import definition as fmt_definition
from tiqora.itsm.znuny_format.definition import (
    ISSUE_KEY_INVALID,
    ISSUE_KEY_UNKNOWN,
    DefinitionError,
    parse_definition_with_issues,
    parse_znuny_definition,
)
from tiqora.itsm.znuny_format.perl_literal import PerlLiteralError, parse_perl_literal
from tiqora.znuny import perl_data

FIX = Path(__file__).parent / "fixtures" / "itsm"
CMDB_FIX = Path(__file__).parent / "fixtures" / "cmdb"
SURVEY = Path(__file__).parent / "fixtures" / "znuny-itsm" / "definitions"


def test_single_implementation_is_reexported() -> None:
    assert parse_znuny_definition is cmdb_parse
    assert parse_perl_literal is perl_data.parse_perl_data
    assert PerlLiteralError is perl_data.PerlDataError
    assert fmt_definition.DefinitionError is fmt_definition.DefinitionParseError


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        (
            "[ { Key => 'Vendor', Name => 'Vendor', Searchable => 1, "
            "Input => { Type => 'Text', Size => 50, MaxLength => 50, }, }, ]",
            [
                {
                    "Key": "Vendor",
                    "Name": "Vendor",
                    "Searchable": 1,
                    "Input": {"Type": "Text", "Size": 50, "MaxLength": 50},
                }
            ],
        ),
        (
            "{ a => undef, 'b c' => \"x\\ty\", d => -1.5 } # trailing comment",
            {"a": None, "b c": "x\ty", "d": -1.5},
        ),
        ("[ 'it\\'s', \"q\\\"\" ]", ["it's", 'q"']),
    ],
)
def test_perl_literal_subset(src: str, expected: Any) -> None:
    assert parse_perl_literal(src) == expected


@pytest.mark.parametrize(
    "src",
    ["system('id')", '[ "$ENV{HOME}" ]', "[ @list ]", "{ a => sub { 1 } }", "[ 1, 2", "qw(a b)"],
)
def test_perl_literal_rejects_code(src: str) -> None:
    with pytest.raises(PerlLiteralError) as exc:
        parse_perl_literal(src)
    assert isinstance(exc.value.position, int)


def test_perl_literal_depth_limit() -> None:
    with pytest.raises(PerlLiteralError):
        parse_perl_literal("[" * 100 + "]" * 100)


def _summary(attrs: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path, a in iter_attribute_paths(attrs):
        row: dict[str, Any] = {
            "type": a.type,
            "searchable": a.searchable,
            "required": a.required,
            "count_max": a.count_max,
        }
        if a.count_default != 1:
            row["count_default"] = a.count_default
        if a.options.max_length:
            row["max_length"] = a.options.max_length
        if a.options.catalog_class:
            row["catalog_class"] = a.options.catalog_class
        if a.options.class_keys:
            row["class_keys"] = a.options.class_keys
        out[path] = row
    return out


def test_6x_perl_definition_matches_the_hand_written_vector() -> None:
    expected = json.loads((FIX / "definition_computer.json").read_text())
    expected.pop("_comment")
    res = parse_znuny_definition((CMDB_FIX / "znuny65_computer_definition.pl").read_text(), "perl")
    got = _summary(res.attributes)
    assert set(got) == set(expected)
    by_path = dict(iter_attribute_paths(res.attributes))
    for path, raw_want in expected.items():
        want = {"count_default": 1, **raw_want}
        count_min = want.pop("count_min", None)
        assert {"count_default": 1, **got[path]} == want, path
        if count_min is not None:
            assert by_path[path].count_min == count_min, path


def test_6x_and_7x_stock_definitions_normalise_identically() -> None:
    six = SURVEY.joinpath("itsm-6", "22_Computer.yml").read_text()
    seven = SURVEY.joinpath("itsm-7", "22_Computer.yml").read_text()
    a, b = parse_znuny_definition(six, "yaml"), parse_znuny_definition(seven, "yaml")
    assert [x.model_dump() for x in a.attributes] == [x.model_dump() for x in b.attributes]
    assert a.catalog_classes_used == b.catalog_classes_used and a.warnings == b.warnings


def test_definition_must_be_list_of_keyed_hashes() -> None:
    with pytest.raises(DefinitionError):
        parse_znuny_definition("{ Key => 'x' }", "perl")
    with pytest.raises(DefinitionError):
        parse_znuny_definition("[ { Name => 'no key' } ]", "perl")


def test_invalid_key_attribute_is_reported_and_left_out_not_the_whole_class() -> None:
    res = parse_definition_with_issues(
        "[ { Key => 'Good', Name => 'Good' }, { Key => 'Bad Key', Name => 'x' }, "
        "{ Key => 'Grp', Name => 'g', Sub => [ { Key => 'Zähler' }, { Key => 'Ok' } ] } ]",
        "perl",
    )
    assert [a.key for a in res.result.attributes] == ["Good", "Grp"]
    assert [s.key for s in res.result.attributes[1].sub] == ["Ok"]
    bad = [(i.code, i.path, i.key) for i in res.issues]
    assert bad == [
        (ISSUE_KEY_INVALID, "Bad Key", "Bad Key"),
        (ISSUE_KEY_INVALID, "Grp.Zähler", "Zähler"),
    ]
    assert all("nicht übernommen" in i.message for i in res.issues)


def test_unknown_attribute_keys_are_listed() -> None:
    res = parse_definition_with_issues(
        "---\n- Key: Foo\n  Name: Foo\n  Hint: shown\n  Sub:\n    - Key: Bar\n      Colour: red\n",
        "yaml",
    )
    assert [a.key for a in res.result.attributes] == ["Foo"]
    assert [(i.code, i.path, i.key) for i in res.issues] == [
        (ISSUE_KEY_UNKNOWN, "Foo", "Hint"),
        (ISSUE_KEY_UNKNOWN, "Foo.Bar", "Colour"),
    ]


def test_clean_definition_has_no_issues_and_equals_the_plain_parser() -> None:
    text = (CMDB_FIX / "znuny65_computer_definition.pl").read_text()
    res = parse_definition_with_issues(text, "perl")
    assert res.issues == []
    assert [a.model_dump() for a in res.result.attributes] == [
        a.model_dump() for a in parse_znuny_definition(text, "perl").attributes
    ]


def test_wrapper_keeps_the_parsers_limits() -> None:
    with pytest.raises(DefinitionError):
        parse_definition_with_issues("[" * 70 + "]" * 70, "perl")
    with pytest.raises(DefinitionError):
        parse_definition_with_issues("- !!python/object/apply:os.system ['true']", "yaml")
    with pytest.raises(DefinitionError):
        parse_definition_with_issues("#" * (1024 * 1024 + 1), "perl")
