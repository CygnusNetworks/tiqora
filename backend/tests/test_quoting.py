"""Unit tests for reply/forward subject + body quoting helpers (no DB)."""

from __future__ import annotations

from datetime import datetime

from tiqora.domain.quoting import (
    build_forward_subject,
    build_reply_subject,
    clean_subject,
    html_to_plaintext,
    quote_plaintext_body,
)


def test_clean_subject_strips_repeated_prefixes() -> None:
    assert clean_subject("Re: Aw: Antw: Hello") == "Hello"
    assert clean_subject("Fwd: Wg: Report") == "Report"
    assert clean_subject("Plain subject") == "Plain subject"
    assert clean_subject(None) == ""


def test_build_reply_subject_no_double_prefix() -> None:
    assert build_reply_subject("Hello") == "Re: Hello"
    assert build_reply_subject("Re: Hello") == "Re: Hello"
    assert build_reply_subject("RE: aw: Hello") == "Re: Hello"


def test_build_forward_subject() -> None:
    assert build_forward_subject("Hello") == "Fwd: Hello"
    assert build_forward_subject("Fwd: Hello") == "Fwd: Hello"


def test_quote_plaintext_body_attribution_and_prefix() -> None:
    out = quote_plaintext_body(
        "line one\nline two",
        from_address="cust@example.com",
        sent_at=datetime(2026, 6, 26, 10, 5),
    )
    lines = out.splitlines()
    assert lines[0] == "On 2026-06-26 10:05, cust@example.com wrote:"
    assert lines[1] == "> line one"
    assert lines[2] == "> line two"


def test_quote_plaintext_body_blank_lines_use_bare_marker() -> None:
    out = quote_plaintext_body("a\n\nb", from_address=None, sent_at=None)
    assert "unknown sender" in out.splitlines()[0]
    assert ">" in out
    # Blank line quoted as bare ">" (no trailing space)
    assert "> \n" not in out


def test_html_to_plaintext_strips_tags() -> None:
    out = html_to_plaintext("<p>Hello <b>world</b></p><br>Line2 &amp; more")
    assert "Hello world" in out
    assert "Line2 & more" in out
    assert "<" not in out
