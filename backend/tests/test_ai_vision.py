"""Unit tests for tiqora.ai.vision (image-description pre-pass). No real
vision endpoint is ever called — a scripted FakeLlm stands in."""

from __future__ import annotations

from typing import Any

from tiqora.ai.llm import LlmMessage, LlmResponse, LlmUsage
from tiqora.ai.vision import MAX_IMAGES_PER_RUN, describe_images


class FakeVisionLlm:
    def __init__(self, response: str | None = "A cat sitting on a windowsill.") -> None:
        self.response = response
        self.calls = 0
        self.seen_messages: list[list[LlmMessage]] = []

    async def chat(
        self,
        *,
        messages: list[LlmMessage],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        self.calls += 1
        self.seen_messages.append(messages)
        return LlmResponse(
            content=self.response, usage=LlmUsage(prompt_tokens=20, completion_tokens=10)
        )


class RaisingLlm:
    async def chat(self, **kwargs: Any) -> LlmResponse:
        raise RuntimeError("vision endpoint unreachable")


async def test_describe_images_returns_description_and_only_sees_images() -> None:
    fake = FakeVisionLlm("A screenshot of an error dialog.")
    images = [("error.png", "image/png", b"\x89PNG fake bytes")]

    results = await describe_images(images, llm_factory=lambda: fake)

    assert results == [("error.png", "A screenshot of an error dialog.")]
    assert fake.calls == 1
    [sent] = fake.seen_messages
    [message] = sent
    assert message.role == "user"
    assert isinstance(message.content, list)
    parts = message.content
    assert any(p["type"] == "text" for p in parts)
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    # No ticket text ever appears in the vision prompt.
    assert not any("ticket" in str(p).lower() for p in parts)


async def test_describe_images_caps_at_max_per_run() -> None:
    fake = FakeVisionLlm("desc")
    images = [(f"img{i}.png", "image/png", b"data") for i in range(MAX_IMAGES_PER_RUN + 3)]

    results = await describe_images(images, llm_factory=lambda: fake)

    assert len(results) == MAX_IMAGES_PER_RUN
    assert fake.calls == MAX_IMAGES_PER_RUN


async def test_describe_images_skips_oversized_image_without_calling_llm() -> None:
    from tiqora.ai.vision import MAX_IMAGE_BYTES

    fake = FakeVisionLlm("desc")
    oversized = b"x" * (MAX_IMAGE_BYTES + 1)
    images = [("huge.png", "image/png", oversized), ("small.png", "image/png", b"ok")]

    results = await describe_images(images, llm_factory=lambda: fake)

    assert results[0] == ("huge.png", "")
    assert results[1] == ("small.png", "desc")
    assert fake.calls == 1


async def test_describe_images_failure_yields_empty_description_and_continues() -> None:
    raising = RaisingLlm()
    images = [("a.png", "image/png", b"x"), ("b.png", "image/png", b"y")]

    results = await describe_images(images, llm_factory=lambda: raising)

    assert results == [("a.png", ""), ("b.png", "")]


async def test_describe_images_empty_response_yields_empty_description() -> None:
    fake = FakeVisionLlm(response=None)
    results = await describe_images([("a.png", "image/png", b"x")], llm_factory=lambda: fake)
    assert results == [("a.png", "")]
