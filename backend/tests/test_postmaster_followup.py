"""Unit tests for znuny/followup.py — TN regex corpus (no DB required)."""

from __future__ import annotations

from typing import Any

from tiqora.znuny.followup import get_tn_by_string

_DATECHECKSUM_KW: dict[str, Any] = {
    "hook": "Ticket#",
    "hook_divider": "",
    "generator": "Kernel::System::Ticket::Number::DateChecksum",
    "system_id": "10",
}

_AUTOINCREMENT_KW: dict[str, Any] = {
    "hook": "Ticket#",
    "hook_divider": "",
    "generator": "Kernel::System::Ticket::Number::AutoIncrement",
    "system_id": "10",
}


def test_datechecksum_hooked_subject_matches() -> None:
    tn = get_tn_by_string("Re: [Ticket#20260719104857123] Help me", **_DATECHECKSUM_KW)
    assert tn == "20260719104857123"


def test_datechecksum_colon_form_matches() -> None:
    tn = get_tn_by_string("Ticket#: 20260719104857123 something", **_DATECHECKSUM_KW)
    assert tn == "20260719104857123"


def test_datechecksum_colon_form_with_two_spaces() -> None:
    tn = get_tn_by_string("Ticket#:  20260719104857123", **_DATECHECKSUM_KW)
    assert tn == "20260719104857123"


def test_no_match_returns_none() -> None:
    assert get_tn_by_string("Just a normal subject", **_DATECHECKSUM_KW) is None


def test_empty_subject_returns_none() -> None:
    assert get_tn_by_string("", **_DATECHECKSUM_KW) is None


def test_case_insensitive_hook() -> None:
    tn = get_tn_by_string("[ticket#20260719104857123]", **_DATECHECKSUM_KW)
    assert tn == "20260719104857123"


def test_autoincrement_min_counter_size() -> None:
    tn = get_tn_by_string("[Ticket#12345]", min_counter_size=5, **_AUTOINCREMENT_KW)
    assert tn == "12345"


def test_autoincrement_ignores_out_of_range_digits() -> None:
    # min=5 max=10 digits; a single digit should not match.
    assert get_tn_by_string("[Ticket#1]", min_counter_size=5, **_AUTOINCREMENT_KW) is None


def test_check_system_id_requires_prefix() -> None:
    kw = dict(_DATECHECKSUM_KW)
    # SystemID '10' must appear literally right after the 8-digit date; this
    # subject's 9th/10th digits are "01", not "10" — no match.
    assert get_tn_by_string("[Ticket#202607190123456789]", check_system_id=True, **kw) is None
    # This one has "10" right after the date part — matches.
    tn = get_tn_by_string("[Ticket#2026071910123456]", check_system_id=True, **kw)
    assert tn == "2026071910123456"


def test_hook_divider_is_used() -> None:
    kw = dict(_DATECHECKSUM_KW)
    kw["hook_divider"] = ":"
    tn = get_tn_by_string("[Ticket#:20260719104857123]", **kw)
    assert tn == "20260719104857123"
