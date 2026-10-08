"""Pure tests for the identity mapping / rule / assignment-diff engine (no DB)."""

from __future__ import annotations

from tiqora.identity.config_models import AttributeMap, GroupGrant, RoleRule
from tiqora.identity.mapping import (
    AssignmentDiff,
    AssignmentState,
    DesiredAssignments,
    diff_assignments,
    evaluate_rules,
    lookup,
    map_profile,
    normalize_attributes,
    normalize_dn,
    rule_matches,
)


def test_normalize_attributes() -> None:
    raw = {
        "email": " a@example.org ",
        "groups": ["g1", "", 7],
        "flag": True,
        "nested": {"x": 1},
        "none": None,
    }
    assert normalize_attributes(raw) == {
        "email": ("a@example.org",),
        "groups": ("g1", "7"),
        "flag": ("True",),
    }


def test_normalize_attributes_odd_input_and_truncation() -> None:
    raw = {
        "long": "x" * 5000,
        "mixed": ["a", None, {"k": 1}, 1.5, False, " "],
        "blank": "  ",
        "empty": [],
    }
    out = normalize_attributes(raw)
    assert out["long"] == ("x" * 2000,)
    assert out["mixed"] == ("a", "1.5", "False")
    assert "blank" not in out  # a blank scalar is dropped ...
    assert out["empty"] == ()  # ... an empty list claim stays present (sync fail-safe)
    assert normalize_attributes({"t": ("a", "b")}) == {"t": ("a", "b")}
    assert normalize_attributes({1: "x"}) == {"1": ("x",)}  # type: ignore[dict-item]


def test_lookup_case_insensitive_only_when_unique() -> None:
    attrs = {"Mail": ("a@example.org",)}
    assert lookup(attrs, "mail") == ("a@example.org",)
    assert lookup({"Mail": ("a",), "MAIL": ("b",)}, "mail") == ()
    assert lookup({"Mail": ("a",), "MAIL": ("b",)}, "MAIL") == ("b",)
    assert lookup({}, "x") == ()


def test_map_profile_first_non_empty_and_literal() -> None:
    am = AttributeMap(
        login=["upn", "preferred_username"], email=["mail"], customer_id=["_Example GmbH"]
    )
    p = map_profile({"preferred_username": ("max",), "mail": ("max@example.org",)}, am)
    assert (p.login, p.email, p.customer_id) == ("max", "max@example.org", "Example GmbH")
    assert p.first_name is None and p.mobile is None


def test_map_profile_skips_empty_and_bare_underscore() -> None:
    am = AttributeMap(login=["_", "missing", "uid"], last_name=["sn"])
    p = map_profile({"uid": ("max",), "sn": ()}, am)
    assert p.login == "max" and p.last_name is None


def test_rules_union_and_valid_filter_and_rw_wins() -> None:
    rules = [
        RoleRule(attribute="groups", value="Support", role_ids=[3, 99]),
        RoleRule(
            attribute="groups",
            value="admins",
            groups=[GroupGrant(group_id=2, permissions=["ro", "rw"])],
        ),
    ]
    d = evaluate_rules(
        {"groups": ("support", "ADMINS")}, rules, valid_role_ids={3}, valid_group_ids={2}
    )
    assert (
        d.role_ids == {3}
        and d.group_perms == {(2, "rw")}
        and d.has_role_rules
        and d.has_group_rules
    )


def test_rw_collapses_across_rules_for_same_group() -> None:
    rules = [
        RoleRule(
            attribute="g", value="a", groups=[GroupGrant(group_id=2, permissions=["ro", "note"])]
        ),
        RoleRule(attribute="g", value="b", groups=[GroupGrant(group_id=2, permissions=["rw"])]),
        RoleRule(
            attribute="g", value="a", groups=[GroupGrant(group_id=7, permissions=["ro", "note"])]
        ),
    ]
    d = evaluate_rules({"g": ("a", "b")}, rules, valid_role_ids=set(), valid_group_ids={2, 7})
    assert d.group_perms == {(2, "rw"), (7, "ro"), (7, "note")}
    assert not d.has_role_rules and d.has_group_rules


def test_has_rules_reflect_configuration_not_matches() -> None:
    rules = [RoleRule(attribute="g", value="a", role_ids=[1])]
    d = evaluate_rules({}, rules, valid_role_ids={1}, valid_group_ids=set())
    assert d.role_ids == frozenset() and d.has_role_rules and not d.has_group_rules
    d2 = evaluate_rules({}, [], valid_role_ids=set(), valid_group_ids=set())
    assert not d2.has_role_rules and not d2.has_group_rules


def test_ldap_group_dn_compare_is_normalised() -> None:
    r = RoleRule(
        attribute="ldap:group_dn", value="CN=Support, OU=Groups,DC=example,DC=org", role_ids=[1]
    )
    assert rule_matches({"ldap:group_dn": ("cn=support,ou=groups,dc=example,dc=org",)}, r)
    assert normalize_dn(" CN = A , OU=B ") == "cn=a,ou=b"
    plain = RoleRule(attribute="groups", value="a b", role_ids=[1])
    assert not rule_matches({"groups": ("a,b",)}, plain)
    assert rule_matches({"groups": ("A B",)}, plain)


def test_managed_diff_keeps_manual_assignments() -> None:
    desired = DesiredAssignments(frozenset({1}), frozenset(), True, False)
    state = AssignmentState(
        current_roles=frozenset({1, 2, 5}),
        current_group_perms=frozenset(),
        managed_roles=frozenset({5}),
        managed_group_perms=frozenset(),
    )
    d = diff_assignments(desired, state, mode="managed")
    assert (
        d.add_roles == frozenset()
        and d.remove_roles == {5}
        and d.record_managed_roles == frozenset()
    )
    assert d.drop_managed_roles == {
        5
    }  # role 2 (manual) untouched; role 1 already present manually -> not recorded


def test_managed_diff_adds_and_records() -> None:
    d = diff_assignments(
        DesiredAssignments(frozenset({1}), frozenset(), True, False),
        AssignmentState(frozenset(), frozenset(), frozenset(), frozenset()),
        mode="managed",
    )
    assert d.add_roles == {1} and d.record_managed_roles == {1}


def test_managed_record_dropped_when_row_vanished() -> None:
    d = diff_assignments(
        DesiredAssignments(frozenset(), frozenset(), True, False),
        AssignmentState(frozenset(), frozenset(), frozenset({4}), frozenset()),
        mode="managed",
    )
    assert d.remove_roles == frozenset() and d.drop_managed_roles == {4}


def test_authoritative_replaces_kind_with_rules_only() -> None:
    desired = DesiredAssignments(frozenset({1}), frozenset(), True, False)  # no group rules
    state = AssignmentState(frozenset({1, 2}), frozenset({(9, "rw")}), frozenset(), frozenset())
    d = diff_assignments(desired, state, mode="authoritative")
    assert (
        d.remove_roles == {2} and d.remove_groups == frozenset() and d.record_managed_roles == {1}
    )


def test_no_rules_no_changes() -> None:
    d = diff_assignments(
        DesiredAssignments(frozenset(), frozenset(), False, False),
        AssignmentState(frozenset({1}), frozenset({(2, "ro")}), frozenset({1}), frozenset()),
        mode="managed",
    )
    assert d.empty
    assert AssignmentDiff().empty


def test_group_add_keeps_manual_keys_next_to_managed_rw() -> None:
    # Managed mode never absorbs manual keys: rw is written next to the manual ro / note.
    desired = DesiredAssignments(frozenset(), frozenset({(2, "rw")}), False, True)
    state = AssignmentState(
        frozenset(), frozenset({(2, "ro"), (2, "note"), (9, "ro")}), frozenset(), frozenset()
    )
    d = diff_assignments(desired, state, mode="managed")
    assert d.add_groups == {(2, "rw")}
    assert d.remove_groups == frozenset()  # group 9 untouched as well
    assert d.final_group_keys == {2: frozenset({"rw", "ro", "note"})}
    assert d.record_managed_groups == {(2, "rw")}


def test_group_no_replacement_when_already_granted() -> None:
    desired = DesiredAssignments(frozenset(), frozenset({(2, "rw")}), False, True)
    state = AssignmentState(
        frozenset(), frozenset({(2, "rw"), (2, "ro")}), frozenset(), frozenset()
    )
    d = diff_assignments(desired, state, mode="managed")
    assert d.add_groups == frozenset() and d.remove_groups == frozenset()
    assert d.record_managed_groups == frozenset()


def test_authoritative_groups_remove_everything_not_desired() -> None:
    desired = DesiredAssignments(frozenset(), frozenset({(2, "ro")}), False, True)
    state = AssignmentState(
        frozenset(), frozenset({(2, "rw"), (9, "ro")}), frozenset(), frozenset({(9, "ro")})
    )
    d = diff_assignments(desired, state, mode="authoritative")
    assert d.add_groups == {(2, "ro")} and d.remove_groups == {(2, "rw"), (9, "ro")}
    assert d.record_managed_groups == {(2, "ro")} and d.drop_managed_groups == {(9, "ro")}


def test_managed_manual_removal_readds_and_rerecords() -> None:
    # Admin removed a managed role by hand; rule still grants it -> added and recorded again.
    d = diff_assignments(
        DesiredAssignments(frozenset({4}), frozenset(), True, False),
        AssignmentState(frozenset(), frozenset(), frozenset({4}), frozenset()),
        mode="managed",
    )
    assert d.add_roles == {4} and d.record_managed_roles == {4}


def _gd(*perms: tuple[int, str]) -> DesiredAssignments:
    return DesiredAssignments(frozenset(), frozenset(perms), False, True)


def _gs(cur: set[tuple[int, str]], managed: set[tuple[int, str]] | None = None) -> AssignmentState:
    return AssignmentState(frozenset(), frozenset(cur), frozenset(), frozenset(managed or ()))


def test_managed_manual_rw_not_downgraded_by_desired_ro() -> None:
    d = diff_assignments(_gd((2, "ro")), _gs({(2, "rw")}), mode="managed")
    assert d.empty and d.final_group_keys == {}


def test_managed_ro_upgraded_to_rw_by_admin_is_left_alone() -> None:
    d = diff_assignments(_gd((2, "ro")), _gs({(2, "rw")}, {(2, "ro")}), mode="managed")
    assert d.empty


def test_managed_manual_non_rw_keys_survive_new_managed_key() -> None:
    d = diff_assignments(_gd((2, "ro")), _gs({(2, "note")}), mode="managed")
    assert d.add_groups == {(2, "ro")} and d.remove_groups == frozenset()
    assert d.final_group_keys == {2: frozenset({"note", "ro"})}
    assert d.record_managed_groups == {(2, "ro")}


def test_managed_new_rw_does_not_absorb_manual_keys() -> None:
    d = diff_assignments(_gd((2, "rw")), _gs({(2, "ro"), (2, "note")}), mode="managed")
    assert d.add_groups == {(2, "rw")} and d.remove_groups == frozenset()
    assert d.final_group_keys == {2: frozenset({"rw", "ro", "note"})}


def test_managed_stale_managed_key_removed_manual_kept() -> None:
    d = diff_assignments(_gd((2, "rw")), _gs({(2, "ro"), (2, "note")}, {(2, "ro")}), mode="managed")
    # rw is new, stale managed ro goes, manual note stays
    assert d.final_group_keys == {2: frozenset({"rw", "note"})}
    assert (2, "ro") in d.drop_managed_groups
    d2 = diff_assignments(_gd(), _gs({(2, "ro"), (2, "note")}, {(2, "ro")}), mode="managed")
    assert d2.remove_groups == {(2, "ro")} and d2.final_group_keys == {2: frozenset({"note"})}
    assert d2.drop_managed_groups == {(2, "ro")}


def test_authoritative_final_keys_replace_group() -> None:
    d = diff_assignments(_gd((2, "ro")), _gs({(2, "rw"), (9, "ro")}), mode="authoritative")
    assert d.final_group_keys == {2: frozenset({"ro"}), 9: frozenset()}


def test_invalid_group_in_only_rule_strips_group_perms_in_authoritative() -> None:
    rules = [
        RoleRule(attribute="g", value="a", groups=[GroupGrant(group_id=77, permissions=["rw"])])
    ]
    desired = evaluate_rules({"g": ("a",)}, rules, valid_role_ids=set(), valid_group_ids=set())
    assert desired.has_group_rules and desired.group_perms == frozenset()
    state = _gs({(2, "rw")})
    d = diff_assignments(desired, state, mode="authoritative")
    assert d.remove_groups == {(2, "rw")} and d.final_group_keys == {2: frozenset()}
    assert diff_assignments(desired, state, mode="managed").empty


def test_managed_rw_downgraded_by_rule_to_ro() -> None:
    d = diff_assignments(_gd((2, "ro")), _gs({(2, "rw")}, {(2, "rw")}), mode="managed")
    assert d.final_group_keys == {2: frozenset({"ro"})}
    assert d.add_groups == {(2, "ro")} and d.remove_groups == {(2, "rw")}
    assert d.record_managed_groups == {(2, "ro")} and d.drop_managed_groups == {(2, "rw")}


def test_managed_rw_downgraded_by_rule_to_ro_and_note() -> None:
    d = diff_assignments(_gd((2, "ro"), (2, "note")), _gs({(2, "rw")}, {(2, "rw")}), mode="managed")
    assert d.final_group_keys == {2: frozenset({"ro", "note"})}


def test_manual_rw_with_stale_managed_ro_and_nothing_desired() -> None:
    d = diff_assignments(_gd(), _gs({(2, "rw"), (2, "ro")}, {(2, "ro")}), mode="managed")
    assert d.remove_groups == {(2, "ro")} and d.final_group_keys == {2: frozenset({"rw"})}
    assert d.drop_managed_groups == {(2, "ro")}


def test_authoritative_strips_only_valid_roles_and_groups() -> None:
    desired = DesiredAssignments(frozenset({1}), frozenset({(2, "ro")}), True, True)
    state = AssignmentState(
        frozenset({1, 7, 8}),
        frozenset({(2, "note"), (9, "rw"), (10, "rw")}),
        frozenset(),
        frozenset(),
    )
    d = diff_assignments(
        desired, state, mode="authoritative", valid_role_ids={1, 7}, valid_group_ids={2, 9}
    )
    assert d.remove_roles == {7}  # 8 is invalid: left alone
    assert d.remove_groups == {(2, "note"), (9, "rw")}  # group 10 is invalid: left alone
    assert 10 not in d.final_group_keys


def test_attribute_present_distinguishes_empty_from_absent() -> None:
    from tiqora.identity.mapping import attribute_present

    attrs = normalize_attributes({"groups": [], "Mail": "a@example.org"})
    assert attribute_present(attrs, "groups") and attribute_present(attrs, "mail")
    assert not attribute_present(attrs, "roles")
