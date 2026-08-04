"""Unit tests for Znuny-compatible Ticket ACL matching and filtering.

Pure tests (no DB) cover ``compare_match_with_data``, property matching, and
Possible / PossibleAdd / PossibleNot application — the core of
``domain/ticket_acl.py``.
"""

from __future__ import annotations

from tiqora.domain.ticket_acl import (
    acl_properties_match,
    apply_possible_filters,
    compare_match_with_data,
    match_property_items,
    parse_acl_yaml,
    properties_block_matches,
)

# ---------------------------------------------------------------------------
# compare_match_with_data
# ---------------------------------------------------------------------------


def test_plain_equality() -> None:
    assert compare_match_with_data("open", "open")["match"] is True
    assert compare_match_with_data("open", "closed")["match"] is False


def test_not_prefix() -> None:
    assert compare_match_with_data("[Not]closed", "open")["match"] is True
    assert compare_match_with_data("[Not]closed", "closed")["match"] is False


def test_regexp_case_sensitive() -> None:
    assert compare_match_with_data("[RegExp]^open", "open")["match"] is True
    assert compare_match_with_data("[RegExp]^Open", "open")["match"] is False


def test_regexp_case_insensitive() -> None:
    assert compare_match_with_data("[regexp]^open", "OPEN")["match"] is True


def test_not_regexp() -> None:
    assert compare_match_with_data("[NotRegExp]closed", "open")["match"] is True
    assert compare_match_with_data("[NotRegExp]closed", "closed successful")["match"] is False


def test_slash_regex_convenience() -> None:
    assert compare_match_with_data("/^Raw/", "Raw")["match"] is True
    assert compare_match_with_data("/^raw/i", "Raw")["match"] is True
    assert compare_match_with_data("/^Raw/", "Junk")["match"] is False


def test_array_not_semantics_via_match_property_items() -> None:
    # [Not]Admin matches when Admin is NOT in the role list.
    assert match_property_items(["[Not]Admin"], ["users", "agents"]) is True
    assert match_property_items(["[Not]Admin"], ["users", "Admin"]) is False
    # Positive array membership.
    assert match_property_items(["Admin"], ["users", "Admin"]) is True
    assert match_property_items(["Admin"], ["users"]) is False


# ---------------------------------------------------------------------------
# Properties matching
# ---------------------------------------------------------------------------


def test_empty_properties_force_match() -> None:
    matched, tried = acl_properties_match({}, {}, {})
    assert matched is True
    assert tried is True


def test_properties_and_properties_database_both_required() -> None:
    config = {
        "Properties": {"Frontend": {"Action": ["AgentTicketPhone"]}},
        "PropertiesDatabase": {"Ticket": {"Queue": ["Support"]}},
    }
    checks = {"Frontend": {"Action": "AgentTicketPhone"}, "Ticket": {"Queue": "Raw"}}
    checks_db = {"Ticket": {"Queue": "Support"}}
    matched, tried = acl_properties_match(config, checks, checks_db)
    assert matched is True
    assert tried is True

    checks_db_bad = {"Ticket": {"Queue": "Raw"}}
    matched2, _ = acl_properties_match(config, checks, checks_db_bad)
    assert matched2 is False


def test_missing_properties_inherits_database() -> None:
    config = {"PropertiesDatabase": {"Ticket": {"State": ["open"]}}}
    checks: dict = {}
    checks_db = {"Ticket": {"State": "open"}}
    matched, tried = acl_properties_match(config, checks, checks_db)
    assert matched is True
    assert tried is True


def test_properties_block_requires_all_fields() -> None:
    block = {
        "Ticket": {
            "Queue": ["Support"],
            "State": ["open"],
        }
    }
    checks = {"Ticket": {"Queue": "Support", "State": "closed"}}
    match, tried = properties_block_matches(block, checks)
    assert tried is True
    assert match is False


def test_user_group_rw_array_match() -> None:
    block = {"User": {"Group_rw": ["admin", "users"]}}
    checks = {"User": {"Group_rw": ["stats", "users"]}}
    match, _ = properties_block_matches(block, checks)
    assert match is True


# ---------------------------------------------------------------------------
# Possible / PossibleAdd / PossibleNot
# ---------------------------------------------------------------------------


def test_possible_replaces_whitelist() -> None:
    data = {1: "1 very low", 2: "2 low", 3: "3 normal", 4: "4 high"}
    current = dict(data)
    change = {"Possible": {"Ticket": {"Priority": ["3 normal", "4 high"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="Priority"
    )
    assert used is True
    assert new == {3: "3 normal", 4: "4 high"}


def test_possible_not_subtracts() -> None:
    data = {1: "open", 2: "closed successful", 3: "closed unsuccessful"}
    current = dict(data)
    change = {"PossibleNot": {"Ticket": {"State": ["[RegExp]^closed"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is True
    assert new == {1: "open"}


def test_possible_add_unions() -> None:
    data = {1: "open", 2: "closed successful", 3: "merged"}
    # Start from a restricted set, then PossibleAdd brings "merged" back.
    current = {1: "open"}
    change = {"PossibleAdd": {"Ticket": {"State": ["merged"]}}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is True
    assert new == {1: "open", 3: "merged"}


def test_possible_then_possible_not_stacked() -> None:
    data = {1: "a", 2: "b", 3: "c", 4: "d"}
    current = dict(data)
    change = {
        "Possible": {"Ticket": {"Queue": ["a", "b", "c"]}},
        "PossibleNot": {"Ticket": {"Queue": ["b"]}},
    }
    new, used = apply_possible_filters(
        data, current, change, return_type="Ticket", return_sub_type="Queue"
    )
    assert used is True
    assert new == {1: "a", 3: "c"}


def test_action_return_type_uses_array_under_action() -> None:
    data = {1: "AgentTicketClose", 2: "AgentTicketNote", 3: "AgentTicketMove"}
    current = dict(data)
    change = {"Possible": {"Action": ["AgentTicketNote", "AgentTicketMove"]}}
    new, used = apply_possible_filters(
        data, current, change, return_type="Action", return_sub_type="-"
    )
    assert used is True
    assert new == {2: "AgentTicketNote", 3: "AgentTicketMove"}


def test_no_matching_section_returns_unused() -> None:
    data = {1: "open"}
    change = {"Possible": {"Ticket": {"Priority": ["3 normal"]}}}
    new, used = apply_possible_filters(
        data, data, change, return_type="Ticket", return_sub_type="State"
    )
    assert used is False
    assert new == data


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


def test_parse_acl_yaml_roundtrip() -> None:
    raw = """
Properties:
  Frontend:
    Action:
      - AgentTicketPhone
Possible:
  Ticket:
    Priority:
      - 3 normal
"""
    # config_match only has Properties; Possible would live in config_change.
    parsed = parse_acl_yaml(raw)
    assert "Properties" in parsed
    assert parsed["Properties"]["Frontend"]["Action"] == ["AgentTicketPhone"]


def test_parse_acl_yaml_empty_and_invalid() -> None:
    assert parse_acl_yaml(None) == {}
    assert parse_acl_yaml("") == {}
    assert parse_acl_yaml("not: [valid: yaml: {{{") == {}
    assert parse_acl_yaml("- just a list") == {}
