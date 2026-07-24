"""Safe attachment delivery (stored-XSS hardening, security review H1/Low)."""

from __future__ import annotations

from tiqora.api.attachment_response import safe_attachment_response


def _disp(resp) -> str:
    return resp.headers["content-disposition"]


def test_html_attachment_is_neutralized_and_forced_download() -> None:
    r = safe_attachment_response(b"<script>alert(1)</script>", "text/html", "x.html", "inline")
    # Active type downgraded so the browser never renders it as HTML.
    assert r.media_type == "text/plain"
    assert _disp(r).startswith("attachment;")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_svg_is_neutralized() -> None:
    r = safe_attachment_response(b"<svg onload=alert(1)>", "image/svg+xml", "x.svg", "inline")
    assert r.media_type == "text/plain"
    assert _disp(r).startswith("attachment;")


def test_xml_family_neutralized() -> None:
    for ct in ("application/xml", "text/xml", "application/xhtml+xml", "application/foo+xml"):
        r = safe_attachment_response(b"<x/>", ct, "x", "inline")
        assert r.media_type == "text/plain", ct


def test_raster_image_may_render_inline() -> None:
    r = safe_attachment_response(b"\x89PNG", "image/png", "pic.png", "inline")
    assert r.media_type == "image/png"
    assert _disp(r).startswith("inline;")


def test_non_image_inline_is_forced_to_download() -> None:
    r = safe_attachment_response(b"%PDF", "application/pdf", "doc.pdf", "inline")
    assert _disp(r).startswith("attachment;")


def test_force_download_wins_over_inline() -> None:
    r = safe_attachment_response(b"\x89PNG", "image/png", "pic.png", "inline", force_download=True)
    assert _disp(r).startswith("attachment;")


def test_filename_quotes_and_crlf_are_stripped() -> None:
    r = safe_attachment_response(b"x", "application/octet-stream", 'a".txt\r\nX: y', "attachment")
    disp = _disp(r)
    # No raw quote/CR/LF can break out of the quoted parameter.
    assert 'filename="a.txtX: y"' in disp
    assert "\r" not in disp and "\n" not in disp
