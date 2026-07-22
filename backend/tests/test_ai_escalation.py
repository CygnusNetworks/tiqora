"""Unit tests for tiqora.ai.escalation (plan §3.1). No DB, no network."""

from __future__ import annotations

import pytest

from tiqora.ai.escalation import (
    EscalationRuleError,
    check_escalation,
    validate_escalation_rules,
)


def test_exact_match_on_field() -> None:
    rules = [
        {"tool": "netadmin:diagnose", "field": "lock_code", "match": "exact", "values": ["COPYR"]}
    ]
    hit = check_escalation(
        rules, tool_full_name="netadmin:diagnose", raw_result={"lock_code": "COPYR"}
    )
    assert hit is not None
    assert hit.rule_index == 0

    miss = check_escalation(
        rules, tool_full_name="netadmin:diagnose", raw_result={"lock_code": "OTHER"}
    )
    assert miss is None


def test_substring_match_whole_result_when_no_field() -> None:
    rules = [{"tool": "*", "match": "substring", "values": ["urgent"]}]
    hit = check_escalation(rules, tool_full_name="anything:tool", raw_result="this is urgent!")
    assert hit is not None


def test_regex_match() -> None:
    rules = [{"tool": "*", "field": "code", "match": "regex", "values": [r"^ERR-\d+$"]}]
    hit = check_escalation(rules, tool_full_name="x:y", raw_result={"code": "ERR-42"})
    assert hit is not None
    miss = check_escalation(rules, tool_full_name="x:y", raw_result={"code": "OK"})
    assert miss is None


def test_client_wildcard_matches_any_tool_of_client() -> None:
    rules = [{"tool": "netadmin:*", "match": "exact", "values": ["X"]}]
    assert check_escalation(rules, tool_full_name="netadmin:foo", raw_result="X") is not None
    assert check_escalation(rules, tool_full_name="other:foo", raw_result="X") is None


def test_or_semantics_across_rules_and_values() -> None:
    rules = [
        {"tool": "a:b", "match": "exact", "values": ["one"]},
        {"tool": "*", "match": "exact", "values": ["two", "three"]},
    ]
    assert check_escalation(rules, tool_full_name="x:y", raw_result="three") is not None
    assert check_escalation(rules, tool_full_name="x:y", raw_result="four") is None


def test_guard_runs_on_raw_unmasked_value() -> None:
    """Escalation runs before PII masking — a code embedded next to an email
    (which masking would rewrite) must still be found verbatim."""
    rules = [{"tool": "*", "match": "substring", "values": ["DDOSA"]}]
    raw = {"note": "flagged as DDOSA for alice@example.com"}
    hit = check_escalation(rules, tool_full_name="x:y", raw_result=raw)
    assert hit is not None


def test_broken_rule_is_treated_as_no_match_not_a_hit() -> None:
    rules = [{"tool": "*", "match": "bogus", "values": ["X"]}]
    assert check_escalation(rules, tool_full_name="x:y", raw_result="X") is None


def test_no_rules_never_matches() -> None:
    assert check_escalation(None, tool_full_name="x:y", raw_result="anything") is None
    assert check_escalation([], tool_full_name="x:y", raw_result="anything") is None


def test_validate_escalation_rules_accepts_good_rules() -> None:
    validate_escalation_rules(
        [
            {"tool": "a:b", "match": "exact", "values": ["X"]},
            {"tool": "*", "match": "substring", "values": ["y"]},
        ]
    )


@pytest.mark.parametrize(
    "rule",
    [
        {"match": "exact", "values": ["X"]},  # missing tool
        {"tool": "a:b", "values": ["X"]},  # missing match
        {"tool": "a:b", "match": "weird", "values": ["X"]},  # bad match kind
        {"tool": "a:b", "match": "exact", "values": []},  # empty values
        {"tool": "a:b", "match": "regex", "values": ["("]},  # invalid regex
    ],
)
def test_validate_escalation_rules_rejects_bad_rules(rule: dict[str, object]) -> None:
    with pytest.raises(EscalationRuleError):
        validate_escalation_rules([rule])
