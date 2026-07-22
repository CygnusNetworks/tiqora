"""Unit tests for the KB wiring in tiqora.ai.kb_wiring (plan §3.4 step 7).

A mocked KbService (monkeypatched at the ``tiqora.kb.service`` module level,
where the wiring functions import it lazily) is enough here — no real
Meilisearch/DB is exercised. Shared between the manual-assist API route and
the auto-reply worker (plan Phase D: "extrahiere die Helpers in ein
gemeinsames Modul").
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from tiqora.ai import kb_wiring
from tiqora.kb.service import KbNotFound


class _FakeArticle:
    def __init__(self, id: int, title: str, content_md: str) -> None:
        self.id = id
        self.title = title
        self.content_md = content_md


class _FakeKbService:
    def __init__(self, session: Any, settings: Any) -> None:
        del session, settings

    async def get_knowledge(
        self, user_id: int, *, tags: list[str] | None = None, category_id: int | None = None
    ) -> list[tuple[_FakeArticle, list[str]]]:
        del user_id, tags, category_id
        return [(_FakeArticle(1, "Reset password", "Step 1. Step 2."), ["reset"])]

    async def search_agent(
        self, user_id: int, query: str, *, limit: int = 20, offset: int = 0
    ) -> Any:
        del user_id, limit, offset
        hit = SimpleNamespace(article_id=1, title="Reset password", content=f"Result for {query}")
        return SimpleNamespace(hits=[hit], query=query, estimated_total=1)

    async def get_article_scoped(self, user_id: int, article_id: int) -> _FakeArticle:
        del user_id
        if article_id != 1:
            raise KbNotFound(str(article_id))
        return _FakeArticle(1, "Reset password", "Full steps")

    async def close(self) -> None:
        return None


class _FailingKbService(_FakeKbService):
    async def search_agent(
        self, user_id: int, query: str, *, limit: int = 20, offset: int = 0
    ) -> Any:
        raise RuntimeError("meilisearch unreachable")


async def test_kb_bundle_builds_markdown_from_tagged_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tiqora.kb.service.KbService", _FakeKbService)
    policy = SimpleNamespace(kb_tags=json.dumps(["reset"]), kb_category_ids=None)
    bundle = await kb_wiring.kb_bundle(None, None, 1, policy)  # type: ignore[arg-type]
    assert bundle is not None
    assert "Reset password" in bundle
    assert "Step 1. Step 2." in bundle


async def test_kb_bundle_none_when_queue_has_no_tags_or_category() -> None:
    policy = SimpleNamespace(kb_tags=None, kb_category_ids=None)
    bundle = await kb_wiring.kb_bundle(None, None, 1, policy)  # type: ignore[arg-type]
    assert bundle is None


async def test_kb_search_fn_returns_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiqora.kb.service.KbService", _FakeKbService)
    search = kb_wiring.kb_search_fn(None, None, 1)  # type: ignore[arg-type]
    results = await search("reset password", limit=5)
    assert results == [
        {"article_id": 1, "title": "Reset password", "snippet": "Result for reset password"}
    ]


async def test_kb_search_fn_degrades_cleanly_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tiqora.kb.service.KbService", _FailingKbService)
    search = kb_wiring.kb_search_fn(None, None, 1)  # type: ignore[arg-type]
    results = await search("reset password", limit=5)
    assert results == []


async def test_kb_get_article_fn_returns_none_for_missing_or_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tiqora.kb.service.KbService", _FakeKbService)
    get_article = kb_wiring.kb_get_article_fn(None, None, 1)  # type: ignore[arg-type]
    assert await get_article(999) is None
    found = await get_article(1)
    assert found == {"id": 1, "title": "Reset password", "body": "Full steps"}
