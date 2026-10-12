"""CMDB attribute value normalisation (pure, no database)."""

from __future__ import annotations

from typing import Any

import pytest

from tests._cmdb_helpers import ATTRS
from tiqora.cmdb.definition import AttributeDef
from tiqora.cmdb.values import (
    REF_TYPES,
    canonical_json,
    collect_references,
    normalize_values,
    unknown_top_level_keys,
)


def _one(type_: str, raw: Any, **kw: Any) -> tuple[Any, list[tuple[str, str]]]:
    attrs = [AttributeDef(key="X", name="X", type=type_, **kw)]
    vals, issues = normalize_values(attrs, {"X": [{"v": raw}]})
    return vals.get("X"), [(i.path, i.code) for i in issues]


def test_normalizes_types_and_sub_entries() -> None:
    vals, issues = normalize_values(
        ATTRS,
        {
            "CPU": [{"v": "8"}],
            "RAM": [{"v": "16.5"}],
            "NIC": [
                {"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.10"}, {"v": "2001:db8::1/64"}]}}
            ],
            "Purchase": [{"v": "2026-01-31"}],
            "Cost": [{"v": "1200"}],
        },
    )
    assert issues == []
    assert vals["CPU"] == [{"v": 8}]
    assert vals["RAM"] == [{"v": "16.5"}]
    assert vals["Cost"] == [{"v": "1200"}]
    assert vals["NIC"][0]["s"]["IPAddress"] == [{"v": "192.0.2.10"}, {"v": "2001:db8::1/64"}]
    assert vals["Purchase"] == [{"v": "2026-01-31"}]


def test_decimal_scale_does_not_create_a_second_form() -> None:
    # F21: "16.5" and "16.50" are the same stored value
    a, _ = _one("decimal", "16.50")
    b, _ = _one("decimal", "16.5")
    c, _ = _one("money", "1.2E+3", options={"currency": "EUR"})
    assert a == b == [{"v": "16.5"}]
    assert c == [{"v": "1200"}]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "abc", True])
def test_decimal_rejects_non_numbers(bad: Any) -> None:
    vals, issues = _one("decimal", bad)
    assert vals is None
    assert issues == [("X[1]", "bad_type")]


def test_count_max_and_required_and_bad_format() -> None:
    raw = {
        "NIC": [{"v": f"eth{i}"} for i in range(4)],
        "Vendor": [{"v": ""}],
        "Purchase": [{"v": "31.01.2026"}],
        "CPU": [{"v": 1.5}],
    }
    attrs = [a.model_copy(update={"required": True}) if a.key == "Vendor" else a for a in ATTRS]
    _, issues = normalize_values(attrs, raw)
    assert {(i.path, i.code) for i in issues} >= {
        ("NIC", "too_many"),
        ("Vendor[1]", "required"),
        ("Purchase[1]", "bad_format"),
        ("CPU[1]", "bad_type"),
    }


def test_empty_entries_beyond_count_min_are_dropped() -> None:
    # F18: a non-required attribute drops empty entries; a required one reports them
    vals, issues = normalize_values(
        ATTRS, {"Vendor": [{"v": ""}], "NIC": [{"v": None, "s": {"IPAddress": [{"v": ""}]}}]}
    )
    assert issues == []
    assert "Vendor" not in vals
    assert "NIC" not in vals


def test_required_sub_attribute_only_counts_inside_a_present_entry() -> None:
    nic = next(a for a in ATTRS if a.key == "NIC")
    sub = nic.sub[0].model_copy(update={"required": True})
    attrs = [nic.model_copy(update={"sub": [sub]}) if a.key == "NIC" else a for a in ATTRS]
    _, empty_parent = normalize_values(
        attrs, {"NIC": [{"v": None, "s": {"IPAddress": [{"v": ""}]}}]}
    )
    assert empty_parent == []
    _, blank_in_present = normalize_values(
        attrs, {"NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": ""}]}}]}
    )
    assert [(i.path, i.code) for i in blank_in_present] == [("NIC[1].IPAddress[1]", "required")]


def test_count_min_reports_too_few() -> None:
    attrs = [AttributeDef(key="X", name="X", type="text", count_min=2, count_max=3)]
    _, issues = normalize_values(attrs, {"X": [{"v": "a"}]})
    assert [(i.path, i.code) for i in issues] == [("X", "too_few")]
    _, absent = normalize_values(attrs, {})
    assert [(i.path, i.code) for i in absent] == [("X", "too_few")]


def test_unknown_key_reported_and_removed() -> None:
    vals, issues = normalize_values(ATTRS, {"Gone": [{"v": "x"}]})
    assert "Gone" not in vals
    assert [(i.path, i.code) for i in issues] == [("Gone", "unknown_key")]


def test_unknown_sub_key_is_reported_with_its_path() -> None:
    vals, issues = normalize_values(ATTRS, {"NIC": [{"v": "eth0", "s": {"Nope": [{"v": "x"}]}}]})
    assert vals["NIC"] == [{"v": "eth0", "s": {}}]
    assert [(i.path, i.code) for i in issues] == [("NIC[1].Nope", "unknown_key")]


def test_ip_and_mac_normalised() -> None:
    attrs = [
        AttributeDef(key="Ip", name="IP", type="ip"),
        AttributeDef(key="Mac", name="MAC", type="mac"),
    ]
    # leading zeros are rejected by ipaddress -> bad_format; plain addresses pass
    vals, issues = normalize_values(attrs, {"Ip": [{"v": " 192.0.2.010 "}]})
    assert "Ip" not in vals
    assert [(i.path, i.code) for i in issues] == [("Ip[1]", "bad_format")]
    vals2, issues2 = normalize_values(
        attrs, {"Ip": [{"v": "192.0.2.10"}], "Mac": [{"v": "AA-BB-CC-00-11-22"}]}
    )
    assert issues2 == []
    assert vals2["Ip"] == [{"v": "192.0.2.10"}]
    assert vals2["Mac"] == [{"v": "aa:bb:cc:00:11:22"}]


def test_mac_accepts_other_separators_and_rejects_short() -> None:
    assert _one("mac", "aabb.ccdd.eeff")[0] == [{"v": "aa:bb:cc:dd:ee:ff"}]
    assert _one("mac", "aa:bb:cc")[1] == [("X[1]", "bad_format")]


def test_email_and_url_formats() -> None:
    assert _one("email", "a@example.org")[0] == [{"v": "a@example.org"}]
    assert _one("email", "not-an-email")[1] == [("X[1]", "bad_format")]
    assert _one("url", "https://example.org/x")[0] == [{"v": "https://example.org/x"}]
    assert _one("url", "javascript:alert(1)")[1] == [("X[1]", "bad_format")]
    assert _one("url", "ftp://example.org")[1] == [("X[1]", "bad_format")]
    assert _one("url", "https://")[1] == [("X[1]", "bad_format")]


def test_datetime_is_stored_as_naive_utc() -> None:
    assert _one("datetime", "2026-10-12T08:30")[0] == [{"v": "2026-10-12T08:30:00"}]
    assert _one("datetime", "2026-10-12T10:30:15+02:00")[0] == [{"v": "2026-10-12T08:30:15"}]
    assert _one("datetime", "2026-10-12T08:30:00Z")[0] == [{"v": "2026-10-12T08:30:00"}]
    assert _one("datetime", "12.10.2026")[1] == [("X[1]", "bad_format")]


def test_naive_datetime_uses_now_tz() -> None:
    attrs = [AttributeDef(key="X", name="X", type="datetime")]
    vals, _ = normalize_values(attrs, {"X": [{"v": "2026-10-12T10:00"}]}, now_tz="Europe/Berlin")
    assert vals["X"] == [{"v": "2026-10-12T08:00:00"}]


def test_date_must_exist() -> None:
    assert _one("date", "2026-02-30")[1] == [("X[1]", "bad_format")]


def test_range_and_length_limits() -> None:
    assert _one("integer", 5, options={"min": 10})[1] == [("X[1]", "out_of_range")]
    assert _one("integer", 11, options={"min": 10, "max": 12})[0] == [{"v": 11}]
    assert _one("text", "abcd", options={"max_length": 3})[1] == [("X[1]", "too_long")]
    assert _one("text", "x" * 256)[1] == [("X[1]", "too_long")]


def test_ref_types_coerce_to_int_and_reject_bool() -> None:
    assert _one("ci_ref", "742001")[0] == [{"v": 742001}]
    assert _one("service", True)[1] == [("X[1]", "bad_type")]
    assert _one("queue", "abc")[1] == [("X[1]", "bad_type")]


def test_group_entries_carry_only_sub_values() -> None:
    vals, issues = normalize_values(ATTRS, {"Specs": [{"v": None, "s": {"Disk": [{"v": "sda"}]}}]})
    assert issues == []
    assert vals["Specs"] == [{"v": None, "s": {"Disk": [{"v": "sda"}]}}]


def test_bad_shapes_are_issues_not_exceptions() -> None:
    _, issues = normalize_values(ATTRS, {"Vendor": "x", "Model": ["x"]})
    assert {(i.path, i.code) for i in issues} == {("Vendor", "bad_type"), ("Model[1]", "bad_type")}


def test_normalization_is_idempotent() -> None:
    raw = {"CPU": [{"v": "8"}], "RAM": [{"v": "16.50"}], "Cost": [{"v": "1200.00"}]}
    once, _ = normalize_values(ATTRS, raw)
    twice, issues = normalize_values(ATTRS, once)
    assert issues == []
    assert twice == once


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": [{"v": 1}], "a": [{"v": "ä"}]}) == canonical_json(
        {"a": [{"v": "ä"}], "b": [{"v": 1}]}
    )
    assert canonical_json({"a": [{"v": "ä"}]}) == '{"a":[{"v":"ä"}]}'


def test_collect_references_walks_sub_entries() -> None:
    refs = collect_references(
        ATTRS, {"Location": [{"v": 742001}], "Owner": [{"v": "cmdb4-cust-a@example.org"}]}
    )
    assert refs == {"ci_ref": {742001}, "customer_user": {"cmdb4-cust-a@example.org"}}
    nested = AttributeDef(
        key="G",
        name="G",
        type="group",
        sub=[AttributeDef(key="Svc", name="S", type="service")],
    )
    assert collect_references([nested], {"G": [{"v": None, "s": {"Svc": [{"v": 7}]}}]}) == {
        "service": {7}
    }
    assert collect_references(ATTRS, {"Vendor": [{"v": "x"}]}) == {}


def test_ref_types_cover_all_id_and_login_types() -> None:
    assert {"ci_ref", "customer_user", "customer_company", "agent", "catalog"} <= REF_TYPES
    assert "text" not in REF_TYPES


def test_unknown_top_level_keys() -> None:
    assert unknown_top_level_keys(ATTRS, {"Vendor": [], "Z": [], "A": []}) == ["A", "Z"]
