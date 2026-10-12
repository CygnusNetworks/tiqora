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


async def test_core_registry_error_fails_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_all does not wrap init_registry: an error it raises fails the boot."""

    async def boom(**kw: Any) -> Registry:
        raise RuntimeError("core registration broken")

    monkeypatch.setattr(ai_worker, "init_registry", boom)
    with pytest.raises(RuntimeError, match="core registration"):
        await ai_worker._run_all(asyncio.Event())


async def test_plugin_boot_retry_started_and_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    state = {"cancelled": False}

    async def fake_init(**kw: Any) -> Registry:
        return Registry()

    async def fake_retry(**kw: Any) -> bool:
        calls.append(kw)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        return False

    async def loop_then_stop(stop: asyncio.Event) -> None:
        await asyncio.sleep(0)  # let the retry task start
        raise _Stop

    async def idle(stop: asyncio.Event) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(ai_worker, "init_registry", fake_init)
    monkeypatch.setattr(ai_worker, "retry_plugin_boot", fake_retry)
    monkeypatch.setattr(ai_worker, "get_session_factory", lambda: object())
    monkeypatch.setattr(ai_worker, "_heartbeat_loop", loop_then_stop)
    monkeypatch.setattr(ai_worker, "_run_loop", idle)
    monkeypatch.setattr(ai_worker, "run_chat_bot_loop", idle)

    with pytest.raises(_Stop):
        await ai_worker._run_all(asyncio.Event())

    assert [c["role"] for c in calls] == ["ai-worker"]
    assert state["cancelled"]
