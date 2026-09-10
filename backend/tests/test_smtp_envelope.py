"""Envelope-recipient handling for the Znuny ``SendmailBcc`` equivalent.

``SendmailBcc`` adds an extra envelope-only recipient (RCPT TO) to every
outgoing mail — never a visible header — so the mail server can archive a
copy into the IMAP store (Znuny ``Kernel/System/Email.pm`` semantics).
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from typing import Any

import pytest

from tiqora.channels.email.smtp import (
    SmtpMailSender,
    build_message,
    envelope_recipients,
    message_id_domain,
)
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


def test_every_mail_carries_date_and_message_id() -> None:
    """RFC 5322 requires both, and nothing else in the send path adds them:
    a notification used to arrive with no Date at all, so clients fell back to
    the delivery time and spam filters scored the omission."""
    msg = _message()
    assert parsedate_to_datetime(str(msg["Date"])) is not None
    message_id = str(msg["Message-ID"])
    assert message_id.startswith("<") and message_id.endswith(">")
    assert message_id.endswith("@cygnusnetworks.de>")


def test_supplied_message_id_is_kept() -> None:
    """An article's stored id must reach the wire unchanged or follow-ups
    cannot be threaded onto the row the portal shows."""
    msg = _message(message_id="tiqora.abc123@example.test")
    assert str(msg["Message-ID"]) == "<tiqora.abc123@example.test>"


def test_loop_hint_sets_the_znuny_automated_mail_headers() -> None:
    """Kernel::System::Email::Send with ``Loop => 1``."""
    msg = _message()
    assert str(msg["X-OTRS-Loop"]) == "yes"
    assert str(msg["X-Loop"]) == "yes"
    assert str(msg["Precedence"]) == "bulk"
    assert str(msg["Auto-Submitted"]) == "auto-generated"


def test_banner_headers_reach_every_kind_of_mail() -> None:
    """Email.pm writes Organization and X-Mailer outside its Loop block, so a
    human agent's reply carries them exactly like a notification does."""
    banner = {"Organization": "Cygnus Networks GmbH", "X-Mailer": "Tiqora Mail Service (1.2.3)"}
    for kwargs in ({"loop_hint": True}, {"loop_hint": False}):
        msg = _message(extra_headers=banner, **kwargs)
        assert str(msg["Organization"]) == "Cygnus Networks GmbH"
        assert str(msg["X-Mailer"]) == "Tiqora Mail Service (1.2.3)"


def test_banner_never_overwrites_a_header_already_set() -> None:
    msg = _message(message_id="<kept@example.test>", extra_headers={"Message-ID": "<other@x>"})
    assert msg.get_all("Message-ID") == ["<kept@example.test>"]


@pytest.mark.asyncio
async def test_banner_headers_follow_znuny_config() -> None:
    values: dict[str, Any] = {}

    async def _fetch(name: str) -> Any:
        return values.get(name)

    sysconfig = SysConfig(fetch=_fetch)
    # No Organization configured: the mailer banner still goes out, as in Znuny.
    assert list(await sysconfig.mail_banner_headers()) == ["X-Mailer"]

    values["Organization"] = yaml_encode_effective("Cygnus Networks GmbH")
    sysconfig.clear_cache()
    headers = await sysconfig.mail_banner_headers()
    assert headers["Organization"] == "Cygnus Networks GmbH"
    assert headers["X-Mailer"].startswith("Tiqora Mail Service (")

    # Znuny's own escape hatch against the banner being scored as spam.
    values["Secure::DisableBanner"] = yaml_encode_effective("1")
    sysconfig.clear_cache()
    assert "X-Mailer" not in await sysconfig.mail_banner_headers()


def test_agent_reply_carries_no_automated_mail_headers() -> None:
    """A human reply marked auto-generated gets filed as bulk by the recipient."""
    msg = _message(loop_hint=False)
    for header in ("X-OTRS-Loop", "X-Loop", "Precedence", "Auto-Submitted"):
        assert header not in msg


@pytest.mark.asyncio
async def test_notification_envelope_from_is_null_by_default() -> None:
    """Znuny ships ``SendmailNotificationEnvelopeFrom`` empty and its
    ``::FallbackToEmailFrom`` off, so automated mail carries the null envelope
    sender and its bounces never reach the queue mailbox."""

    async def _fetch(name: str) -> Any:
        return None

    sysconfig = SysConfig(fetch=_fetch)
    assert await sysconfig.notification_envelope_from("Tiqora <noreply@example.test>") == ""


@pytest.mark.asyncio
async def test_notification_envelope_from_honours_configured_address() -> None:
    async def _fetch(name: str) -> Any:
        if name == "SendmailNotificationEnvelopeFrom":
            return yaml_encode_effective("bounces@example.test")
        return None

    sysconfig = SysConfig(fetch=_fetch)
    assert (
        await sysconfig.notification_envelope_from("Tiqora <noreply@example.test>")
        == "bounces@example.test"
    )


@pytest.mark.asyncio
async def test_notification_envelope_from_falls_back_to_bare_from_address() -> None:
    """The fallback is an envelope address, so the display name must be stripped."""

    async def _fetch(name: str) -> Any:
        if name == "SendmailNotificationEnvelopeFrom::FallbackToEmailFrom":
            return yaml_encode_effective("1")
        return None

    sysconfig = SysConfig(fetch=_fetch)
    assert (
        await sysconfig.notification_envelope_from("Tiqora <noreply@example.test>")
        == "noreply@example.test"
    )


def test_generated_message_id_uses_the_sender_domain() -> None:
    """``socket.getfqdn()`` inside a container yields its id (``@7365fc6ef05a``),
    which changes on every restart and resolves nowhere."""
    assert message_id_domain("Netadmin <netadmin@stw-bonn.de>") == "stw-bonn.de"
    assert message_id_domain("bare@example.test") == "example.test"
    assert message_id_domain("not-an-address") is None


def test_auto_submitted_marks_machine_mail_without_bulk() -> None:
    """An invitation or an AI reply is machine-generated but not bulk: RFC 3834
    keeps vacation responders off it, while ``Precedence: bulk`` would only cost
    a password link its inbox placement."""
    msg = _message(loop_hint=False, auto_submitted="auto-generated")
    assert str(msg["Auto-Submitted"]) == "auto-generated"
    assert "Precedence" not in msg
    assert "X-OTRS-Loop" not in msg

    reply = _message(loop_hint=False, auto_submitted="auto-replied")
    assert str(reply["Auto-Submitted"]) == "auto-replied"


def test_loop_hint_wins_over_auto_submitted() -> None:
    """Notifications keep Znuny's own value; the header must not appear twice."""
    msg = _message(loop_hint=True, auto_submitted="auto-replied")
    assert msg.get_all("Auto-Submitted") == ["auto-generated"]
