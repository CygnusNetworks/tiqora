"""Unit tests for the Znuny body-part / inline pseudo-attachment filters."""

from __future__ import annotations

import pytest

from tiqora.domain.ticket_service import _is_body_part_attachment, _is_inline_attachment
from tiqora.storage.backend import AttachmentMeta


def _meta(
    *,
    filename: str | None = None,
    content_type: str | None = None,
    content_id: str | None = None,
    content_alternative: str | None = None,
    disposition: str | None = None,
) -> AttachmentMeta:
    return AttachmentMeta(
        id=1,
        article_id=1,
        filename=filename,
        content_type=content_type,
        content_size="100",
        content_id=content_id,
        content_alternative=content_alternative,
        disposition=disposition,
    )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("file-1", "text/plain; charset=utf-8"),
        # HTML-only mails (no multipart/alternative) store the body as
        # ``file-1`` with text/html — regression for ticket 2026011310000031.
        ("file-1", "text/html; charset=utf-8"),
        ("file-2", "text/html; charset=utf-8"),
        ("file-1.html", "text/html"),
        ("File-1", "TEXT/HTML"),
    ],
)
def test_body_part_by_filename(filename: str, content_type: str) -> None:
    assert _is_body_part_attachment(_meta(filename=filename, content_type=content_type))


def test_content_alternative_flag_is_body_part() -> None:
    assert _is_body_part_attachment(
        _meta(filename="anything.html", content_type="text/html", content_alternative="1")
    )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("report.pdf", "application/pdf"),
        ("file-1", "application/octet-stream"),
        ("file-10", "text/html"),
        ("notes.txt", "text/plain"),
    ],
)
def test_real_attachments_kept(filename: str, content_type: str) -> None:
    assert not _is_body_part_attachment(_meta(filename=filename, content_type=content_type))


def test_inline_detection() -> None:
    assert _is_inline_attachment(
        _meta(filename="logo.png", content_type="image/png", content_id="<cid123>")
    )
    assert _is_inline_attachment(
        _meta(filename="logo.png", content_type="image/png", disposition="inline")
    )
    assert not _is_inline_attachment(
        _meta(filename="scan.pdf", content_type="application/pdf", disposition="attachment")
    )


def test_inline_requires_image_content_type() -> None:
    """A Content-ID or ``inline`` disposition alone doesn't make it inline —
    only image parts are folded into the collapsed "inline images" section.
    Non-image documents (PDFs, Office files) stay regular attachments even
    when the mail client tagged them with a Content-ID or inline
    disposition (regression: a 71KB PDF with a Content-ID was hidden)."""
    assert not _is_inline_attachment(
        _meta(filename="report.pdf", content_type="application/pdf", content_id="<cid456>")
    )
    assert _is_inline_attachment(
        _meta(filename="logo.png", content_type="image/png; name=logo.png", content_id="<cid789>")
    )
    assert _is_inline_attachment(
        _meta(filename="logo.jpg", content_type="IMAGE/JPEG", disposition="inline")
    )
    assert not _is_inline_attachment(
        _meta(filename="report.pdf", content_type="application/pdf", disposition="inline")
    )
