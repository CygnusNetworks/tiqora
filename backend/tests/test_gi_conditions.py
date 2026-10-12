"""Event conditions (G4): vectors ported from Znuny's SerializeConfig.t and ConditionCheck.t."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.gi.conditions import ConditionWarning, condition_check, serialize_config

SERIALIZE_CASES: list[tuple[str, Any, dict[str, Any]]] = [
    ("Simple Values", {"Key1": 1, "Key2": 2, "Key3": 3}, {"Key1": 1, "Key2": 2, "Key3": 3}),
    (
        "Simple Array",
        {"Key1": [1, 2, 3]},
        {"Key1_0": 1, "Key1_1": 2, "Key1_2": 3},
    ),
    (
        "Simple Hash",
        {"Key1": {"KeyA": 1, "KeyB": 2, "KeyC": 3}},
        {"Key1_KeyA": 1, "Key1_KeyB": 2, "Key1_KeyC": 3},
    ),
    (
        "Hash of Arrays",
        {"Key1": {"KeyA": [1, 2, 3], "KeyB": [1, 2, 3], "KeyC": [1, 2, 3]}},
        {f"Key1_Key{k}_{i}": i + 1 for k in "ABC" for i in range(3)},
    ),
    # "Array of Hashes" is commented out in SerializeConfig.t ("not supported"): see
    # test_serialize_skips_containers_inside_arrays below.
]


@pytest.mark.parametrize(("name", "data", "expected"), SERIALIZE_CASES, ids=lambda v: str(v)[:30])
def test_serialize_config_vectors(name: str, data: Any, expected: dict[str, Any]) -> None:
    assert serialize_config(data) == expected, name


def test_serialize_skips_falsy_and_does_not_advance_index() -> None:
    assert serialize_config({"A": ["x", "0", "", "y"]}) == {"A_0": "x", "A_1": "y"}


def test_serialize_skips_containers_inside_arrays() -> None:
    # Perl would emit unmatchable HASH(0x...) keys; the counter still advances
    assert serialize_config({"A": [{"k": "v"}, "s"]}) == {"A_1": "s"}


def test_serialize_empty_container_kept_as_value() -> None:
    assert serialize_config({"A": {}, "B": []}) == {"A": {}, "B": []}


def test_serialize_skips_falsy_hash_keys_but_keeps_falsy_values() -> None:
    assert serialize_config({"": 1, "0": 2, "K": "0", "N": None}) == {"K": "0", "N": None}


# ConditionCheck.t: (name, condition, [(data, expected)]).  Perl `qr/.../` Match values are
# given as the equivalent pattern string.  Module cases: Tiqora never matches.
RAW = ["Raw"]
VW_FIELDS = {"Queue": RAW, "DynamicField_Make": ["2"], "DynamicField_VWModel": ["2"]}
OK_DATA = {"Queue": ["Raw"], "DynamicField_Make": ["2"], "DynamicField_VWModel": ["2"]}


def _hash(match: dict[str, Any]) -> dict[str, Any]:
    return {"Type": "Hash", "Match": match}


def _cond(fields: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"Condition": {"Cond1": {"Fields": fields, **extra}}}


MAKE_HASH_ALL = {"1": 2, "2": 3, "3": 4}

CASES: list[tuple[str, Any, list[tuple[Any, bool]]]] = [
    ("Empty Data", _cond(VW_FIELDS), [("", False)]),
    ("Missing Condition", {"Condition": {}}, [(OK_DATA, False)]),
    (
        "Unknown ConditionLinking",
        {"ConditionLinking": "unknown123", **_cond(VW_FIELDS)},
        [(OK_DATA, False)],
    ),
    ("Missing Condition Fields", _cond({}), [(OK_DATA, False)]),
    ("Unknown Condition Type", _cond(VW_FIELDS, Type="unknown"), [(OK_DATA, False)]),
    (
        "String Type Field with Array Match",
        _cond(
            {
                "Queue": {"Type": "String", "Match": ["Raw"]},
                "DynamicField_Make": ["2"],
                "DynamicField_VWModel": ["2"],
            }
        ),
        [({"Queue": "Raw", "DynamicField_Make": ["2"], "DynamicField_VWModel": ["2"]}, False)],
    ),
    (
        "String Type Field with Array Value",
        _cond({"DynamicField_Make": {"Type": "String", "Match": "Test2"}}, Type="and"),
        [
            ({"DynamicField_Make": ["Test1", "Test2", "Test3"]}, True),
            ({"DynamicField_Make": ["Test1", "Test3", "Test4"]}, False),
        ],
    ),
    (
        "String Type Field with Array Value Flatten",
        _cond({"DynamicField_Make": {"Type": "String", "Match": "Test2"}}, Type="and"),
        [
            (
                {
                    "DynamicField_Make_1": "Test1",
                    "DynamicField_Make_2": "Test2",
                    "DynamicField_Make_3": "Test3",
                },
                True,
            ),
            (
                {
                    "DynamicField_Make_1": "Test1",
                    "DynamicField_Make_2": "Test3",
                    "DynamicField_Make_3": "Test4",
                },
                False,
            ),
        ],
    ),
    (
        "Regexp Type Field with Array Value",  # Match qr/t2$/
        _cond({"DynamicField_Make": {"Type": "Regexp", "Match": "t2$"}}, Type="and"),
        [
            ({"DynamicField_Make": ["Test1", "Test2", "Test3"]}, True),
            ({"DynamicField_Make": ["Test1", "Test3", "Test4"]}, False),
        ],
    ),
    (
        "Regexp Type Field with Array Value Flatten",  # Match qr/t2$/
        _cond({"DynamicField_Make": {"Type": "Regexp", "Match": "t2$"}}, Type="and"),
        [
            (
                {
                    "DynamicField_Make_1": "Test1",
                    "DynamicField_Make_2": "Test2",
                    "DynamicField_Make_3": "Test3",
                },
                True,
            ),
            (
                {
                    "DynamicField_Make_1": "Test1",
                    "DynamicField_Make_2": "Test3",
                    "DynamicField_Make_3": "Test4",
                },
                False,
            ),
        ],
    ),
    (
        "Condition Type 'and'",
        _cond(
            {
                "Queue": ["Raw"],
                "DynamicField_Make": _hash({"1": 7, "2": 8, "3": 11}),
                "DynamicField_VWModel": ["2"],
            },
            Type="and",
        ),
        [
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 7, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["2"],
                },
                True,
            ),
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 8, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        "Condition Type 'and' using Regexp",
        _cond(
            {
                "Queue": {"Type": "String", "Match": "Raw"},
                "DynamicField_VWModel": {"Type": "Regexp", "Match": r"\d+"},
            },
            Type="and",
        ),
        [
            ({"Queue": "Raw", "DynamicField_VWModel": "2"}, True),
            ({"Queue": "Raw", "DynamicField_VWModel": "a"}, False),
        ],
    ),
    (
        "Condition Type 'or'",
        _cond(
            {
                "Queue": ["Raw"],
                "DynamicField_Make": _hash(MAKE_HASH_ALL),
                "DynamicField_VWModel": ["2"],
            },
            Type="or",
        ),
        [
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 7, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["2"],
                },
                True,
            ),
            (
                {
                    "Queue": ["Raw2"],
                    "DynamicField_Make": {"1": 7, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["3"],
                },
                False,
            ),
        ],
    ),
    (
        "Condition Type 'xor' Success",
        _cond(
            {
                "Queue": ["Raw"],
                "DynamicField_Make": _hash(MAKE_HASH_ALL),
                "DynamicField_VWModel": ["2"],
            },
            Type="xor",
        ),
        [
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 7, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["1"],
                },
                True,
            ),
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 7, "2": 8, "3": 11},
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        # ConditionLinking inside Condition is skipped; the top-level one defaults to and
        "ConditionLinking 'and'",
        {
            "Condition": {
                "ConditionLinking": "and",
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {"Fields": {"DynamicField_Make": _hash(MAKE_HASH_ALL)}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 2, "2": 3, "3": 4},
                    "DynamicField_VWModel": ["2"],
                },
                True,
            ),
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 2, "2": 19, "3": 4},
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        "ConditionLinking 'or'",
        {
            "ConditionLinking": "or",
            "Condition": {
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {"Fields": {"DynamicField_Make": ["VW"]}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["3"]}},
            },
        },
        [
            (
                {
                    "Queue": ["PostMaster"],
                    "DynamicField_Make": ["VW"],
                    "DynamicField_VWModel": ["3"],
                },
                True,
            ),
            (
                {
                    "Queue": ["PostMaster"],
                    "DynamicField_Make": ["Seat"],
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        "ConditionLinking 'xor'",
        {
            "ConditionLinking": "xor",
            "Condition": {
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {"Fields": {"DynamicField_Make": ["VW"]}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            },
        },
        [
            (
                {
                    "Queue": ["PostMaster"],
                    "DynamicField_Make": ["VW"],
                    "DynamicField_VWModel": ["3"],
                },
                True,
            ),
            (
                {
                    "Queue": ["PostMaster"],
                    "DynamicField_Make": ["VW"],
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        "Hash Field",
        _cond(
            {
                "Queue": ["Raw"],
                "DynamicField_Make": _hash(MAKE_HASH_ALL),
                "DynamicField_VWModel": ["2"],
            }
        ),
        [
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 2, "2": 3, "3": 4},
                    "DynamicField_VWModel": ["2"],
                },
                True,
            ),
            (
                {
                    "Queue": ["Raw"],
                    "DynamicField_Make": {"1": 1, "2": 3, "3": 4},
                    "DynamicField_VWModel": ["2"],
                },
                False,
            ),
        ],
    ),
    (
        "Array Field",
        _cond(VW_FIELDS),
        [
            (OK_DATA, True),
            ({"Queue": ["Raw"], "DynamicField_Make": ["1"], "DynamicField_VWModel": ["2"]}, False),
        ],
    ),
    (
        "String Field",
        _cond(
            {
                "Queue": {"Type": "String", "Match": "Raw"},
                "DynamicField_Make": ["2"],
                "DynamicField_VWModel": ["2"],
            }
        ),
        [
            ({"Queue": "Raw", "DynamicField_Make": ["2"], "DynamicField_VWModel": ["2"]}, True),
            (
                {"Queue": "Postmaster", "DynamicField_Make": ["2"], "DynamicField_VWModel": ["2"]},
                False,
            ),
        ],
    ),
    (
        "Regexp Field",  # Match qr/^VW$/
        {
            "Condition": {
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {"Fields": {"DynamicField_Make": {"Type": "Regexp", "Match": "^VW$"}}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [
            ({"Queue": ["Raw"], "DynamicField_Make": "VW", "DynamicField_VWModel": ["2"]}, True),
            ({"Queue": ["Raw"], "DynamicField_Make": "oVW", "DynamicField_VWModel": ["2"]}, False),
        ],
    ),
    (
        "PlainTextRegexp Field",
        {
            "Condition": {
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {"Fields": {"DynamicField_Make": {"Type": "Regexp", "Match": "^VW$"}}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [
            ({"Queue": ["Raw"], "DynamicField_Make": "VW", "DynamicField_VWModel": ["2"]}, True),
            ({"Queue": ["Raw"], "DynamicField_Make": "oVW", "DynamicField_VWModel": ["2"]}, False),
        ],
    ),
    (
        "PlainText Invalid Regexp Field",
        {
            "Condition": {
                "Cond1": {"Fields": {"Queue": ["Raw"]}},
                "Cond2": {
                    "Fields": {
                        "DynamicField_Make": {"Type": "Regexp", "Match": r"^(#)?([\w-\*]+))"}
                    }
                },
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [({"Queue": ["Raw"], "DynamicField_Make": "oVoWo", "DynamicField_VWModel": ["2"]}, False)],
    ),
    (
        # Znuny expects (True, False) via ValidateDemo; Tiqora: Module never matches.
        "Module Field",
        {
            "Condition": {
                "Cond1": {
                    "Fields": {
                        "Queue": {
                            "Type": "Module",
                            "Match": "Kernel::GenericInterface::Event::Validation::ValidateDemo",
                        }
                    }
                },
                "Cond2": {"Fields": {"DynamicField_Make": {"Type": "Regexp", "Match": "^VW$"}}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [
            (
                {"Queue": "Postmaster", "DynamicField_Make": "VW", "DynamicField_VWModel": ["2"]},
                False,
            ),
            ({"Queue": "Raw", "DynamicField_Make": "VW", "DynamicField_VWModel": ["2"]}, False),
        ],
    ),
    (
        "Module Invalid Field Fail",
        {
            "Condition": {
                "Cond1": {
                    "Fields": {
                        "Queue": {
                            "Type": "Module",
                            "Match": "Kernel::GenericInterface::Event::Validation::UnkownModule",
                        }
                    }
                },
                "Cond2": {"Fields": {"DynamicField_Make": {"Type": "Regexp", "Match": "^VW$"}}},
                "Cond3": {"Fields": {"DynamicField_VWModel": ["2"]}},
            }
        },
        [({"Queue": "Raw", "DynamicField_Make": "VW", "DynamicField_VWModel": ["2"]}, False)],
    ),
]

FLAT_CASES = [(name, cond, data, ok) for name, cond, sets in CASES for data, ok in sets]


@pytest.mark.parametrize(
    ("name", "cond", "data", "expected"),
    FLAT_CASES,
    ids=[f"{c[0]}-{'ok' if c[3] else 'fail'}-{i}" for i, c in enumerate(FLAT_CASES)],
)
def test_condition_check_vectors(name: str, cond: Any, data: Any, expected: bool) -> None:
    assert condition_check(cond, data) is expected, name


def test_string_matches_serialized_array_keys() -> None:
    cond = {"ConditionLinking": "and", "Condition": {"1": {"Type": "and", "Fields": {"Tag": "b"}}}}
    assert condition_check(cond, serialize_config({"Tag": ["a", "b"]}))
    assert not condition_check(cond, serialize_config({"Tag": ["a", "c"]}))


def test_string_zero_match_and_zero_data() -> None:
    cond = {"Condition": {"1": {"Fields": {"N": "0"}}}}
    assert condition_check(cond, {"N": "0"})
    assert not condition_check(cond, {"N": "1"})


def test_hash_type_non_dict_is_fail_without_early_return() -> None:
    # Znuny quirk (`next FIELDLNAME`, no early return): with linking and + type and, a failing
    # Hash field on non-dict data does not return at once, so the remaining fields still run -
    # but the condition fails at the end all the same.
    warnings: list[ConditionWarning] = []
    cond = {
        "Condition": {
            "1": {
                "Type": "and",
                "Fields": {"A": _hash({"k": "v"}), "B": {"Type": "Module", "Match": "x"}},
            }
        }
    }
    assert not condition_check(cond, {"A": "scalar", "B": "x"}, warnings=warnings)
    # the Module field after A was still evaluated, proving no early return at A
    assert [w.message for w in warnings] == [
        "Bedingung vom Typ Module wird in Tiqora nicht ausgewertet"
    ]
    # and with type 'or' the later fields can still rescue the condition
    cond_or = {"Condition": {"1": {"Type": "or", "Fields": {"A": _hash({"k": "v"}), "B": "x"}}}}
    assert condition_check(cond_or, {"A": "scalar", "B": "x"})


def test_hash_type_match_must_be_dict() -> None:
    warnings: list[ConditionWarning] = []
    cond = _cond({"A": {"Type": "Hash", "Match": "nope"}})
    assert not condition_check(cond, {"A": {"k": "v"}}, warnings=warnings)
    assert warnings


def test_or_linking_short_circuits() -> None:
    warnings: list[ConditionWarning] = []
    cond = {
        "ConditionLinking": "or",
        "Condition": {
            "1": {"Fields": {"A": "x"}},
            "2": {"Fields": {"B": {"Type": "Bogus"}}},  # would be a config error if reached
        },
    }
    assert condition_check(cond, {"A": "x"}, warnings=warnings)
    assert warnings == []
    # without the first hit the broken second condition is reached and rejects
    assert not condition_check(cond, {"A": "y"}, warnings=warnings)
    assert warnings


def test_xor_condition_needs_exactly_one() -> None:
    cond = _cond({"A": "1", "B": "1", "C": "1"}, Type="xor")
    assert condition_check(cond, {"A": "1", "B": "0", "C": "0"})
    assert not condition_check(cond, {"A": "1", "B": "1", "C": "0"})
    assert not condition_check(cond, {"A": "0", "B": "0", "C": "0"})


def test_regexp_is_not_verbose() -> None:
    # Perl qr{$Match} has no /x: a space is significant
    cond = {"Condition": {"1": {"Fields": {"Title": {"Type": "Regexp", "Match": "a b"}}}}}
    assert condition_check(cond, {"Title": "xa by"}) and not condition_check(
        cond, {"Title": "xaby"}
    )


def test_regexp_catastrophic_pattern_times_out_as_non_match() -> None:
    warnings: list[ConditionWarning] = []
    cond = {"Condition": {"1": {"Fields": {"Title": {"Type": "Regexp", "Match": r"^(a|aa)+$"}}}}}
    assert not condition_check(cond, {"Title": "a" * 60 + "c"}, warnings=warnings)
    assert any("überschritt" in w.message for w in warnings)


def test_regexp_falsy_match_is_config_error() -> None:
    warnings: list[ConditionWarning] = []
    cond = {"Condition": {"1": {"Fields": {"T": {"Type": "Regexp", "Match": "0"}}}}}
    assert not condition_check(cond, {"T": "0"}, warnings=warnings)
    assert warnings


def test_module_and_jq_never_match_with_warning() -> None:
    warnings: list[ConditionWarning] = []
    module = _cond({"Queue": {"Type": "Module", "Match": "Some::Module"}})
    assert not condition_check(
        module, {"Queue": "Raw"}, invoker="Inv", event="TicketCreate", warnings=warnings
    )
    assert warnings == [
        ConditionWarning(
            "Inv", "TicketCreate", "Bedingung vom Typ Module wird in Tiqora nicht ausgewertet"
        )
    ]
    warnings.clear()
    jq = _cond({"jq#.Ticket.Queue": "Raw"})
    assert not condition_check(jq, {"Ticket_Queue": "Raw"}, warnings=warnings)
    assert any("jq" in w.message for w in warnings)


def test_invalid_linking_or_type_is_false() -> None:
    warnings: list[ConditionWarning] = []
    assert not condition_check(
        {"ConditionLinking": "nand", **_cond({"A": "x"})}, {"A": "x"}, warnings=warnings
    )
    assert not condition_check(
        _cond({"A": {"Type": "Bogus", "Match": "x"}}), {"A": "x"}, warnings=warnings
    )
    assert not condition_check(_cond({"A": "x"}, Type="nand"), {"A": "x"}, warnings=warnings)
    assert len(warnings) == 3


def test_string_type_requires_nonempty_string_match() -> None:
    warnings: list[ConditionWarning] = []
    assert not condition_check(_cond({"A": ""}), {"A": ""}, warnings=warnings)
    assert warnings
