"""Wires document text extraction (:mod:`tiqora.ai.attachments`) and the
vision pre-pass (:mod:`tiqora.ai.vision`) into a per-article render block,
shared by :mod:`tiqora.ai.runtime` and :mod:`tiqora.ai.summary`.

Ordering: document text and image descriptions are collected per attachment,
then the combined per-run character budget
(:data:`tiqora.ai.attachments.DEFAULT_RUN_BUDGET_CHARS`) is applied in
chronological article order (oldest first) — matching how article bodies
themselves are rendered. The vision pass itself picks its (at most
:data:`tiqora.ai.vision.MAX_IMAGES_PER_RUN`) images newest-first, since a
handful of the most recent images are far more likely to be relevant than an
old one buried early in the ticket.

The vision pass runs once per agent/summary run (not once per attachment
"use") — callers invoke :func:`build_attachment_context` exactly once before
building the LLM prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.attachments import apply_budget, extract_attachment_text, is_image
from tiqora.ai.context import ArticleSnapshot, load_attachment_content
from tiqora.ai.llm import LlmClient, LlmUsage
from tiqora.ai.vision import describe_images

# Sync on purpose: the caller already resolved the vision provider row (a DB
# fetch) before calling build_attachment_context, so constructing the actual
# LlmClient here needs no further I/O.
VisionLlmFactory = Callable[[], LlmClient]

# Real (non-inline) image attachments below this size are almost always
# tracking pixels / mini icons rather than substantive content — skip them
# before spending a vision call. Inline images (signature logos etc.) are
# already filtered upstream in tiqora.ai.context regardless of size.
_MIN_IMAGE_BYTES = 5 * 1024


@dataclass(frozen=True, slots=True)
class AttachmentContextResult:
    blocks: dict[int, str] = field(default_factory=dict)
    # Vision-pass token usage, summed across all described images — the
    # caller adds this to the same run's tiqora_ai_usage record (the
    # documented "simpler" of the two options, see tiqora.ai.vision).
    vision_usage: LlmUsage = field(default_factory=LlmUsage)


async def build_attachment_context(
    session: AsyncSession,
    articles: list[ArticleSnapshot],
    *,
    vision_enabled: bool,
    vision_llm_factory: VisionLlmFactory | None = None,
) -> AttachmentContextResult:
    """Returns per-article rendered attachment blocks + aggregate vision
    usage. ``result.blocks`` maps ``{article_id: rendered_attachment_block}``
    for every article that has at least one usable attachment — the caller
    appends the block after the article body, before PII masking (so
    extracted/described text is masked exactly like the rest of the body).

    ``vision_enabled`` gates the image path entirely — when ``False`` (no
    ``vision_provider_id`` configured, or the configured provider is
    invalid), images are skipped without ever being loaded from the DB.
    """
    doc_blocks: dict[int, tuple[str, str]] = {}
    # attachment_id, filename, content_type, content
    image_candidates: list[tuple[int, str, str, bytes]] = []

    for article in articles:
        for att in article.attachments:
            if is_image(att.content_type, att.filename):
                if not vision_enabled or att.size < _MIN_IMAGE_BYTES:
                    continue
                content = await load_attachment_content(session, att.id)
                if content:
                    fallback_name = f"attachment-{att.id}"
                    image_candidates.append(
                        (att.id, att.filename or fallback_name, att.content_type or "", content)
                    )
                continue
            content = await load_attachment_content(session, att.id)
            if content is None:
                continue
            text = extract_attachment_text(att.filename, att.content_type, content)
            if text:
                doc_blocks[att.id] = (att.filename or f"Anhang {att.id}", text)

    image_blocks: dict[int, tuple[str, str]] = {}
    usage_sink: list[LlmUsage] = []
    if image_candidates and vision_llm_factory is not None:
        newest_first = list(reversed(image_candidates))
        described = await describe_images(
            [(fn, ct, content) for (_aid, fn, ct, content) in newest_first],
            llm_factory=vision_llm_factory,
            usage_sink=usage_sink,
        )
        for (attachment_id, filename, _ct, _content), (_fn, description) in zip(
            newest_first, described, strict=True
        ):
            if description:
                image_blocks[attachment_id] = (filename, description)

    ordered_pairs: list[tuple[int, str, str]] = []
    for article in articles:
        for att in article.attachments:
            if att.id in doc_blocks:
                filename, text = doc_blocks[att.id]
                ordered_pairs.append((article.id, f"[Anhang: {filename}]", text))
            elif att.id in image_blocks:
                filename, description = image_blocks[att.id]
                label = f"[Bild-Anhang: {filename} — Beschreibung durch Vision-Modell]"
                ordered_pairs.append((article.id, label, description))

    budgeted = apply_budget([(label, text) for _aid, label, text in ordered_pairs])

    per_article: dict[int, list[str]] = {}
    for (article_id, label, _text), (_label, budgeted_text) in zip(
        ordered_pairs, budgeted, strict=True
    ):
        per_article.setdefault(article_id, []).append(f"{label}\n{budgeted_text}")

    blocks = {article_id: "\n\n".join(parts) for article_id, parts in per_article.items()}
    vision_usage = LlmUsage(
        prompt_tokens=sum(u.prompt_tokens for u in usage_sink),
        completion_tokens=sum(u.completion_tokens for u in usage_sink),
    )
    return AttachmentContextResult(blocks=blocks, vision_usage=vision_usage)


__all__ = ["AttachmentContextResult", "VisionLlmFactory", "build_attachment_context"]
