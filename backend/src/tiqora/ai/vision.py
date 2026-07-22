"""Vision pre-pass (image-attachment description) — see attachment-handling
architecture in ``docs/ai-integration.md``.

Image attachments are **never** shown to the main agent/summary model. Instead
this module sends each image, on its own, to a dedicated vision-capable
provider (``tiqora_llm_provider.supports_vision``, selected per-queue via
``tiqora_ai_queue_policy.vision_provider_id``) with a neutral description
prompt. The resulting plain-text description is what gets embedded into the
main model's context, exactly like a document attachment's extracted text.
The vision model never sees ticket text; the main model never sees image
bytes.

PII note: images cannot be masked the way text is (:mod:`tiqora.ai.pii`) —
the only control is *which* provider is allowed to see them. Operators should
prefer an ``eu_hosted`` provider for ``vision_provider_id`` and treat
enabling it per-queue as a deliberate decision, not a default.
"""

from __future__ import annotations

import base64
from typing import Any, Protocol

import structlog

from tiqora.ai.llm import LlmClient, LlmMessage, LlmUsage

logger = structlog.get_logger(__name__)

MAX_IMAGES_PER_RUN = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_DESCRIBE_PROMPT = (
    "Beschreibe präzise und sachlich, was auf dem Bild zu sehen ist, inkl. lesbarem Text."
)


class LlmClientFactory(Protocol):
    def __call__(self) -> LlmClient: ...


def _data_url(content_type: str, content: bytes) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


async def describe_images(
    images: list[tuple[str, str, bytes]],
    *,
    llm_factory: LlmClientFactory,
    usage_sink: list[LlmUsage] | None = None,
) -> list[tuple[str, str]]:
    """Describe up to :data:`MAX_IMAGES_PER_RUN` images (newest first, each
    capped at :data:`MAX_IMAGE_BYTES`) via a dedicated vision model.

    ``images`` is a list of ``(filename, content_type, content)``, newest
    first. Returns ``(filename, description)`` pairs in the same order —
    skipped/failed images get an empty description rather than raising, so a
    bad image never aborts the run.

    ``usage_sink``, if given, gets one :class:`~tiqora.ai.llm.LlmUsage`
    appended per successful call — chosen (over a separate
    ``tiqora_ai_usage`` row per vision call) as the simpler of the two
    documented options for surfacing vision-pass token usage: the caller
    sums these into the same run's usage record.
    """
    selected = images[:MAX_IMAGES_PER_RUN]
    results: list[tuple[str, str]] = []
    for filename, content_type, content in selected:
        if len(content) > MAX_IMAGE_BYTES:
            logger.warning(
                "ai_vision_image_skipped_too_large", filename=filename, size=len(content)
            )
            results.append((filename, ""))
            continue
        try:
            client = llm_factory()
            messages = [
                LlmMessage(
                    role="user",
                    content=_build_content(filename, content_type, content),
                )
            ]
            response = await client.chat(messages=messages, tools=None, max_tokens=512)
            description = (response.content or "").strip()
            if usage_sink is not None:
                usage_sink.append(response.usage)
        except Exception:  # noqa: BLE001 — a vision failure must never abort the run
            logger.warning("ai_vision_describe_failed", filename=filename, exc_info=True)
            description = ""
        results.append((filename, description))
    return results


def _build_content(filename: str, content_type: str, content: bytes) -> list[dict[str, Any]]:
    _ = filename
    return [
        {"type": "text", "text": _DESCRIBE_PROMPT},
        {"type": "image_url", "image_url": {"url": _data_url(content_type, content)}},
    ]


__all__ = ["MAX_IMAGES_PER_RUN", "MAX_IMAGE_BYTES", "describe_images"]
