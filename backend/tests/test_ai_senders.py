"""Unit tests for tiqora.ai.senders (plan block 2). Pure functions, no DB."""

from __future__ import annotations

from tiqora.ai.senders import matches_ignored


def test_exact_match() -> None:
    assert matches_ignored("noreply@example.com", ["noreply@example.com"])


def test_exact_match_is_case_insensitive() -> None:
    assert matches_ignored("NoReply@Example.COM", ["noreply@example.com"])


def test_domain_glob_match() -> None:
    assert matches_ignored("someone@internal.example", ["*@internal.example"])
    assert not matches_ignored("someone@other.example", ["*@internal.example"])


def test_display_name_format_is_parsed() -> None:
    assert matches_ignored("System Notifier <noreply@example.com>", ["noreply@example.com"])


def test_empty_pattern_list_never_matches() -> None:
    assert not matches_ignored("someone@example.com", [])


def test_empty_from_address_never_matches() -> None:
    assert not matches_ignored(None, ["*@example.com"])
    assert not matches_ignored("", ["*@example.com"])


def test_no_match_when_pattern_absent() -> None:
    assert not matches_ignored("agent@example.com", ["noreply@example.com", "*@internal.example"])


def test_blank_and_garbage_patterns_are_skipped() -> None:
    assert not matches_ignored("agent@example.com", ["", "   "])
