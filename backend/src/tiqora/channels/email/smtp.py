"""Outbound SMTP for auto-responses and agent email replies.

Znuny sends via the configured ``SendmailModule`` (SMTP/SMTPS/Sendmail); Tiqora
uses ``aiosmtplib`` against either admin-DB settings (``tiqora_mail_outbound``)
or ``tiqora.config.Settings`` (``TIQORA_SMTP_*``). Wrapped behind a thin
protocol so tests can inject a capturing fake.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import TYPE_CHECKING, Literal, Protocol

import aiosmtplib
import structlog

if TYPE_CHECKING:
    from tiqora.config import Settings
    from tiqora.domain.mail_outbound import ResolvedOutboundSmtp

logger = structlog.get_logger(__name__)

MailSecurity = Literal["none", "starttls", "ssl"]


class MailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class SmtpMailSender:
    """Default sender: aiosmtplib against host/port/security/auth/timeout."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        security: MailSecurity | None = None,
        timeout: float | None = None,
    ) -> None:
        # Backward-compatible: ``SmtpMailSender(settings)`` still works.
        if settings is not None and host is None:
            host = settings.smtp_host
            port = settings.smtp_port
            username = settings.smtp_user or None
            password = settings.smtp_password or None
            security = "starttls" if settings.smtp_use_tls else "none"
            timeout = 60.0
        self._host = host or "localhost"
        self._port = int(port if port is not None else 25)
        self._username = username or None
        self._password = password or None
        self._security: MailSecurity = security or "none"
        self._timeout = float(timeout if timeout is not None else 60.0)
        # Populated after a successful ``send`` for communication-log detail.
        self.last_smtp_code: int | None = None
        self.last_smtp_detail: str | None = None

    @classmethod
    def from_resolved(cls, cfg: ResolvedOutboundSmtp) -> SmtpMailSender:
        user = cfg.auth_user if cfg.auth_type == "password" else None
        password = cfg.auth_password if cfg.auth_type == "password" else None
        return cls(
            host=cfg.host,
            port=cfg.port,
            username=user or None,
            password=password or None,
            security=cfg.security,
            timeout=float(cfg.timeout_seconds),
        )

    async def send(self, message: EmailMessage) -> None:
        use_tls = self._security == "ssl"
        start_tls = True if self._security == "starttls" else False if use_tls else None
        recipients, _message_id = await aiosmtplib.send(
            message,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            timeout=self._timeout,
            use_tls=use_tls,
            start_tls=start_tls,
        )
        # aiosmtplib returns dict[recipient, SMTPResponse]; pick first for log.
        self.last_smtp_code = None
        self.last_smtp_detail = None
        if recipients:
            parts: list[str] = []
            for addr, resp in recipients.items():
                code = getattr(resp, "code", None)
                msg = getattr(resp, "message", str(resp))
                if self.last_smtp_code is None and isinstance(code, int):
                    self.last_smtp_code = code
                parts.append(f"{addr}: {code} {msg}".strip())
            self.last_smtp_detail = "; ".join(parts) if parts else None


class CapturingMailSender:
    """Test double: records messages instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.last_smtp_code: int | None = 250
        self.last_smtp_detail: str | None = "250 OK (captured)"

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class FailingMailSender:
    """Test double: always raises (simulates SMTP outage)."""

    def __init__(self, message: str = "SMTP connection refused") -> None:
        self.message = message
        self.attempts = 0
        self.last_smtp_code: int | None = None
        self.last_smtp_detail: str | None = None

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
