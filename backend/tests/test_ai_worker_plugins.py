"""The ai-worker builds the plugin registry before its loops start."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tiqora.ai import worker as ai_worker
from tiqora.extensions.registry import Registry


class _Stop(Exception):
    pass


async def test_run_all_inits_registry_first(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def fake_init(**kw: Any) -> Registry:
        order.append(f"init:{kw['role']}")
        return Registry()

    async def sentinel_loop(stop: asyncio.Event) -> None:
        order.append("loop")
        raise _Stop

    monkeypatch.setattr(ai_worker, "init_registry", fake_init)
    monkeypatch.setattr(ai_worker, "get_session_factory", lambda: object())
    monkeypatch.setattr(ai_worker, "_heartbeat_loop", sentinel_loop)
    monkeypatch.setattr(ai_worker, "_run_loop", sentinel_loop)
    monkeypatch.setattr(ai_worker, "run_chat_bot_loop", sentinel_loop)

    with pytest.raises(_Stop):
        await ai_worker._run_all(asyncio.Event())

    assert order[0] == "init:ai-worker"
    assert "loop" in order


async def test_init_failure_propagates_not_swallowed_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """init_registry itself never raises for plugin errors; a core error fails the boot."""

    async def boom(**kw: Any) -> Registry:
        raise RuntimeError("core registration broken")

    monkeypatch.setattr(ai_worker, "init_registry", boom)
    with pytest.raises(RuntimeError, match="core registration"):
        await ai_worker._run_all(asyncio.Event())
