"""Pure tests for ``tiqora.process.conditions`` (Znuny Transition.pm, TransitionValidation)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.process.conditions import (
    ConditionConfigError,
    check_transition,
    evaluate_transition,
    resolve_transition_tags,
)
from tiqora.process.conditions import (
    evaluate_condition_field as ev,
)
from tiqora.process.config import TransitionConditionField as C
from tiqora.process.config import TransitionConfig


def _transition(raw: dict[str, Any]) -> TransitionConfig:
    return TransitionConfig.from_dict(raw)


def _fields(**fields: Any) -> dict[str, Any]:
    return {"Type": "and", "Fields": fields}


# --- String / Regexp (String.pm:99-170, Regexp.pm:99-175) -------------------------------


def test_string_needs_defined_value_and_nonempty_match() -> None:
    assert ev("Queue", C(match="Raw", type_="String"), {"Queue": "Raw"})
    assert not ev("DynamicField_X", C(match="", type_="String"), {"DynamicField_X": ""})
    assert not ev("DynamicField_X", C(match="a", type_="String"), {"DynamicField_X": None})
    assert ev("DynamicField_M", C(match="b", type_="String"), {"DynamicField_M": ["a", "b"]})


def test_string_zero_match_is_allowed() -> None:
    # String.pm:116-123: "0" is the one falsy Match that still compares.
    assert ev("Prio", C(match="0", type_="String"), {"Prio": "0"})


def test_regexp_needs_truthy_value() -> None:
    assert not ev("Title", C(match=".*", type_="Regexp"), {"Title": ""})
    assert not ev("Title", C(match=".*", type_="Regexp"), {"Title": "0"})
    assert ev("DynamicField_M", C(match="^b$", type_="Regexp"), {"DynamicField_M": ["a", "b"]})


# --- Base.pm: CheckValueGet / Equal / NotEqual ------------------------------------------


def test_base_missing_value_falls_back_to_field_name() -> None:
    # Znuny CheckValueGet: Data->{FieldName} // FieldName (Base.pm:195)
    assert ev(
        "DynamicField_Unset",
        C(match="dynamicfield_unset", type_="Equal"),
        {"DynamicField_Unset": None},
    )


def test_equal_empty_check_value_never_matches() -> None:
    assert not ev("Title", C(match="", type_="Equal"), {"Title": ""})
    assert ev("Title", C(match="x", type_="NotEqual"), {"Title": ""})


def test_equal_list_rules() -> None:
    assert ev("M", C(match="a, b", type_="Equal"), {"M": ["a", "b"]})
    assert ev("M", C(match="A", type_="Equal"), {"M": ["a"]})
    assert not ev("M", C(match="a", type_="Equal"), {"M": ["a", "b"]})


def test_equal_string_against_match_list_is_split_on_commas() -> None:
    # Base.pm:Equal, IsStringWithData(Check) && IsArrayRefWithData(Match)
    assert ev("T", C(match=["a", "b"], type_="Equal"), {"T": "a, b"})
    assert ev("T", C(match=["a"], type_="Equal"), {"T": "A"})
    assert not ev("T", C(match=["a", "b"], type_="Equal"), {"T": "a"})


def test_not_equal_follows_base_pm_case_rules() -> None:
    # Base.pm NotEqual: strings are compared lower-cased on both sides ...
    assert not ev("Q", C(match="RAW", type_="NotEqual"), {"Q": "raw"})
    # ... but for a list the Match is NOT lower-cased (grep { $Match eq lc($_) }).
    assert ev("M", C(match="A", type_="NotEqual"), {"M": ["a", "b"]})
    assert not ev("M", C(match="a", type_="NotEqual"), {"M": ["a", "b"]})


def test_contains_and_not_contains() -> None:
    assert ev("Title", C(match="PRINT", type_="Contains"), {"Title": "my printer"})
    assert ev("M", C(match="b", type_="Contains"), {"M": ["abc"]})
    assert not ev("Title", C(match="scan", type_="Contains"), {"Title": "my printer"})
    assert ev("Title", C(match="scan", type_="NotContains"), {"Title": "my printer"})
    # NotContains on a list compares whole elements (Base.pm:NotContains)
    assert ev("M", C(match="b", type_="NotContains"), {"M": ["abc"]})
    assert not ev("M", C(match="abc", type_="NotContains"), {"M": ["abc"]})


def test_tags_in_field_name_and_match_for_base_types_only() -> None:
    tags = {"<OTRS_TICKET_DynamicField_Target>": "Postmaster", "<OTRS_TICKET_Queue>": "raw"}
    assert ev(
        "<OTRS_TICKET_DynamicField_Target>",
        C(match="postmaster", type_="Equal"),
        {},
        tags=tags,
    )
    assert ev("Queue", C(match="<OTRS_TICKET_Queue>", type_="Equal"), {"Queue": "Raw"}, tags=tags)
    assert not ev(
        "Queue", C(match="<OTRS_TICKET_Queue>", type_="String"), {"Queue": "Raw"}, tags=tags
    )


def test_tag_found_in_data_value_is_substituted_too() -> None:
    # Base.pm:CheckValueGet second round: the data value itself may carry a tag.
    tags = {"<OTRS_TICKET_Queue>": "Raw"}
    data = {"DynamicField_Copy": "<OTRS_TICKET_Queue>"}
    assert ev("DynamicField_Copy", C(match="raw", type_="Equal"), data, tags=tags)


def test_ordered_compare_with_dates() -> None:
    assert ev(
        "DynamicField_D",
        C(match="2026-01-01", type_="GreaterThan"),
        {"DynamicField_D": "2026-02-01 00:00:00"},
    )
    assert not ev("N", C(match="x", type_="GreaterThan"), {"N": "5"})
    assert ev("N", C(match="5", type_="GreaterThanOrEqual"), {"N": "5"})
    assert ev("N", C(match="5", type_="LessThan"), {"N": "4"})


@pytest.mark.asyncio
async def test_resolve_transition_tags_renders_each_base_type_text_once() -> None:
    config = _transition(
        {
            "Condition": {
                "1": _fields(
                    **{
                        "<OTRS_TICKET_Queue>": {"Type": "Equal", "Match": "<OTRS_TICKET_State>"},
                        "Title": {"Type": "String", "Match": "<OTRS_TICKET_Title>"},
                        "DynamicField_Copy": {"Type": "Equal", "Match": "x"},
                    }
                )
            }
        }
    )
    rendered: list[str] = []

    async def render(text: str) -> str:
        rendered.append(text)
        return text.strip("<>").lower()

    tags = await resolve_transition_tags(
        config, {"DynamicField_Copy": "<OTRS_TICKET_Priority>"}, render=render
    )
    # String compares raw, so its Match is never rendered; the data value found under the
    # (Equal) field name is (Base.pm:CheckValueGet second round).
    assert tags == {
        "<OTRS_TICKET_Queue>": "otrs_ticket_queue",
        "<OTRS_TICKET_State>": "otrs_ticket_state",
        "<OTRS_TICKET_Priority>": "otrs_ticket_priority",
    }
    assert sorted(rendered) == [
        "<OTRS_TICKET_Priority>",
        "<OTRS_TICKET_Queue>",
        "<OTRS_TICKET_State>",
    ]


# --- Transition.pm:329-425 config shorthands and linking (F15) --------------------------


def test_condition_linking_falls_back_to_condition_type() -> None:
    # Transition.pm:329-333: ConditionLinking || Condition->{Type} || 'and'
    cfg = _transition(
        {"Condition": {"Type": "or", "1": _fields(Queue="Raw"), "2": _fields(Queue="Misc")}}
    )
    assert cfg.condition_linking == "or"
    assert evaluate_transition(cfg, {"Queue": "Raw"})
    explicit = _transition(
        {
            "ConditionLinking": "and",
            "Condition": {"Type": "or", "1": _fields(Queue="Raw"), "2": _fields(Queue="Misc")},
        }
    )
    assert not evaluate_transition(explicit, {"Queue": "Raw"})


def test_plain_string_field_entry_becomes_string_type() -> None:
    # Transition.pm:418-428 (ref eq '' -> {Type => 'String', Match => value})
    cfg = _transition({"Condition": {"1": _fields(Queue="Raw")}})
    field = cfg.conditions[0].fields["Queue"]
    assert (field.type_, field.match) == ("String", "Raw")
    assert evaluate_transition(cfg, {"Queue": "Raw"})
    assert not evaluate_transition(cfg, {"Queue": "raw"})  # String is case-sensitive


def test_list_field_entry_becomes_array_type_all_elements_must_occur() -> None:
    # Transition.pm:430-440 (list -> Array) and 543-607 (every Match element in the value)
    cfg = _transition({"Condition": {"1": _fields(DynamicField_M=["a", "b"])}})
    field = cfg.conditions[0].fields["DynamicField_M"]
    assert (field.type_, field.match) == ("Array", ["a", "b"])
    assert evaluate_transition(cfg, {"DynamicField_M": ["b", "c", "a"]})
    assert not evaluate_transition(cfg, {"DynamicField_M": ["a", "c"]})
    assert not evaluate_transition(cfg, {"DynamicField_M": ["A", "B"]})  # case-sensitive
    assert not evaluate_transition(cfg, {"DynamicField_M": None})


def test_hash_field_all_keys_must_equal() -> None:
    # Transition.pm:608-690: Hash compares every Match key; a non-hash value is a quiet fail.
    cfg = _transition(
        {"Condition": {"1": _fields(Meta={"Type": "Hash", "Match": {"k": "v", "n": "1"}})}}
    )
    assert evaluate_transition(cfg, {"Meta": {"k": "v", "n": "1", "x": "y"}})
    assert not evaluate_transition(cfg, {"Meta": {"k": "v", "n": "2"}})
    assert not evaluate_transition(cfg, {"Meta": "v"})
    bad = _transition({"Condition": {"1": _fields(Meta={"Type": "Hash", "Match": "nope"})}})
    assert not evaluate_transition(bad, {"Meta": {"k": "v"}})  # "Match must be a hash!"


def test_block_type_xor_needs_exactly_one_field() -> None:
    # Transition.pm:925-947 (CondType xor: FieldSuccess == 1)
    def cfg(**fields: Any) -> TransitionConfig:
        return _transition({"Condition": {"1": {"Type": "xor", "Fields": fields}}})

    assert cfg(Queue="Raw", State="open").conditions[0].type_ == "xor"
    assert evaluate_transition(cfg(Queue="Raw", State="closed"), {"Queue": "Raw", "State": "open"})
    assert not evaluate_transition(
        cfg(Queue="Raw", State="open"), {"Queue": "Raw", "State": "open"}
    )
    assert not evaluate_transition(cfg(Queue="x", State="y"), {"Queue": "Raw", "State": "open"})


def test_condition_linking_xor_needs_exactly_one_block() -> None:
    # Transition.pm:981-995 (ConditionLinking xor: ConditionSuccess == 1)
    def cfg(second: str) -> TransitionConfig:
        return _transition(
            {
                "ConditionLinking": "xor",
                "Condition": {"1": _fields(Queue="Raw"), "2": _fields(State=second)},
            }
        )

    data = {"Queue": "Raw", "State": "open"}
    assert evaluate_transition(cfg("closed"), data)
    assert not evaluate_transition(cfg("open"), data)


def test_unknown_linking_and_missing_conditions_never_match() -> None:
    # Transition.pm:322-327 (no Condition), 345-355 (invalid linking), 359-366 (no Fields)
    assert not evaluate_transition(_transition({}), {"Queue": "Raw"})
    assert not evaluate_transition(
        _transition({"ConditionLinking": "nand", "Condition": {"1": _fields(Queue="Raw")}}),
        {"Queue": "Raw"},
    )
    assert not evaluate_transition(
        _transition({"Condition": {"1": {"Type": "and", "Fields": {}}}}), {"Queue": "Raw"}
    )


def test_and_linking_with_or_block_and_or_linking_with_and_block() -> None:
    # Transition.pm:515 / 893-921: block results combine per ConditionLinking
    data = {"Queue": "Raw", "State": "open"}
    or_block = {"Type": "or", "Fields": {"Queue": "Raw", "State": "closed"}}
    and_block = {"Type": "and", "Fields": {"Queue": "Raw", "State": "closed"}}
    assert evaluate_transition(
        _transition({"ConditionLinking": "and", "Condition": {"1": or_block}}), data
    )
    assert not evaluate_transition(
        _transition({"ConditionLinking": "and", "Condition": {"1": and_block}}), data
    )
    assert evaluate_transition(
        _transition(
            {
                "ConditionLinking": "or",
                "Condition": {"1": and_block, "2": {"Fields": {"Queue": "Raw"}}},
            }
        ),
        data,
    )


def test_config_errors_raise_from_check_transition() -> None:
    # Transition.pm:322-327 etc.: Znuny's bare ``return`` leaves TransitionCheck altogether, so
    # check_transition raises (run_transitions then fires nothing); evaluate_transition says False.
    for raw in (
        {},
        {"ConditionLinking": "nand", "Condition": {"1": _fields(Queue="Raw")}},
        {"Condition": {"1": {"Type": "and", "Fields": {}}}},
        {"Condition": {"1": {"Type": "nand", "Fields": {"Queue": "Raw"}}}},
        {"Condition": {"1": _fields(Queue={"Type": "Bogus", "Match": "Raw"})}},
    ):
        cfg = _transition(raw)
        with pytest.raises(ConditionConfigError):
            check_transition(cfg, {"Queue": "Raw"})
        assert evaluate_transition(cfg, {"Queue": "Raw"}) is False
