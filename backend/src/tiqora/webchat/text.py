"""Plain-text rules for chat messages and visitor-entered values (pure)."""

from __future__ import annotations

import re
from typing import Final, Literal
from urllib.parse import urlsplit

from tiqora.domain.quoting import html_to_plaintext

MAX_MESSAGE_LENGTH: Final = 2000
MAX_AGENT_MESSAGE_LENGTH: Final = 4000

#: Characters dropped from every visitor-controlled string (messages, filenames, names):
#: C0 controls, DEL, C1 controls, bidi marks/overrides/isolates, zero-width space, word joiner,
#: BOM, line/paragraph separators and lone surrogates (a JSON surrogate escape would make the
#: DB write fail). Built from code points so the source holds no invisible characters.
_DROP_RANGES: Final = (
    (0x00, 0x1F),
    (0x7F, 0x9F),
    (0x061C, 0x061C),
    (0x200B, 0x200B),
    (0x200E, 0x200F),
    (0x2028, 0x202E),
    (0x2060, 0x2060),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
    (0xD800, 0xDFFF),
)


def _class(ranges: tuple[tuple[int, int], ...]) -> re.Pattern[str]:
    body = "".join(f"{re.escape(chr(lo))}-{re.escape(chr(hi))}" for lo, hi in ranges)
    return re.compile(f"[{body}]")


CONTROL_CHARS: Final = _class(_DROP_RANGES)
#: Message bodies keep \n and \t (callers normalise CR/CRLF to LF first).
_BODY_DROP: Final = _class(((0x00, 0x08), (0x0B, 0x1F)) + _DROP_RANGES[1:])


class MessageRejected(ValueError):
    def __init__(self, code: Literal["empty", "too_long"]) -> None:
        super().__init__(code)
        self.code = code


def sanitize_message(raw: str, *, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    """CRLF/CR → LF, drop control/bidi characters, strip; empty/too long → ``MessageRejected``."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _BODY_DROP.sub("", text).strip()
    if not text:
        raise MessageRejected("empty")
    if len(text) > max_length:
        raise MessageRejected("too_long")
    return text


def chat_body_from_article(body: str, content_type: str) -> str:
    if "html" in (content_type or "").lower():
        body = html_to_plaintext(body)
    return sanitize_message(body, max_length=MAX_AGENT_MESSAGE_LENGTH)


# --- visitor-entered addresses and URLs (R-INPUT) ----------------------------------------------

EMAIL_RE: Final = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$"
)
MAX_EMAIL_LENGTH: Final = 250
MAX_PAGE_URL_LENGTH: Final = 500


def normalize_email(raw: str | None) -> str | None:
    """Strict dot-atom address with IDNA domain, lower-cased domain; ``None`` when invalid."""
    value = (raw or "").strip()
    if not value or len(value) > MAX_EMAIL_LENGTH or value.count("@") != 1:
        return None
    local, _, domain = value.partition("@")
    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    out = f"{local}@{ascii_domain}"
    if len(out) > MAX_EMAIL_LENGTH or EMAIL_RE.fullmatch(out) is None:
        return None
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    if any(
        label.startswith("-") or label.endswith("-") or len(label) > 63
        for label in ascii_domain.split(".")
    ):
        return None
    return out


def quoted_display_name(name: str | None) -> str:
    """Display name safe inside ``"..." <addr>``: no quotes, backslash, angle brackets, controls."""
    cleaned = CONTROL_CHARS.sub(" ", name or "")
    cleaned = re.sub(r'["\\<>]', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:100]
    return cleaned


def clean_page_url(raw: str | None) -> str | None:
    """Keep ``page_url`` only when it is an http(s) URL with a host, ≤ 500 chars, no controls."""
    value = (raw or "").strip()
    if not value or len(value) > MAX_PAGE_URL_LENGTH or CONTROL_CHARS.search(value):
        return None
    if any(c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return value
