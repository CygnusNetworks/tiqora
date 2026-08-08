"""Unit tests for HTML article sanitiser and cid rewrite."""

from __future__ import annotations

from tiqora.domain.article_html import (
    mark_external_images,
    render_article_body,
    rewrite_cid_urls,
    sanitize_html,
)


def test_sanitize_removes_script() -> None:
    raw = "<p>Hello</p><script>alert(1)</script><img src=x onerror=alert(1)>"
    cleaned = sanitize_html(raw)
    assert "script" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "Hello" in cleaned


def test_rewrite_cid_urls() -> None:
    html = '<img src="cid:part1@mail" alt="x"><a href="cid:nope">n</a>'
    out = rewrite_cid_urls(html, ticket_id=10, article_id=20)
    assert 'src="/api/v1/tickets/10/articles/20/attachments/by-cid/part1%40mail"' in out
    # href cid not rewritten by rewrite_cid_urls (src only); ok for now
    assert "cid:part1@mail" not in out


def test_mark_external_images() -> None:
    html = '<img src="https://tracker.example/pixel.gif" alt="t">'
    out = mark_external_images(html)
    assert 'data-external-src="https://tracker.example/pixel.gif"' in out
    assert 'src=""' in out or "src=''" in out
    assert "https://tracker.example/pixel.gif" in out


def test_render_html_full_pipeline() -> None:
    raw = (
        "<p>Hi</p>"
        "<script>evil()</script>"
        '<img src="cid:inline1">'
        '<img src="https://cdn.example/x.png">'
    )
    rendered = render_article_body(
        body=raw,
        content_type="text/html; charset=utf-8",
        ticket_id=1,
        article_id=2,
    )
    assert rendered.is_html
    assert rendered.content_type == "text/html"
    assert "script" not in rendered.body.lower()
    assert "evil" not in rendered.body
    assert "/api/v1/tickets/1/articles/2/attachments/by-cid/inline1" in rendered.body
    assert "data-external-src" in rendered.body


def test_external_image_gate_covers_bypass_forms() -> None:
    # Protocol-relative and unquoted external image sources must also be gated
    # (moved to run after nh3 normalisation) — otherwise a customer-emailed
    # tracking pixel auto-loads in the agent's browser (security review).
    for raw in (
        '<img src="//tracker.example/p.gif">',  # protocol-relative
        "<img src=http://tracker.example/p.gif>",  # unquoted
        '<img src="https://tracker.example/p.gif">',  # baseline
    ):
        rendered = render_article_body(
            body=raw,
            content_type="text/html",
            ticket_id=1,
            article_id=2,
        )
        assert "data-external-src" in rendered.body, raw
        assert "tracker.example" in rendered.body
        # The live src must be neutralised until the user opts in.
        assert 'src=""' in rendered.body or "src=''" in rendered.body, raw


def test_render_plain_escaped() -> None:
    rendered = render_article_body(
        body='<b>not html</b> & "x"',
        content_type="text/plain",
        ticket_id=1,
        article_id=1,
    )
    assert not rendered.is_html
    assert "&lt;b&gt;" in rendered.body
    assert "&amp;" in rendered.body
