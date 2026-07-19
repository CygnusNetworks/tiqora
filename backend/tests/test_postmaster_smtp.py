"""Unit tests for channels/email/smtp.py (message building + capturing sender)."""

from __future__ import annotations

from tiqora.channels.email.smtp import CapturingMailSender, build_message


def test_build_message_plain() -> None:
    msg = build_message(
        from_addr="Support <support@example.com>",
        to_addrs="alice@example.com",
        cc_addrs=None,
        subject="Re: your ticket",
        body="We received your request.",
        content_type="text/plain",
        in_reply_to="<orig@example.com>",
    )
    assert msg["From"] == "Support <support@example.com>"
    assert msg["To"] == "alice@example.com"
    assert msg["Subject"] == "Re: your ticket"
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert msg["References"] == "<orig@example.com>"
    assert msg["X-OTRS-Loop"] == "yes"
    assert msg.get_content_type() == "text/plain"
    assert "received your request" in msg.get_content()


def test_build_message_html_content_type() -> None:
    msg = build_message(
        from_addr="support@example.com",
        to_addrs="bob@example.com",
        cc_addrs="cc@example.com",
        subject="HTML reply",
        body="<p>Hi</p>",
        content_type="text/html; charset=utf-8",
        in_reply_to=None,
    )
    assert msg.get_content_type() == "text/html"
    assert msg["Cc"] == "cc@example.com"
    assert "In-Reply-To" not in msg


async def test_capturing_mail_sender_records_messages() -> None:
    sender = CapturingMailSender()
    msg = build_message(
        from_addr="a@example.com",
        to_addrs="b@example.com",
        cc_addrs=None,
        subject="s",
        body="b",
        content_type="text/plain",
        in_reply_to=None,
    )
    await sender.send(msg)
    assert sender.sent == [msg]
