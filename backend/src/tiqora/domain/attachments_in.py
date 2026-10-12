"""Base64 attachments submitted in JSON bodies (reply dialog, phone calls, process dialogs).

Lives in the domain layer so engine code (``tiqora.process``) can decode uploads without
importing from ``tiqora.api``. The errors are domain errors; the routes map
:class:`AttachmentTooLarge` to 413 and :class:`AttachmentInvalid` to 422 (rulings F33).
"""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, Field

#: Largest single attachment (and largest decoded total of one request) accepted from an
#: agent or a portal customer. ``tiqora.api.uploads`` re-exports it.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

__all__ = [
    "MAX_ATTACHMENT_BYTES",
    "ArticleAttachmentIn",
    "AttachmentInvalid",
    "AttachmentTooLarge",
    "decode_attachments",
]


class ArticleAttachmentIn(BaseModel):
    filename: str = Field(min_length=1, max_length=250)
    content_type: str = "application/octet-stream"
    content_base64: str


class AttachmentTooLarge(Exception):
    """The decoded attachments of one request exceed the upload limit (HTTP 413)."""


class AttachmentInvalid(Exception):
    """An attachment is not valid base64, or the request is malformed (HTTP 422)."""


def decode_attachments(
    items: list[ArticleAttachmentIn], *, max_bytes: int = MAX_ATTACHMENT_BYTES
) -> list[tuple[str, str, bytes]]:
    """Base64-decode request attachments as ``(filename, content_type, bytes)``.

    Raises :class:`AttachmentInvalid` on bad data and :class:`AttachmentTooLarge` when the
    decoded total exceeds *max_bytes*."""
    out: list[tuple[str, str, bytes]] = []
    total = 0
    for item in items:
        try:
            content = base64.b64decode(item.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentInvalid(f"attachment {item.filename!r} is not valid base64") from None
        total += len(content)
        if total > max_bytes:
            raise AttachmentTooLarge("attachments exceed the upload limit")
        out.append((item.filename, item.content_type, content))
    return out
