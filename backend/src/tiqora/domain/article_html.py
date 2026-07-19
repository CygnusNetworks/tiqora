"""Server-side article body rendering: HTML sanitisation and cid rewrite."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

import nh3

# Allowlisted tags for HTML mail bodies (scripts/event handlers never allowed).
_ALLOWED_TAGS: set[str] = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "col",
    "colgroup",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "id", "title", "dir", "lang"},
    # "rel" managed by nh3 link_rel= (do not list when link_rel is set)
    "a": {"href", "name", "target"},
    "img": {
        "src",
        "alt",
        "width",
        "height",
        "data-external-src",
        "data-cid",
    },
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "col": {"span", "width"},
    "colgroup": {"span"},
}

_CID_SRC_RE = re.compile(
    r"""(?P<prefix>\bsrc\s*=\s*["'])cid:(?P<cid>[^"']+)(?P<suffix>["'])""",
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(
    r"""<img\b([^>]*?)\bsrc\s*=\s*(?P<q>["'])(?P<src>https?://[^"']+)(?P=q)([^>]*)>""",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class RenderedArticleBody:
    """Sanitised article body ready for a sandboxed iframe or plain display."""

    content_type: str  # "text/html" | "text/plain"
    body: str
    is_html: bool


def _attachment_url(ticket_id: int, article_id: int, content_id: str) -> str:
    """Build relative API path for cid content-id lookup."""
    cid = quote(content_id.strip("<>"), safe="")
    return f"/api/v1/tickets/{ticket_id}/articles/{article_id}/attachments/by-cid/{cid}"


def rewrite_cid_urls(html_body: str, ticket_id: int, article_id: int) -> str:
    """Rewrite ``cid:…`` src attributes to the attachment-by-cid endpoint."""

    def repl(match: re.Match[str]) -> str:
        cid = match.group("cid")
        url = _attachment_url(ticket_id, article_id, cid)
        return f"{match.group('prefix')}{url}{match.group('suffix')}"

    return _CID_SRC_RE.sub(repl, html_body)


def mark_external_images(html_body: str) -> str:
    """Move external http(s) img src to data-external-src; leave empty src.

    Frontends can offer click-to-load without auto-fetching remote trackers.
    """

    def repl(match: re.Match[str]) -> str:
        before = match.group(1) or ""
        src = match.group("src")
        q = match.group("q")
        after = match.group(4) or ""
        return f"<img{before} src={q}{q} data-external-src={q}{src}{q}{after}>"

    return _IMG_SRC_RE.sub(repl, html_body)


def sanitize_html(raw_html: str) -> str:
    """Strip scripts, event handlers, and disallowed tags/attributes via nh3."""
    cleaned = nh3.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto", "cid"},
    )
    return cleaned


def render_article_body(
    *,
    body: str | None,
    content_type: str | None,
    ticket_id: int,
    article_id: int,
) -> RenderedArticleBody:
    """Return a safe body for display.

    * ``text/html`` → sanitise, rewrite cid:, mark external images
    * otherwise → HTML-escape as plain text
    """
    text = body or ""
    ct = (content_type or "text/plain").split(";", 1)[0].strip().lower()
    if ct in {"text/html", "application/xhtml+xml"}:
        rewritten = rewrite_cid_urls(text, ticket_id, article_id)
        marked = mark_external_images(rewritten)
        safe = sanitize_html(marked)
        return RenderedArticleBody(content_type="text/html", body=safe, is_html=True)
    return RenderedArticleBody(
        content_type="text/plain",
        body=html.escape(text),
        is_html=False,
    )
