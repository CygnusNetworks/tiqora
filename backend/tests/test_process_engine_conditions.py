"""Pure unit tests for ``evaluate_transition`` (no DB) — subtask 2.

Builds ``TransitionConfig``/``TransitionConditionBlock``/
``TransitionConditionField`` objects directly (as subtask 1's
``test_process_config.py`` does via YAML parsing) and feeds them straight to
``evaluate_transition`` with a hand-built ``ticket_attrs`` dict.
"""

from __future__ import annotations

from tiqora.process.config import (
    TransitionConditionBlock,
    TransitionConditionField,
    TransitionConfig,
)
from tiqora.process.engine import evaluate_transition

ATTRS = {
    "Queue": "Raw",
    "State": "open",
    "Priority": "3 normal",
    "Title": "Please help me with my printer",
    "DynamicField_Make": "Toyota",
}


def _config(
    *fields: dict[str, tuple[object, str]],
    block_type: str = "and",
    condition_linking: str = "and",
) -> TransitionConfig:
    """Build a TransitionConfig with one condition block per *fields* dict."""
    blocks = [
        TransitionConditionBlock(
            type_=block_type,
            fields={
                name: TransitionConditionField(match=match, type_=type_)
                for name, (match, type_) in field_map.items()
            },
        )
        for field_map in fields
    ]
    return TransitionConfig(condition_linking=condition_linking, conditions=blocks)


def test_no_condition_key_matches_unconditionally() -> None:
    config = TransitionConfig(condition_linking="and", conditions=[])
    assert evaluate_transition(config, ATTRS) is True


def test_empty_fields_in_block_is_vacuously_true() -> None:
    config = _config({})
    assert evaluate_transition(config, ATTRS) is True


# --- String -----------------------------------------------------------------


def test_string_exact_match() -> None:
    config = _config({"Queue": ("Raw", "String")})
    assert evaluate_transition(config, ATTRS) is True


def test_string_is_case_sensitive() -> None:
    # Verified against znuny-6.5.22 TransitionValidation/String.pm: plain
    # Perl `eq`, no lc() — case-SENSITIVE, unlike Equal/NotEqual.
    config = _config({"Queue": ("raw", "String")})
    assert evaluate_transition(config, ATTRS) is False


def test_string_mismatch() -> None:
    config = _config({"Queue": ("Misc", "String")})
    assert evaluate_transition(config, ATTRS) is False


def test_string_non_string_match_never_matches() -> None:
    config = _config({"Queue": (123, "String")})
    assert evaluate_transition(config, ATTRS) is False


# --- Regexp -------------------------------------------------------------


def test_regexp_matches() -> None:
    config = _config({"Title": (r"printer", "Regexp")})
    assert evaluate_transition(config, ATTRS) is True


def test_regexp_no_match() -> None:
    config = _config({"Title": (r"^printer", "Regexp")})
    assert evaluate_transition(config, ATTRS) is False


def test_regexp_invalid_pattern_is_non_matching_not_raising() -> None:
    config = _config({"Title": (r"[unclosed", "Regexp")})
    assert evaluate_transition(config, ATTRS) is False


# --- Contains / NotContains ----------------------------------------------


def test_contains_is_case_insensitive_substring() -> None:
    config = _config({"Title": ("PRINTER", "Contains")})
    assert evaluate_transition(config, ATTRS) is True


def test_contains_no_match() -> None:
    config = _config({"Title": ("scanner", "Contains")})
    assert evaluate_transition(config, ATTRS) is False


def test_not_contains_true_when_absent() -> None:
    config = _config({"Title": ("scanner", "NotContains")})
    assert evaluate_transition(config, ATTRS) is True


def test_not_contains_false_when_present() -> None:
    config = _config({"Title": ("printer", "NotContains")})
    assert evaluate_transition(config, ATTRS) is False


# --- Equal / NotEqual ------------------------------------------------------


def test_equal_is_case_insensitive() -> None:
    config = _config({"State": ("OPEN", "Equal")})
    assert evaluate_transition(config, ATTRS) is True


def test_equal_mismatch() -> None:
    config = _config({"State": ("closed", "Equal")})
    assert evaluate_transition(config, ATTRS) is False


def test_not_equal_true_when_different() -> None:
    config = _config({"State": ("closed", "NotEqual")})
    assert evaluate_transition(config, ATTRS) is True


def test_not_equal_false_when_same_case_insensitive() -> None:
    config = _config({"State": ("OPEN", "NotEqual")})
    assert evaluate_transition(config, ATTRS) is False


# --- Missing field defaults to "" -------------------------------------------


def test_missing_field_treated_as_empty_string() -> None:
    config = _config({"DynamicField_Missing": ("", "String")})
    assert evaluate_transition(config, ATTRS) is True


def test_missing_field_nonempty_match_fails() -> None:
    config = _config({"DynamicField_Missing": ("anything", "String")})
    assert evaluate_transition(config, ATTRS) is False


# --- Unsupported types: GreaterThan/LessThan/Module -------------------------


def test_greater_than_is_unsupported_and_non_matching() -> None:
    config = _config({"Priority": ("2", "GreaterThan")})
    assert evaluate_transition(config, ATTRS) is False


def test_module_based_condition_is_unsupported_and_non_matching() -> None:
    config = _config({"Queue": ("Raw", "Module")})
    assert evaluate_transition(config, ATTRS) is False


# --- Field combination within a block (and/or) ------------------------------


def test_block_and_requires_all_fields() -> None:
    config = _config(
        {"Queue": ("Raw", "String"), "State": ("closed", "String")},
        block_type="and",
    )
    assert evaluate_transition(config, ATTRS) is False


def test_block_or_requires_any_field() -> None:
    config = _config(
        {"Queue": ("Raw", "String"), "State": ("closed", "String")},
        block_type="or",
    )
    assert evaluate_transition(config, ATTRS) is True


# --- Block combination (condition_linking and/or) ---------------------------


def test_condition_linking_and_requires_all_blocks() -> None:
    config = _config(
        {"Queue": ("Raw", "String")},
        {"State": ("closed", "String")},
        condition_linking="and",
    )
    assert evaluate_transition(config, ATTRS) is False


def test_condition_linking_or_requires_any_block() -> None:
    config = _config(
        {"Queue": ("Raw", "String")},
        {"State": ("closed", "String")},
        condition_linking="or",
    )
    assert evaluate_transition(config, ATTRS) is True
