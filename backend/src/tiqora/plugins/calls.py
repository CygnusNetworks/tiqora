"""Contained calls into plugin code (D1.2).

Plugin code runs in-process with full trust, so every call from a request or job path goes
through here: a hard timeout, every failure contained, and failures logged without the
call's arguments (only the plugin id, what was called and the error text).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

_ERROR_MAX: Final = 500


@dataclass(frozen=True, slots=True)
class CallOutcome[T]:
    ok: bool
    value: T | None
    error: str | None  # f"{type(exc).__name__}: {exc}"[:500], or "Zeitüberschreitung nach {n} s"
    timed_out: bool = False


def _scrub(text: str, redact: Sequence[str]) -> str:
    for secret in redact:
        if secret:
            text = text.replace(secret, "***")
    return text


def _failed[T](
    plugin_id: str,
    what: str,
    error: str,
    redact: Sequence[str],
    *,
    timed_out: bool = False,
) -> CallOutcome[T]:
    error = _scrub(error, redact)[:_ERROR_MAX]
    logger.warning(
        "plugin_call_failed", plugin=plugin_id, what=what, error=error, timed_out=timed_out
    )
    return CallOutcome(False, None, error, timed_out)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _calling_task_cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def call_plugin[T](
    plugin_id: str,
    what: str,
    fn: Callable[[], Awaitable[T]],
    *,
    timeout: float,  # noqa: ASYNC109  (D1.2 signature; enforced via wait_for)
    redact: Sequence[str] = (),
) -> CallOutcome[T]:
    """Run ``fn()`` with a hard timeout; never raises for anything the plugin does.

    ``asyncio.CancelledError`` propagates only when the calling task itself is being
    cancelled; raised by plugin code on its own it is a plugin failure. Values in
    ``redact`` are replaced by ``***`` in the error before it is logged or returned.
    """
    try:
        value = await asyncio.wait_for(fn(), timeout)
    except TimeoutError:
        return _failed(
            plugin_id, what, f"Zeitüberschreitung nach {timeout:g} s", redact, timed_out=True
        )
    except asyncio.CancelledError as exc:
        if _calling_task_cancelled():
            raise
        return _failed(plugin_id, what, _describe(exc), redact)
    except Exception as exc:
        return _failed(plugin_id, what, _describe(exc), redact)
    except BaseException as exc:  # SystemExit / KeyboardInterrupt / GeneratorExit from plugin code
        return _failed(plugin_id, what, _describe(exc), redact)
    return CallOutcome(True, value, None)


def call_plugin_sync[T](
    plugin_id: str,
    what: str,
    fn: Callable[[], T],
    *,
    redact: Sequence[str] = (),
) -> CallOutcome[T]:
    """For pure synchronous callbacks (mapping, DF validate/display, number build).

    No timeout is possible: such callbacks must be pure and fast.
    """
    try:
        value = fn()
    except Exception as exc:
        return _failed(plugin_id, what, _describe(exc), redact)
    except BaseException as exc:
        return _failed(plugin_id, what, _describe(exc), redact)
    return CallOutcome(True, value, None)


class PluginCallBudget:
    """Shared deadline for a chain of calls (sync hooks 5 s, auth chain 20 s, ...)."""

    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def timeout_for(self, own: float) -> float:
        return min(own, self.remaining())
