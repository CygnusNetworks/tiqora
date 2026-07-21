"""Unit tests for reply/forward subject + body quoting helpers (no DB)."""

from __future__ import annotations

from datetime import datetime

from tiqora.domain.quoting import (
    build_forward_subject,
    build_reply_subject,
    build_ticket_subject,
    clean_subject,
    html_to_plaintext,
    quote_plaintext_body,
    strip_ticket_hook,
)
from tiqora.znuny.followup import get_tn_by_string


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


def test_strip_ticket_hook_removes_bracket_tag() -> None:
    assert (
        strip_ticket_hook("[Cygnus#2026071810000011] Re: Test", hook="Cygnus#", divider="")
        == "Re: Test"
    )
    assert strip_ticket_hook("Re: Test [Ticket#12345]", hook="Ticket#", divider="") == "Re: Test"
    assert strip_ticket_hook("plain", hook="Ticket#", divider="") == "plain"


def test_build_ticket_subject_left_right_none() -> None:
    left = build_ticket_subject(
        "Re: Hello",
        hook="Cygnus#",
        divider="",
        tn="2026070100000019",
        subject_format="Left",
    )
    assert left == "[Cygnus#2026070100000019] Re: Hello"

    right = build_ticket_subject(
        "Re: Hello",
        hook="Cygnus#",
        divider="",
        tn="2026070100000019",
        subject_format="Right",
    )
    assert right == "Re: Hello [Cygnus#2026070100000019]"

    none = build_ticket_subject(
        "Re: Hello",
        hook="Cygnus#",
        divider="",
        tn="2026070100000019",
        subject_format="None",
    )
    assert none == "Re: Hello"


def test_build_ticket_subject_idempotent_strip_then_rebuild() -> None:
    hooked = "[Cygnus#2026070100000019] Re: Hello"
    again = build_ticket_subject(
        hooked,
        hook="Cygnus#",
        divider="",
        tn="2026070100000019",
        subject_format="Left",
        add_re=False,
    )
    assert again == hooked
    # Already Re'd + hooked, with add_re still yields exactly one of each.
    with_re = build_ticket_subject(
        hooked,
        hook="Cygnus#",
        divider="",
        tn="2026070100000019",
        subject_format="Left",
        add_re=True,
    )
    assert with_re == "[Cygnus#2026070100000019] Re: Hello"
    assert with_re.count("Re:") == 1
    assert with_re.count("[Cygnus#") == 1


def test_build_ticket_subject_different_hook_divider() -> None:
    out = build_ticket_subject(
        "Help",
        hook="Ticket",
        divider="#",
        tn="12345",
        subject_format="Left",
        add_re=True,
    )
    assert out == "[Ticket#12345] Re: Help"


def test_build_ticket_subject_roundtrip_extract_tn() -> None:
    tn = "20260719104857123"
    subject = build_ticket_subject(
        "Need help",
        hook="Ticket#",
        divider="",
        tn=tn,
        subject_format="Left",
        add_re=True,
    )
    extracted = get_tn_by_string(
        subject,
        hook="Ticket#",
        hook_divider="",
        generator="Kernel::System::Ticket::Number::DateChecksum",
        system_id="10",
    )
    assert extracted == tn

    # Custom hook prefix (Cygnus) with DateChecksum-style tn.
    custom = build_ticket_subject(
        "Need help",
        hook="Cygnus#",
        divider="",
        tn=tn,
        subject_format="Left",
    )
    extracted2 = get_tn_by_string(
        custom,
        hook="Cygnus#",
        hook_divider="",
        generator="Kernel::System::Ticket::Number::DateChecksum",
        system_id="10",
    )
    assert extracted2 == tn


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
