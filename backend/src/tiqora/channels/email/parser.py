"""RFC822 parsing for the postmaster pipeline.

Pragmatic port of ``Kernel/System/EmailParser.pm``'s public surface (header
access, address splitting, references, body/attachment extraction) using the
Python standard library ``email`` package instead of ``Mail::Internet`` /
``MIME::Tools``. Charset decoding is best-effort (``errors="replace"``) so a
single malformed header/part never aborts the whole message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

# Simple HTML→text fallback, mirroring the intent of Znuny's
# ``PostmasterAutoHTML2Text`` (real HTML-to-text conversion is out of scope for
# Phase 4a — documented as an uncertainty).
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    content: bytes
    content_id: str | None = None
    disposition: str = "attachment"  # "attachment" | "inline"


@dataclass
class ParsedEmail:
    headers: dict[str, str] = field(default_factory=dict)
    subject: str = ""
    from_address: str = ""
    from_header: str = ""
    to_header: str = ""
    cc_header: str = ""
    reply_to_header: str = ""
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    body: str = ""
    content_type: str = "text/plain; charset=utf-8"
    attachments: list[ParsedAttachment] = field(default_factory=list)
    raw_size: int = 0

    def get_header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
    except (UnicodeDecodeError, ValueError):
        return raw
    out: list[str] = []
    for text_part, charset in parts:
        if isinstance(text_part, bytes):
            try:
                out.append(text_part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(text_part.decode("utf-8", errors="replace"))
        else:
            out.append(text_part)
    return "".join(out)


def get_email_address(value: str) -> str:
    """Extract the bare address from a ``From:``-style header value."""
    _, addr = parseaddr(value)
    return addr


def split_address_line(value: str) -> list[str]:
    """Split a comma-separated address header into individual address strings."""
    if not value:
        return []
    return [f"{name} <{addr}>" if name else addr for name, addr in getaddresses([value])]


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    )
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _decode_part_text(part: EmailMessage) -> str:
    charset = part.get_content_charset() or "utf-8"
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def parse_email(raw: bytes) -> ParsedEmail:
    """Parse a raw RFC822 message into a :class:`ParsedEmail`."""
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)  # noqa: S101 — policy.default guarantees this

    headers: dict[str, str] = {}
    for key in msg:
        headers[key.lower()] = _decode_header_value(str(msg.get(key, "")))

    subject = _decode_header_value(str(msg.get("Subject", "")))
    from_header = _decode_header_value(str(msg.get("From", "")))
    to_header = _decode_header_value(str(msg.get("To", "")))
    cc_header = _decode_header_value(str(msg.get("Cc", "")))
    reply_to_header = _decode_header_value(str(msg.get("Reply-To", "")))
    message_id = (str(msg.get("Message-ID", "")) or "").strip() or None
    in_reply_to = (str(msg.get("In-Reply-To", "")) or "").strip() or None

    references_raw = str(msg.get("References", "") or "")
    references = [r.strip("<>") for r in references_raw.split() if r.strip("<>")]
    # Znuny's FollowUpCheck::References also considers In-Reply-To as a reference.
    if in_reply_to and in_reply_to.strip("<>") not in references:
        references.append(in_reply_to.strip("<>"))

    plain_body: str | None = None
    html_body: str | None = None
    attachments: list[ParsedAttachment] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            disposition = (part.get_content_disposition() or "").lower()
            filename = part.get_filename()
            content_id = part.get("Content-ID")
            if content_id:
                content_id = content_id.strip("<>")

            if disposition in ("attachment", "inline") or filename:
                decoded = part.get_payload(decode=True)
                payload = decoded if isinstance(decoded, bytes) else b""
                attachments.append(
                    ParsedAttachment(
                        filename=filename or "unnamed",
                        content_type=content_type,
                        content=payload,
                        content_id=content_id,
                        disposition="inline" if disposition == "inline" else "attachment",
                    )
                )
                continue

            if content_type == "text/plain" and plain_body is None:
                plain_body = _decode_part_text(part)
            elif content_type == "text/html" and html_body is None:
                html_body = _decode_part_text(part)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/html":
            html_body = _decode_part_text(msg)
        else:
            plain_body = _decode_part_text(msg)

    if plain_body is not None:
        body = plain_body
        body_content_type = "text/plain; charset=utf-8"
    elif html_body is not None:
        # PostmasterAutoHTML2Text-equivalent fallback (Phase 4a: tag-strip only).
        body = _html_to_text(html_body)
        body_content_type = "text/plain; charset=utf-8"
    else:
        body = ""
        body_content_type = "text/plain; charset=utf-8"

    return ParsedEmail(
        headers=headers,
        subject=subject,
        from_address=get_email_address(from_header),
        from_header=from_header,
        to_header=to_header,
        cc_header=cc_header,
        reply_to_header=reply_to_header,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        body=body,
        content_type=body_content_type,
        attachments=attachments,
        raw_size=len(raw),
    )
