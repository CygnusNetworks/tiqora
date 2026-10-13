"""Follow-up detection with the regex of a plugin ticket number scheme (pure, D7/F9)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.plugins.number_generators import anchored_pattern
from tiqora.znuny.followup import get_tn_by_string, get_tn_candidates

KW: dict[str, Any] = {
    "hook": "Ticket#",
    "hook_divider": "",
    "generator": "Kernel::System::Ticket::Number::DateChecksum",
    "system_id": "10",
}
PLUGIN = [r"\d{4}-\d{6}"]


def test_followup_matches_plugin_and_znuny_numbers() -> None:
    # the second accepted shape is "<Hook>: <number>" with the whole hook ("Ticket#:"), as in Znuny
    assert (
        get_tn_by_string("Re: [Ticket#2026-000042] x", extra_patterns=PLUGIN, **KW) == "2026-000042"
    )
    assert (
        get_tn_by_string("Re: [Ticket#2026101210000017] x", extra_patterns=PLUGIN, **KW)
        == "2026101210000017"
    )
    assert get_tn_by_string("Ticket#: 2026-000042", extra_patterns=PLUGIN, **KW) == "2026-000042"


def test_without_extra_patterns_a_plugin_number_is_not_found() -> None:
    assert get_tn_by_string("Re: [Ticket#2026-000042] x", **KW) is None


def test_plugin_pattern_does_not_match_the_start_of_a_longer_number() -> None:
    # \d{4}-\d{6} must not be satisfied by the first digits of "2026-0000421"
    assert get_tn_by_string("[Ticket#2026-0000421]", extra_patterns=PLUGIN, **KW) is None
    assert get_tn_by_string("[Ticket#2026-000042x]", extra_patterns=PLUGIN, **KW) is None


def test_candidates_list_plugin_numbers_first_then_znuny_numbers() -> None:
    subject = "Re: [Ticket#2026101210000017] und Ticket#: 2026-000042"
    assert get_tn_candidates(subject, extra_patterns=PLUGIN, **KW) == [
        "2026-000042",
        "2026101210000017",
    ]
    assert get_tn_candidates("kein Treffer", extra_patterns=PLUGIN, **KW) == []
    assert get_tn_candidates("", extra_patterns=PLUGIN, **KW) == []


def test_hook_without_divider_ending_in_a_letter_still_matches() -> None:
    kw = {**KW, "hook": "Ticket"}
    assert get_tn_by_string("[Ticket2026-000042]", extra_patterns=PLUGIN, **kw) == "2026-000042"


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        (r"\d{4}-\d{6}", "2026-000042", True),
        (r"\d{4}-\d{6}", "2026-000042 ", True),
        (r"\d{4}-\d{6}", "2026-0000421", False),
        (r"(?:A|B)-\d{3}", "B-123", True),
    ],
)
def test_anchored_pattern_has_one_group_and_a_trailing_boundary(
    pattern: str, text: str, expected: bool
) -> None:
    import re

    compiled = re.compile(anchored_pattern(pattern))
    assert compiled.groups == 1
    assert (compiled.match(text) is not None) is expected


def test_short_plugin_pattern_does_not_swallow_a_znuny_number() -> None:
    # F9: \d{4} must not match the first digits of a 16-digit Znuny number
    assert get_tn_candidates("Re: [Ticket#2026101210000017]", extra_patterns=[r"\d{4}"], **KW) == [
        "2026101210000017"
    ]
