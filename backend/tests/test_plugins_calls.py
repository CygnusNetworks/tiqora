from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import structlog.testing

from tiqora.plugins.calls import PluginCallBudget, call_plugin, call_plugin_sync


async def _ret(value: int) -> int:
    return value


def _raise(exc: BaseException) -> Callable[[], Awaitable[Any]]:
    async def fn() -> Any:
        raise exc

    return fn


async def test_ok_and_value() -> None:
    out = await call_plugin("org.example.x", "t", lambda: _ret(5), timeout=1)
    assert out.ok and out.value == 5 and out.error is None and not out.timed_out


async def test_timeout() -> None:
    out = await call_plugin("org.example.x", "t", lambda: asyncio.sleep(5), timeout=0.05)
    assert not out.ok and out.timed_out and "Zeitüberschreitung" in (out.error or "")


async def test_exception_and_systemexit_contained() -> None:
    out = await call_plugin("p", "t", _raise(ValueError("kaputt")), timeout=1)
    assert not out.ok and out.error == "ValueError: kaputt"
    out = await call_plugin("p", "t", _raise(SystemExit(3)), timeout=1)
    assert not out.ok and (out.error or "").startswith("SystemExit")


async def test_synchronous_raise_in_factory_is_contained() -> None:
    def fn() -> Awaitable[int]:
        raise RuntimeError("vor dem await")

    out = await call_plugin("p", "t", fn, timeout=1)
    assert not out.ok and "vor dem await" in (out.error or "")


async def test_error_text_is_truncated() -> None:
    out = await call_plugin("p", "t", _raise(ValueError("x" * 2000)), timeout=1)
    assert out.error is not None and len(out.error) == 500


async def test_caller_cancellation_propagates() -> None:
    task = asyncio.create_task(call_plugin("p", "t", lambda: asyncio.sleep(5), timeout=10))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_plugin_raised_cancelled_is_failure() -> None:
    out = await call_plugin("p", "t", _raise(asyncio.CancelledError()), timeout=1)
    assert not out.ok and (out.error or "").startswith("CancelledError")


def test_sync_variant_and_budget() -> None:
    assert call_plugin_sync("p", "t", lambda: 1).value == 1
    assert not call_plugin_sync("p", "t", lambda: 1 / 0).ok
    exited = call_plugin_sync("p", "t", lambda: (_ for _ in ()).throw(SystemExit(1)))
    assert not exited.ok
    clock = iter([0.0, 0.0, 4.0, 6.0]).__next__
    budget = PluginCallBudget(5.0, clock=clock)
    assert budget.timeout_for(3.0) == 3.0 and budget.timeout_for(3.0) == 1.0
    assert budget.remaining() == 0.0


async def test_failure_logged_without_arguments() -> None:
    with structlog.testing.capture_logs() as logs:
        await call_plugin("p", "auth", _raise(ValueError("geheim123")), timeout=1)
    entry = next(e for e in logs if e["event"] == "plugin_call_failed")
    assert entry["plugin"] == "p" and entry["what"] == "auth" and not entry["timed_out"]


async def test_redact_scrubs_error_and_log() -> None:
    with structlog.testing.capture_logs() as logs:
        out = await call_plugin(
            "p",
            "auth",
            _raise(ValueError("Anmeldung mit geheim123 abgelehnt")),
            timeout=1,
            redact=("geheim123", ""),
        )
        sync = call_plugin_sync("p", "auth", lambda: 1 / 0, redact=("division",))
    assert out.error == "ValueError: Anmeldung mit *** abgelehnt"
    assert "geheim123" not in repr(logs) and "division" not in repr(logs)
    assert sync.error is not None and "***" in sync.error
