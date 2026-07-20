"""Outbound SMTP for auto-responses and agent email replies.

Znuny sends via the configured ``SendmailModule`` (SMTP/SMTPS/Sendmail); Tiqora
uses ``aiosmtplib`` directly against ``tiqora.config.Settings`` (``TIQORA_SMTP_*``).
Wrapped behind a thin protocol so tests can inject a capturing fake.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Protocol

import aiosmtplib
import structlog

from tiqora.config import Settings

logger = structlog.get_logger(__name__)


class MailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class SmtpMailSender:
    """Default sender: aiosmtplib against ``Settings.smtp_*``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, message: EmailMessage) -> None:
        await aiosmtplib.send(
            message,
            hostname=self._settings.smtp_host,
            port=self._settings.smtp_port,
            username=self._settings.smtp_user or None,
            password=self._settings.smtp_password or None,
            start_tls=self._settings.smtp_use_tls,
        )


class CapturingMailSender:
    """Test double: records messages instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class FailingMailSender:
    """Test double: always raises (simulates SMTP outage)."""

    def __init__(self, message: str = "SMTP connection refused") -> None:
        self.message = message
        self.attempts = 0

    async def send(self, message: EmailMessage) -> None:
        self.attempts += 1
        raise OSError(self.message)


def build_message(
    *,
    from_addr: str,
    to_addrs: str,
    cc_addrs: str | None,
    subject: str,
    body: str,
    content_type: str,
    in_reply_to: str | None,
    bcc_addrs: str | None = None,
    reply_to: str | None = None,
    references: str | None = None,
    message_id: str | None = None,
    loop_hint: bool = True,
) -> EmailMessage:
    """Build an :class:`EmailMessage` for SMTP delivery.

    ``loop_hint=True`` sets ``X-OTRS-Loop: yes`` (auto-responses / notifications).
    Agent replies pass ``loop_hint=False`` so recipients' ticket systems do not
    treat the message as automated mail.
    """
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addrs
    if cc_addrs:
        msg["Cc"] = cc_addrs
    if bcc_addrs:
        msg["Bcc"] = bcc_addrs
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if message_id:
        mid = message_id if message_id.startswith("<") else f"<{message_id}>"
        msg["Message-ID"] = mid
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    refs = references or in_reply_to
    if refs:
        msg["References"] = refs
    if loop_hint:
        # X-OTRS-Loop marks automated messages (Znuny's own loop hint;
        # SendAutoResponse also checks the *inbound* email for this header
        # before replying — see channels/email/autoresponse.py).
        msg["X-OTRS-Loop"] = "yes"
    subtype = "html" if "html" in content_type.lower() else "plain"
    msg.set_content(body, subtype=subtype, charset="utf-8")
    return msg
