"""Unit tests for the ``tiqora-ai-worker`` process shell (heartbeat + tick
loop). All DB/LLM dependencies are mocked — this only exercises the loop
skeleton in ``tiqora.ai.worker`` itself (heartbeat writes, enable-flag gate,
success/error tick-status recording), not ``run_auto_tick``'s own logic
(covered elsewhere)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from tiqora.ai import worker as ai_worker


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _fake_factory() -> _FakeSession:
    return _FakeSession()


def test_write_heartbeat_writes_current_timestamp(tmp_path: Any, monkeypatch: Any) -> None:
    heartbeat_file = tmp_path / "heartbeat"
    monkeypatch.setattr(ai_worker, "_HEARTBEAT_FILE", str(heartbeat_file))

    ai_worker._write_heartbeat()

    contents = heartbeat_file.read_text()
    assert contents.strip().isdigit()


async def test_heartbeat_loop_writes_at_least_once_then_stops(
    tmp_path: Any, monkeypatch: Any
) -> None:
    heartbeat_file = tmp_path / "heartbeat"
    monkeypatch.setattr(ai_worker, "_HEARTBEAT_FILE", str(heartbeat_file))
    monkeypatch.setattr(ai_worker, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    stop = asyncio.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(ai_worker._heartbeat_loop(stop), _stop_soon())

    assert heartbeat_file.read_text().strip().isdigit()


async def test_heartbeat_loop_handles_write_errors_without_raising(
    tmp_path: Any, monkeypatch: Any
) -> None:
    # Point the heartbeat file at a directory that doesn't exist so every
    # write raises OSError; the loop must swallow it and keep running.
    monkeypatch.setattr(
        ai_worker, "_HEARTBEAT_FILE", str(tmp_path / "does-not-exist" / "heartbeat")
    )
    monkeypatch.setattr(ai_worker, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    stop = asyncio.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    # Must complete without raising.
    await asyncio.gather(ai_worker._heartbeat_loop(stop), _stop_soon())


async def test_ai_tick_disabled_skips_run_auto_tick(monkeypatch: Any) -> None:
    monkeypatch.setattr(ai_worker, "get_setting_bool", AsyncMock(return_value=False))
    run_auto_tick_mock = AsyncMock()
    monkeypatch.setattr(ai_worker, "run_auto_tick", run_auto_tick_mock)

    result = await ai_worker._ai_tick(_fake_factory)

    assert result == {"enabled": False}
    run_auto_tick_mock.assert_not_called()


async def test_ai_tick_enabled_calls_run_auto_tick_and_merges_result(monkeypatch: Any) -> None:
    monkeypatch.setattr(ai_worker, "get_setting_bool", AsyncMock(return_value=True))
    run_auto_tick_mock = AsyncMock(return_value={"events": 3, "auto_replies": 1})
    monkeypatch.setattr(ai_worker, "run_auto_tick", run_auto_tick_mock)

    result = await ai_worker._ai_tick(_fake_factory)

    assert result == {"enabled": True, "events": 3, "auto_replies": 1}
    run_auto_tick_mock.assert_awaited_once_with(session_factory=_fake_factory)


async def test_run_loop_records_success_and_stops(monkeypatch: Any) -> None:
    settings = type("S", (), {"ai_worker_interval_seconds": 30})()
    monkeypatch.setattr(ai_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_worker, "get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr(ai_worker, "get_setting_int", AsyncMock(return_value=30))
    ai_tick_mock = AsyncMock(return_value={"enabled": True})
    monkeypatch.setattr(ai_worker, "_ai_tick", ai_tick_mock)

    stop = asyncio.Event()
    record_tick_status_mock = AsyncMock(side_effect=lambda *a, **kw: stop.set())
    monkeypatch.setattr(ai_worker, "record_tick_status", record_tick_status_mock)

    await ai_worker._run_loop(stop)

    ai_tick_mock.assert_awaited_once()
    record_tick_status_mock.assert_awaited_once()
    _args, kwargs = record_tick_status_mock.await_args
    assert kwargs["ok"] is True
    assert kwargs["result"] == {"enabled": True}


async def test_run_loop_records_error_and_stops(monkeypatch: Any) -> None:
    settings = type("S", (), {"ai_worker_interval_seconds": 30})()
    monkeypatch.setattr(ai_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_worker, "get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr(ai_worker, "get_setting_int", AsyncMock(return_value=30))
    ai_tick_mock = AsyncMock(side_effect=RuntimeError("llm exploded"))
    monkeypatch.setattr(ai_worker, "_ai_tick", ai_tick_mock)

    stop = asyncio.Event()
    record_tick_status_mock = AsyncMock(side_effect=lambda *a, **kw: stop.set())
    monkeypatch.setattr(ai_worker, "record_tick_status", record_tick_status_mock)

    # Must not propagate — a failed tick is caught and recorded, not raised.
    await ai_worker._run_loop(stop)

    record_tick_status_mock.assert_awaited_once()
    _args, kwargs = record_tick_status_mock.await_args
    assert kwargs["ok"] is False
    assert "llm exploded" in kwargs["error"]


async def test_run_loop_get_setting_int_failure_still_stops_loop(monkeypatch: Any) -> None:
    """If reading the configured interval itself fails, the exception is
    caught by the same try/except as the tick body, so the loop still
    records an error tick rather than crashing the process."""
    settings = type("S", (), {"ai_worker_interval_seconds": 30})()
    monkeypatch.setattr(ai_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_worker, "get_session_factory", lambda: _fake_factory)
    monkeypatch.setattr(
        ai_worker, "get_setting_int", AsyncMock(side_effect=RuntimeError("db down"))
    )
    ai_tick_mock = AsyncMock()
    monkeypatch.setattr(ai_worker, "_ai_tick", ai_tick_mock)

    stop = asyncio.Event()
    record_tick_status_mock = AsyncMock(side_effect=lambda *a, **kw: stop.set())
    monkeypatch.setattr(ai_worker, "record_tick_status", record_tick_status_mock)

    await ai_worker._run_loop(stop)

    ai_tick_mock.assert_not_awaited()
    record_tick_status_mock.assert_awaited_once()
    _args, kwargs = record_tick_status_mock.await_args
    assert kwargs["ok"] is False
    assert "db down" in kwargs["error"]
