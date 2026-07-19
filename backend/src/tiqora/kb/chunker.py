"""Heading-aware Markdown chunker for KB articles.

Splits ``content_md`` into chunks at H2 (``## ``)/H3 (``### ``) boundaries,
targeting ~500 tokens per chunk (approximated as ``len(text) // 4``, the same
crude heuristic used elsewhere in the codebase for cheap token estimation —
no tokenizer dependency). Sections that exceed the target are further split
on blank-line paragraph boundaries while keeping the same ``heading_path``/
``anchor`` so all sub-chunks of one heading remain citable to the same anchor.

Pure functions, no I/O — safe to unit test without a DB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_TOKENS = 500
_CHARS_PER_TOKEN = 4
TARGET_CHARS = TARGET_TOKENS * _CHARS_PER_TOKEN

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def estimate_tokens(text: str) -> int:
    """Crude token estimate: ~4 characters per token."""
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def slugify(text: str) -> str:
    """Lowercase, spaces/punctuation -> hyphens, strip leading/trailing hyphens."""
    slug = _SLUG_STRIP_RE.sub("-", text.strip().lower()).strip("-")
    return slug or "section"


@dataclass(frozen=True, slots=True)
class Chunk:
    seq: int
    heading_path: str
    anchor: str
    content_md: str
    token_count: int


@dataclass(frozen=True, slots=True)
class _RawSection:
    level: int  # 0 = preamble (no heading), 1 = H1, 2 = H2, 3 = H3
    heading_text: str | None
    lines: list[str]


def _split_sections(content_md: str, article_title: str) -> tuple[str, list[_RawSection]]:
    """Split raw Markdown into (h1_title, sections) on H1/H2/H3 boundaries."""
    lines = content_md.splitlines()
    h1_title = article_title
    sections: list[_RawSection] = []
    current: _RawSection | None = None

    for line in lines:
        m = _HEADING_RE.match(line)
        if m is not None and len(m.group(1)) <= 3:
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            if level == 1:
                # A leading H1 line supplies the article title breadcrumb
                # root instead of the passed-in article_title, but does not
                # start its own chunk section.
                h1_title = heading_text
                continue
            if current is not None:
                sections.append(current)
            current = _RawSection(level=level, heading_text=heading_text, lines=[line])
            continue
        if current is None:
            current = _RawSection(level=0, heading_text=None, lines=[])
        current.lines.append(line)

    if current is not None:
        sections.append(current)

    return h1_title, sections


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank-line boundaries, dropping empty fragments."""
    parts = re.split(r"\n\s*\n", text)
    return [p for p in (p.strip() for p in parts) if p]


def _pack_paragraphs(paragraphs: list[str]) -> list[str]:
    """Greedily pack paragraphs into ~TARGET_CHARS-sized groups, in order."""
    groups: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        para_len = len(para)
        if buf and buf_len + para_len > TARGET_CHARS:
            groups.append("\n\n".join(buf))
            buf = []
            buf_len = 0
        buf.append(para)
        buf_len += para_len + 2
    if buf:
        groups.append("\n\n".join(buf))
    return groups or [""]


def chunk_article(content_md: str, *, article_title: str) -> list[Chunk]:
    """Chunk a Markdown article into heading-scoped, size-bounded pieces.

    ``heading_path`` is a breadcrumb ``"H1 > H2 > H3"`` where H1 is either a
    leading ``# `` line in ``content_md`` or *article_title* if no such line
    is present. ``anchor`` is a slug of the section heading, unique within
    the article (``-2``, ``-3``, … appended on collision). Preamble content
    (before the first ``## ``/``### `` heading) is emitted as its own chunk
    with ``heading_path`` equal to just the H1 title.
    """
    h1_title, sections = _split_sections(content_md, article_title)

    chunks: list[Chunk] = []
    seq = 0
    seen_anchors: dict[str, int] = {}
    h2_current: str | None = None

    for section in sections:
        if section.level == 2:
            h2_current = section.heading_text
            path_parts = [h1_title, h2_current] if h2_current else [h1_title]
        elif section.level == 3:
            h3 = section.heading_text or ""
            path_parts = [h1_title, h2_current, h3] if h2_current else [h1_title, h3]
        else:
            path_parts = [h1_title]
        heading_path = " > ".join(p for p in path_parts if p)

        anchor_base = slugify(section.heading_text) if section.heading_text else "top"
        count = seen_anchors.get(anchor_base, 0)
        seen_anchors[anchor_base] = count + 1
        anchor = anchor_base if count == 0 else f"{anchor_base}-{count + 1}"

        body = "\n".join(section.lines).strip("\n")
        if not body.strip():
            continue

        pieces = [body] if len(body) <= TARGET_CHARS else _pack_paragraphs(_split_paragraphs(body))

        for piece in pieces:
            if not piece:
                continue
            chunks.append(
                Chunk(
                    seq=seq,
                    heading_path=heading_path,
                    anchor=anchor,
                    content_md=piece,
                    token_count=estimate_tokens(piece),
                )
            )
            seq += 1

    return chunks
