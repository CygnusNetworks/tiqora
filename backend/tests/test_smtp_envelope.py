"""Envelope-recipient handling for the Znuny ``SendmailBcc`` equivalent.

``SendmailBcc`` adds an extra envelope-only recipient (RCPT TO) to every
outgoing mail — never a visible header — so the mail server can archive a
copy into the IMAP store (Znuny ``Kernel/System/Email.pm`` semantics).
"""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.channels.email.smtp import SmtpMailSender, build_message, envelope_recipients
from tiqora.domain.mail_outbound import ResolvedOutboundSmtp
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective


def _message(**overrides: Any):
    kwargs: dict[str, Any] = dict(
        from_addr="support@cygnusnetworks.de",
        to_addrs="kunde@example.com",
        cc_addrs=None,
        subject="Re: [Cygnus#123] Test",
        body="Hallo",
        content_type="text/plain",
        in_reply_to=None,
    )
    kwargs.update(overrides)
    return build_message(**kwargs)


def test_envelope_recipients_none_without_extra() -> None:
    msg = _message()
    assert envelope_recipients(msg, None) is None
    assert envelope_recipients(msg, "") is None


def test_envelope_recipients_appends_extra() -> None:
    msg = _message(cc_addrs="cc@example.com", bcc_addrs="bcc@example.com")
    recipients = envelope_recipients(msg, "otrs-watcher@cygnusnetworks.de")
    assert recipients == [
        "kunde@example.com",
        "cc@example.com",
        "bcc@example.com",
        "otrs-watcher@cygnusnetworks.de",
    ]


def test_envelope_recipients_parses_display_names() -> None:
    msg = _message(to_addrs='"Niklas Cyffka" <kunde@example.com>')
    recipients = envelope_recipients(msg, "otrs-watcher@cygnusnetworks.de")
    assert recipients == ["kunde@example.com", "otrs-watcher@cygnusnetworks.de"]


def test_envelope_recipients_deduplicates_extra() -> None:
    msg = _message(to_addrs="otrs-watcher@cygnusnetworks.de")
    recipients = envelope_recipients(msg, "otrs-watcher@cygnusnetworks.de")
    assert recipients == ["otrs-watcher@cygnusnetworks.de"]


def test_sendmail_bcc_never_becomes_a_header() -> None:
    msg = _message()
    envelope_recipients(msg, "otrs-watcher@cygnusnetworks.de")
    assert msg.get_all("Bcc") is None


def test_from_resolved_carries_sendmail_bcc() -> None:
    cfg = ResolvedOutboundSmtp(
        enabled=True,
        host="mail.w359.de",
        port=25,
        security="none",
        auth_type="none",
        auth_user="",
        auth_password="",
        from_default="",
        timeout_seconds=30,
        source="db",
    )
    sender = SmtpMailSender.from_resolved(cfg, sendmail_bcc="otrs-watcher@cygnusnetworks.de")
    assert sender.sendmail_bcc == "otrs-watcher@cygnusnetworks.de"
    assert SmtpMailSender.from_resolved(cfg).sendmail_bcc is None


@pytest.mark.asyncio
async def test_sysconfig_sendmail_bcc_default_empty() -> None:
    async def _fetch(name: str) -> Any:
        return None

    assert await SysConfig(fetch=_fetch).sendmail_bcc() == ""


@pytest.mark.asyncio
async def test_sysconfig_sendmail_bcc_reads_value() -> None:
    async def _fetch(name: str) -> Any:
        if name == "SendmailBcc":
            return yaml_encode_effective("otrs-watcher@cygnusnetworks.de")
        return None

    assert await SysConfig(fetch=_fetch).sendmail_bcc() == "otrs-watcher@cygnusnetworks.de"
