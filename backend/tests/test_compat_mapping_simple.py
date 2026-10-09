"""Port of Znuny's Simple.t vectors plus Tiqora-specific Perl-quirk checks.

All 26 ``@MappingTests`` entries of scripts/test/GenericInterface/Mapping/Simple.t
(the file contains 27 ``Name =>`` lines; one is the web service name).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from tiqora.api.compat.mapping import MappingConfigError, MappingError, build_mapper

_NO_CONFIG: Any = object()


@dataclass(frozen=True)
class SimpleCase:
    name: str
    config: Any
    data: Any
    result: Any
    config_success: bool
    result_success: bool
    check_time: bool = False


_KEEP = {"MapType": "Keep"}

# Simple.t reads StdAttachment-Test1.{xls,txt,doc,png,pdf}; the vectors only need five distinct
# large string payloads, so they are generated here (no dependency on the Znuny source tree).
_ATTACHMENTS = {ext: (f"content-of-{ext}\n" * 400) for ext in ("xls", "txt", "doc", "png", "pdf")}
_LARGE_HASH = {f"Key_{k}": f"Value_{k}" for k in range(10001)}
_LARGE_HASH_NEW = {f"Key_{k}": "new_value" for k in range(10001)}
_LARGE_ATT: dict[str, str] = {}
_LARGE_ATT_RESULT: dict[str, str] = {}
for _k in range(501):
    for _f in ("xls", "txt", "doc", "png", "pdf"):
        _LARGE_ATT[f"Attachment_{_k}{_f}"] = _ATTACHMENTS[_f]
        _LARGE_ATT_RESULT[f"Attachment_{_k}{_f}"] = _ATTACHMENTS["txt"]

_VALUE_MAP = {
    "first": {"ValueMapExact": {"1": "one", "2": "two", "3": "three"}},
    "second": {"ValueMapRegEx": {"[rR]eg[eE]x": "regular expression"}},
}
_NUMS = {"one": "one", "two": "two", "three": "three"}


def _vm(default: Any = _NO_CONFIG, value_map: Any = _VALUE_MAP) -> dict[str, Any]:
    cfg: dict[str, Any] = {"KeyMapDefault": _KEEP, "ValueMap": value_map}
    if default is not _NO_CONFIG:
        cfg["ValueMapDefault"] = default
    return cfg


CASES: list[SimpleCase] = [
    SimpleCase("Test without config and data", _NO_CONFIG, None, {}, True, True),
    SimpleCase("Test with invalid config and without data", [], None, None, False, False),
    SimpleCase("Test with empty data", _NO_CONFIG, {}, {}, True, True),
    SimpleCase("Test with invalid data", _NO_CONFIG, [], None, True, False),
    SimpleCase(
        "Test without data",
        {"KeyMapDefault": _KEEP, "ValueMapDefault": _KEEP},
        None,
        {},
        True,
        True,
    ),
    SimpleCase(
        "Test with leftover data",
        {"KeyMapDefault": _KEEP, "ValueMapDefault": _KEEP},
        {"": "three", "two": "two"},  # Perl literal repeats '' => last one wins
        {"two": "two"},
        True,
        True,
    ),
    SimpleCase("Test with undef config", _NO_CONFIG, dict(_NUMS), dict(_NUMS), True, True),
    SimpleCase("Test empty config", {}, dict(_NUMS), dict(_NUMS), False, False),
    SimpleCase(
        "Test with wrong config",
        {"NoValidValue": {"Invalid": "value"}},
        dict(_NUMS),
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test KeyMapExact",
        {
            "KeyMapExact": {
                "one": "new_value",
                "two": "another_new_value",
                "four": "new_value_gain",
            },
            "KeyMapDefault": {"MapType": "Ignore"},
            "ValueMapDefault": _KEEP,
        },
        {"one": "one", "two": "two", "three": "three", "four": "four", "five": "five"},
        {"new_value": "one", "another_new_value": "two", "new_value_gain": "four"},
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapRegEx",
        {
            "KeyMapRegEx": {"Stat(e|us)": "state", "[pP]riority": "prio"},
            "KeyMapDefault": {"MapType": "Ignore"},
            "ValueMapDefault": _KEEP,
        },
        {
            "State": "A lost state",
            "Stadium": "Allianz Arena",
            "Status": "Open",
            "Priority": "with capital letter",
            "priority": "without capital letter",
        },
        {"state": "A lost state", "prio": "with capital letter"},
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapDefault (Keep)",
        {"KeyMapDefault": {"MapType": "Keep", "MapTo": "new_value"}, "ValueMapDefault": _KEEP},
        dict(_NUMS),
        dict(_NUMS),
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapDefault (Ignore)",
        {"KeyMapDefault": {"MapType": "Ignore", "MapTo": "new_value"}, "ValueMapDefault": _KEEP},
        dict(_NUMS),
        {},
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapDefault (MapTo)",
        {"KeyMapDefault": {"MapType": "MapTo", "MapTo": "new_key"}, "ValueMapDefault": _KEEP},
        dict(_NUMS),
        {"new_key": "one"},
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapExact & KeyMapDefault",
        {
            "KeyMapExact": {
                "one": "new_value",
                "two": "another_new_value",
                "four": "new_value_gain",
            },
            "KeyMapDefault": {"MapType": "MapTo", "MapTo": "new_key"},
            "ValueMapDefault": _KEEP,
        },
        {"one": "one", "two": "two", "three": "three", "four": "four", "five": "five"},
        {
            "new_value": "one",
            "another_new_value": "two",
            "new_key": "five",
            "new_value_gain": "four",
        },
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapRegEx & KeyMapDefault",
        {
            "KeyMapRegEx": {"Stat(e|us)": "state", "[pP]riority": "prio"},
            "KeyMapDefault": {"MapType": "Keep", "MapTo": "new_key"},
            "ValueMapDefault": _KEEP,
        },
        {
            "State": "A lost state",
            "Stadium": "Allianz Arena",
            "Status": "Open",
            "Priority": "with capital letter",
            "priority": "without capital letter",
            "one_more_key": "some value",
            "other_key": "an empty string",
        },
        {
            "state": "A lost state",
            "Stadium": "Allianz Arena",
            "prio": "with capital letter",
            "one_more_key": "some value",
            "other_key": "an empty string",
        },
        True,
        True,
    ),
    SimpleCase(
        "Test KeyMapDefault ValueMapDefault large hash",
        {
            "KeyMapDefault": _KEEP,
            "ValueMapDefault": {"MapType": "MapTo", "MapTo": "new_value"},
        },
        _LARGE_HASH,
        _LARGE_HASH_NEW,
        True,
        True,
        check_time=True,
    ),
    SimpleCase(
        "Test KeyMapDefault & ValueMapDefault large attachments hash",
        {
            "KeyMapDefault": _KEEP,
            "ValueMapDefault": {"MapType": "MapTo", "MapTo": _ATTACHMENTS["txt"]},
        },
        _LARGE_ATT,
        _LARGE_ATT_RESULT,
        True,
        True,
        check_time=True,
    ),
    SimpleCase("Test ValueMap no ValueMapDefault", _vm(), {}, {}, False, False),
    SimpleCase("Test ValueMap empty ValueMapDefault", _vm({}), {}, {}, False, False),
    SimpleCase(
        "Test ValueMap invalid ValueMapDefault",
        _vm({"MapType": "ThisIsNotAValidMapType"}),
        {"Key": "Value"},
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test ValueMap no MapTo for MapType MapTo",
        _vm({"MapType": "MapTo"}),
        {"Key": "Value"},
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test ValueMap invalid MapTo for MapType MapTo",
        _vm({"MapType": "MapTo", "MapTo": {}}),
        {"Key": "Value"},
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test ValueMap invalid ValueMapExact",
        _vm(
            _KEEP,
            {
                "first": {"ValueMapExact": "ThisShouldNotBeAString"},
                "second": _VALUE_MAP["second"],
            },
        ),
        {"Key": "Value"},
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test ValueMap invalid ValueMapRegEx",
        _vm(
            _KEEP,
            {
                "first": _VALUE_MAP["first"],
                "second": {"ValueMapRegEx": {"[rR]eg[eE]x": {}}},
            },
        ),
        {"Key": "Value"},
        None,
        False,
        False,
    ),
    SimpleCase(
        "Test ValueMap all correct",
        _vm(_KEEP),
        {"first": 2, "second": "regex"},
        {"first": "two", "second": "regular expression"},
        True,
        True,
    ),
]


def test_all_znuny_vectors_present() -> None:
    assert len(CASES) == 26
    assert len({c.name for c in CASES}) == 26


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_znuny_simple_vectors(case: SimpleCase) -> None:
    cfg = (
        {"Type": "Simple", "Config": case.config}
        if case.config is not _NO_CONFIG
        else {"Type": "Simple"}
    )
    started = time.monotonic()
    if not case.config_success:
        with pytest.raises(MappingConfigError):
            build_mapper(cfg)
        return
    mapper = build_mapper(cfg)
    if not case.result_success:
        with pytest.raises(MappingError) as excinfo:
            mapper.map(case.data)
        assert str(excinfo.value)
        return
    assert mapper.map(case.data) == case.result
    if case.check_time:
        assert time.monotonic() - started < 5
    # Simple.t builds a second mapper from the same config: construction is repeatable
    assert build_mapper(cfg) is not mapper


def test_key_exact_zero_target_is_no_match() -> None:  # Perl truthiness quirk
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapExact": {"a": "0"},
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        }
    )
    assert m.map({"a": "v"}) == {"a": "v"}


def test_unmapped_number_stays_number_and_nested_passes_through() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        }
    )
    assert m.map({"n": 5, "d": {"x": "y"}, "l": [1]}) == {"n": 5, "d": {"x": "y"}, "l": [1]}


def test_regex_collision_drops_second_key() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapRegEx": {"[ab]": "c"},
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        }
    )
    assert m.map({"a": "1", "b": "2"}) == {"c": "1"}


def test_integer_yaml_keys_in_value_map_match_like_perl_strings() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
                "ValueMap": {"first": {"ValueMapExact": {1: "one", 2: 20}}},
            },
        }
    )
    assert m.map({"first": 1}) == {"first": "one"}
    assert m.map({"first": "2"}) == {"first": 20}


def test_value_exact_empty_string_target_counts_as_hit() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "MapTo", "MapTo": "dflt"},
                "ValueMap": {"k": {"ValueMapExact": {"x": ""}}},
            },
        }
    )
    assert m.map({"k": "x", "j": "y"}) == {"k": "", "j": "dflt"}


def test_value_default_ignore_drops_key() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Ignore"},
            },
        }
    )
    assert m.map({"a": "1", "n": {"x": 1}}) == {"n": {"x": 1}}


def test_bad_regex_fails_at_build_time() -> None:
    base = {"KeyMapDefault": {"MapType": "Keep"}, "ValueMapDefault": {"MapType": "Keep"}}
    with pytest.raises(MappingConfigError, match="Invalid regular expression"):
        build_mapper({"Type": "Simple", "Config": {**base, "KeyMapRegEx": {"(unclosed": "c"}}})
    with pytest.raises(MappingConfigError, match="Invalid regular expression"):
        build_mapper(
            {
                "Type": "Simple",
                "Config": {**base, "ValueMap": {"k": {"ValueMapRegEx": {"[": "c"}}}},
            }
        )


def test_catastrophic_regex_is_mapping_error_not_a_hang() -> None:
    m = build_mapper(
        {
            "Type": "Simple",
            "Config": {
                "KeyMapRegEx": {"(a|aa)+": "c"},
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        }
    )
    started = time.monotonic()
    with pytest.raises(MappingError, match="timed out"):
        m.map({"a" * 40 + "b": "1"})
    assert time.monotonic() - started < 1


def test_mapping_new_errors() -> None:
    for bad in ({}, {"Type": ""}, {"Type": "Simple", "Config": {}}, {"Type": "Nope"}, None, "x"):
        with pytest.raises(MappingConfigError):
            build_mapper(bad)


def test_explicit_none_config_is_passthrough() -> None:
    m = build_mapper({"Type": "Simple", "Config": None})
    assert m.map({"a": 1}) == {"a": 1}


def test_unknown_type_names_the_type() -> None:
    with pytest.raises(MappingConfigError, match="Nope"):
        build_mapper({"Type": "Nope"})


def test_non_dict_data_message() -> None:
    m = build_mapper({"Type": "Simple"})
    with pytest.raises(MappingError, match="not a hash ref"):
        m.map([])
