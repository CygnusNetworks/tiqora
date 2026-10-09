"""``SendNoAutoResponseRegExp`` runs on the ``regex`` package with a time budget.

The pattern is admin configuration, the address comes from a remote mail: a catastrophic
pattern must not block the event loop (same rule as the GI mapping regexes).
"""

from __future__ import annotations

import time

from tiqora.channels.email.autoresponse import _matches_no_auto_response


def test_matches_case_insensitively_like_perl_i() -> None:
    pattern = "(MAILER-DAEMON|postmaster|abuse)@.+?\\..+?"
    assert _matches_no_auto_response(pattern, "Mailer-Daemon@example.org")
    assert not _matches_no_auto_response(pattern, "kim@example.org")
    assert not _matches_no_auto_response("", "kim@example.org")


def test_broken_pattern_never_matches() -> None:
    assert not _matches_no_auto_response("(unclosed", "kim@example.org")


def test_catastrophic_pattern_is_cut_off() -> None:
    started = time.monotonic()
    assert not _matches_no_auto_response("^(a+)+$", "a" * 34 + "!@example.org")
    assert time.monotonic() - started < 2.0
