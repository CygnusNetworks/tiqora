"""Upload policy: content sniffing, filename normalisation, size rules (pure)."""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

from tiqora.webchat.models import UploadPolicy
from tiqora.webchat.text import CONTROL_CHARS

#: Multipart framing headroom over the policy size for the Content-Length gate (R-UPLOAD).
UPLOAD_ENVELOPE_SLACK: Final = 64 * 1024
#: Cap of every other public JSON body.
JSON_BODY_MAX: Final = 64 * 1024
MAX_FILENAME_CHARS: Final = 120
MAX_FILENAME_BYTES: Final = 240

#: Types a browser can safely draw in an <img>; SVG is never accepted at all (R-XSS).
RASTER_IMAGE_TYPES: Final = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_EXT: Final = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}
_TEXT_EXTS: Final = frozenset({".txt", ".log", ".csv"})
_FS_UNSAFE: Final = re.compile(r'[<>:"|?*]')
_WS: Final = re.compile(r"\s+")
_LINE_WS: Final = re.compile(r"[\t\n\r\x0b\x0c]")


class UploadRejected(ValueError):
    def __init__(self, code: Literal["disabled", "empty", "size", "type"]) -> None:
        super().__init__(code)
        self.code = code


def is_raster_image(content_type: str) -> bool:
    """True only for types that may be rendered as a thumbnail."""
    return content_type in RASTER_IMAGE_TYPES


def _extension(filename: str) -> str:
    base = re.split(r"[/\\]", filename or "")[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def sniff_upload_type(content: bytes, filename: str) -> str | None:
    """Content type from the bytes themselves; the declared type and the name never decide."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if _extension(filename) in _TEXT_EXTS and b"\x00" not in content:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/plain"
    return None


def safe_upload_filename(name: str, content_type: str) -> str:
    """Safe stored name: basename, no controls/bidi, canonical extension, bounded length."""
    base = re.split(r"[/\\]", name or "")[-1]
    base = unicodedata.normalize(
        "NFC", _WS.sub(" ", CONTROL_CHARS.sub("", _LINE_WS.sub(" ", base)))
    )
    base = _FS_UNSAFE.sub("_", base).strip()
    dot = base.rfind(".")
    stem = base[:dot] if dot > 0 else base
    stem = stem.lstrip(". ").rstrip(". ")
    ext = _EXT.get(content_type, "")
    stem = stem or "datei"
    stem = stem[: MAX_FILENAME_CHARS - len(ext)]
    while len((stem + ext).encode("utf-8")) > MAX_FILENAME_BYTES:
        stem = stem[:-1]
    stem = stem.rstrip(". ") or "datei"
    return stem + ext


def check_upload(content: bytes, filename: str, policy: UploadPolicy) -> tuple[str, str]:
    """Return ``(safe_name, content_type)`` or raise ``UploadRejected``."""
    if not policy.enabled:
        raise UploadRejected("disabled")
    if not content:
        raise UploadRejected("empty")
    if len(content) > policy.max_bytes:
        raise UploadRejected("size")
    ctype = sniff_upload_type(content, filename)
    if ctype is None or ctype not in policy.types:
        raise UploadRejected("type")
    return safe_upload_filename(filename, ctype), ctype


def content_length_within(raw: str | None, limit: int, *, minimum: int = 1) -> bool:
    """Strict Content-Length gate: ASCII digits only, minimum <= value <= limit."""
    if raw is None:
        return False
    value = raw.strip()
    if not value or not value.isascii() or not value.isdigit() or len(value) > 12:
        return False
    return minimum <= int(value) <= limit
