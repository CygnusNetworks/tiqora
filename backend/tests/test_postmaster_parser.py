"""Unit tests for channels/email/parser.py (RFC822 parsing)."""

from __future__ import annotations

from email.message import EmailMessage
from typing import cast

from tiqora.channels.email.parser import get_email_address, parse_email, split_address_line


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
