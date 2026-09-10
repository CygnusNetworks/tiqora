"""Unit tests for channels/email/parser.py (RFC822 parsing)."""

from __future__ import annotations

from email.message import EmailMessage
from typing import cast

from tiqora.channels.email.parser import get_email_address, parse_email, split_address_line
from tiqora.channels.email.pipeline import _build_get_param


def _to_bytes(msg: EmailMessage) -> bytes:
    return msg.as_bytes()


def test_plain_text_email() -> None:
    msg = EmailMessage()
    msg["From"] = "Alice <alice@example.com>"
    msg["To"] = "support@example.com"
    msg["Subject"] = "Hello there"
    msg["Message-ID"] = "<abc123@example.com>"
    msg.set_content("Plain body text.")

    parsed = parse_email(_to_bytes(msg))

    assert parsed.subject == "Hello there"
    assert parsed.from_address == "alice@example.com"
    assert parsed.body.strip() == "Plain body text."
    assert parsed.content_type == "text/plain; charset=utf-8"
    assert parsed.message_id == "abc123@example.com"
    assert parsed.attachments == []


def test_html_with_inline_attachment_and_cid() -> None:
    msg = EmailMessage()
    msg["From"] = "bob@example.com"
    msg["To"] = "support@example.com"
    msg["Subject"] = "HTML mail"
    msg.set_content("Plain fallback")
    msg.add_alternative("<html><body><b>Bold</b> text</body></html>", subtype="html")
    # Attach an inline image with a Content-ID.
    html_part = cast(list[EmailMessage], msg.get_payload())[1]
    html_part.add_related(b"\x89PNG\r\n", maintype="image", subtype="png", cid="<logo123>")

    parsed = parse_email(_to_bytes(msg))

    # Plain part wins when both are present.
    assert "Plain fallback" in parsed.body
    inline = [a for a in parsed.attachments if a.disposition == "inline"]
    assert len(inline) == 1
    assert inline[0].content_id == "logo123"
    assert inline[0].content_type == "image/png"


def test_html_only_falls_back_to_tag_strip() -> None:
    msg = EmailMessage()
    msg["From"] = "carol@example.com"
    msg["To"] = "support@example.com"
    msg["Subject"] = "HTML only"
    msg.set_content("<p>Hello <b>World</b></p>", subtype="html")

    parsed = parse_email(_to_bytes(msg))

    assert "Hello" in parsed.body
    assert "World" in parsed.body
    assert "<p>" not in parsed.body
    assert parsed.content_type == "text/plain; charset=utf-8"


def test_base64_attachment_roundtrips() -> None:
    msg = EmailMessage()
    msg["From"] = "dave@example.com"
    msg["To"] = "support@example.com"
    msg["Subject"] = "With attachment"
    msg.set_content("See attached.")
    payload = b"%PDF-1.4 fake pdf content"
    msg.add_attachment(payload, maintype="application", subtype="pdf", filename="doc.pdf")

    parsed = parse_email(_to_bytes(msg))

    assert len(parsed.attachments) == 1
    att = parsed.attachments[0]
    assert att.filename == "doc.pdf"
    assert att.content_type == "application/pdf"
    assert att.content == payload
    assert att.disposition == "attachment"


def test_broken_charset_does_not_raise() -> None:
    # Hand-craft a message with a bogus charset label but latin-1 bytes.
    raw = (
        b"From: eve@example.com\r\n"
        b"To: support@example.com\r\n"
        b"Subject: broken charset\r\n"
        b"Content-Type: text/plain; charset=totally-bogus-charset\r\n"
        b"\r\n"
        b"\xe9\xe8 body text\r\n"
    )
    parsed = parse_email(raw)
    assert "body text" in parsed.body


def test_references_include_in_reply_to() -> None:
    raw = (
        b"From: frank@example.com\r\n"
        b"To: support@example.com\r\n"
        b"Subject: re: thread\r\n"
        b"Message-ID: <new@example.com>\r\n"
        b"In-Reply-To: <orig@example.com>\r\n"
        b"References: <root@example.com> <orig@example.com>\r\n"
        b"\r\n"
        b"body\r\n"
    )
    parsed = parse_email(raw)
    assert parsed.references == ["root@example.com", "orig@example.com"]
    assert parsed.in_reply_to == "orig@example.com"


def test_huge_subject_is_preserved() -> None:
    subject = "A" * 5000
    msg = EmailMessage()
    msg["From"] = "grace@example.com"
    msg["To"] = "support@example.com"
    msg["Subject"] = subject
    msg.set_content("body")

    parsed = parse_email(_to_bytes(msg))
    assert parsed.subject == subject


def test_get_email_address_and_split_address_line() -> None:
    assert get_email_address("Alice <alice@example.com>") == "alice@example.com"
    assert get_email_address("") == ""
    addrs = split_address_line("Alice <alice@example.com>, bob@example.com")
    assert len(addrs) == 2


def test_split_address_line_requotes_comma_display_name() -> None:
    """ "Nachname, Vorname" (common German Outlook/Exchange format) must round-trip
    through get_email_address without losing its address — a bare f-string
    reformat used to drop the comma-quoting and silently lose the address."""
    line = (
        "Cygnus Networks GmbH - Support <support@example.com>, "
        '"Potulski, Allan Jens" <j.potulski@example.com>, '
        '"Nitsche, Christopher" <c.nitsche@example.com>'
    )
    entries = split_address_line(line)
    assert len(entries) == 3
    addrs = [get_email_address(e) for e in entries]
    assert addrs == ["support@example.com", "j.potulski@example.com", "c.nitsche@example.com"]


def _message_with(**headers: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "someone@example.test"
    msg["To"] = "support@example.test"
    msg["Subject"] = "Request"
    for name, value in headers.items():
        msg[name] = value
    msg.set_content("body")
    return msg


def _get_param(**headers: str) -> dict[str, str]:
    msg = EmailMessage()
    msg["From"] = "someone@example.test"
    msg["To"] = "support@example.test"
    msg["Subject"] = "Out of office"
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    msg.set_content("I am away.")
    return _build_get_param(parse_email(_to_bytes(msg)), trusted=False)


def test_rfc3834_and_bulk_markers_become_the_loop_flag() -> None:
    """PostMaster.pm:602-614 folds every marker of machine-generated mail into
    X-OTRS-Loop, the single flag the auto-response check reads. Without it a
    vacation responder that sets only the RFC 3834 header gets an auto-response
    back and the two systems answer each other."""
    for header, value in (
        ("Auto-Submitted", "auto-replied"),
        ("Auto-Submitted", "auto-generated; owner=someone"),
        ("Precedence", "bulk"),
        ("X-Loop", "yes"),
        ("X-No-Loop", "yes"),
        ("Mailing-List", "list support.example.test"),
    ):
        assert _get_param(**{header.replace("-", "_"): value})["X-OTRS-Loop"] == "yes", header


def test_ordinary_mail_keeps_no_loop_flag() -> None:
    assert "X-OTRS-Loop" not in _get_param()
    # RFC 3834's value for "this is not automated" must not trip the check.
    assert "X-OTRS-Loop" not in _get_param(Auto_Submitted="no")


def test_untrusted_mail_cannot_set_the_loop_flag_itself() -> None:
    """PostMaster.pm:581 drops every ``x-otrs*`` header from an untrusted
    mailbox, so on the normal account the flag can only come from the standard
    markers -- which is why deriving it there is what makes loop protection
    work at all, not a nicety."""
    param = _get_param(X_OTRS_Loop="no")
    assert "X-OTRS-Loop" not in param

    param = _get_param(X_OTRS_Loop="no", Precedence="bulk")
    assert param["X-OTRS-Loop"] == "yes"


def test_trusted_x_otrs_headers_reach_their_canonical_name() -> None:
    """Znuny pulls these under the exact names from ``PostmasterX-Header``.
    Deriving the spelling instead yields ``X-Otrs-Queue`` /
    ``X-Otrs-Isvisibleforcustomer``, and every consumer lookup misses."""
    param = _build_get_param(
        parse_email(
            _to_bytes(
                _message_with(
                    **{
                        "X-OTRS-Queue": "support",
                        "X-OTRS-IsVisibleForCustomer": "0",
                        "X-OTRS-State-PendingTime": "2026-09-11 08:00:00",
                        "X-OTRS-OwnerID": "42",
                    }
                )
            )
        ),
        trusted=True,
    )
    assert param["X-OTRS-Queue"] == "support"
    assert param["X-OTRS-IsVisibleForCustomer"] == "0"
    assert param["X-OTRS-State-PendingTime"] == "2026-09-11 08:00:00"
    assert param["X-OTRS-OwnerID"] == "42"


def test_unknown_headers_keep_the_derived_spelling() -> None:
    param = _build_get_param(
        parse_email(_to_bytes(_message_with(**{"X-Custom-Thing": "v"}))), trusted=True
    )
    assert param["X-Custom-Thing"] == "v"
