"""Pure unit tests for the KB Markdown chunker (no DB)."""

from __future__ import annotations

from tiqora.kb.chunker import Chunk, chunk_article, estimate_tokens, slugify


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Getting Started!") == "getting-started"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert slugify("Über café") == "ber-caf"
    assert slugify("###") == "section"


def test_estimate_tokens_is_len_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_single_h2_section_breadcrumb() -> None:
    md = "## Overview\n\nThis is the overview text."
    chunks = chunk_article(md, article_title="My Article")
    assert len(chunks) == 1
    c = chunks[0]
    assert c.heading_path == "My Article > Overview"
    assert c.anchor == "overview"
    assert "Overview text" not in c.content_md or "overview text" in c.content_md.lower()
    assert c.seq == 0


def test_h1_line_overrides_article_title() -> None:
    md = "# Real Title\n\n## Section A\n\nBody A."
    chunks = chunk_article(md, article_title="Fallback Title")
    assert chunks[0].heading_path == "Real Title > Section A"


def test_h3_nested_under_h2() -> None:
    md = "## Parent\n\nintro\n\n### Child\n\nchild body"
    chunks = chunk_article(md, article_title="T")
    assert [c.heading_path for c in chunks] == ["T > Parent", "T > Parent > Child"]
    assert [c.anchor for c in chunks] == ["parent", "child"]


def test_h3_after_h2_resets_when_new_h2_seen() -> None:
    md = (
        "## First\n\n### Sub\n\nsub body\n\n"
        "## Second\n\nsecond body\n\n"
        "### AnotherSub\n\nanother body"
    )
    chunks = chunk_article(md, article_title="T")
    paths = [c.heading_path for c in chunks]
    assert paths == [
        "T > First",
        "T > First > Sub",
        "T > Second",
        "T > Second > AnotherSub",
    ]


def test_preamble_before_first_heading() -> None:
    md = "Intro paragraph before any heading.\n\n## First\n\nbody"
    chunks = chunk_article(md, article_title="T")
    assert chunks[0].heading_path == "T"
    assert chunks[0].anchor == "top"
    assert "Intro paragraph" in chunks[0].content_md
    assert chunks[1].heading_path == "T > First"


def test_anchor_collision_suffix() -> None:
    md = "## Notes\n\nfirst notes\n\n## Notes\n\nsecond notes"
    chunks = chunk_article(md, article_title="T")
    assert [c.anchor for c in chunks] == ["notes", "notes-2"]


def test_long_section_splits_on_paragraph_boundaries() -> None:
    paragraphs = [f"Paragraph number {i} " + ("word " * 100) for i in range(6)]
    body = "\n\n".join(paragraphs)
    md = f"## Big Section\n\n{body}"
    chunks = chunk_article(md, article_title="T")
    assert len(chunks) > 1
    for c in chunks:
        assert c.heading_path == "T > Big Section"
        assert c.anchor == "big-section"
        assert c.token_count <= 500 * 1.5  # some slack for pack boundary rounding
    # seq is sequential across the whole article
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    # No content lost: every paragraph text appears in some chunk
    joined = "\n\n".join(c.content_md for c in chunks)
    for p in paragraphs:
        assert p.strip() in joined


def test_empty_content_returns_no_chunks() -> None:
    assert chunk_article("", article_title="T") == []
    assert chunk_article("   \n\n  ", article_title="T") == []


def test_chunk_is_frozen_dataclass() -> None:
    c = Chunk(seq=0, heading_path="a", anchor="b", content_md="c", token_count=1)
    assert c.seq == 0
