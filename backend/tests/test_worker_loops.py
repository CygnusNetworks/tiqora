"""Pure unit tests for the worker's generic tick-loop helpers (no DB, no
testcontainers) — ``seconds_until_daily`` edge cases, ``_interval_loop``
survival/status-recording behaviour, and ``record_tick_status`` never
propagating a DB failure.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

import tiqora.worker.__main__ as worker_main
from tiqora.worker.status import record_tick_status, seconds_until_daily


def test_seconds_until_daily_future_today() -> None:
    now = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
    assert seconds_until_daily("12:00", now=now) == pytest.approx(2 * 3600)


def test_seconds_until_daily_past_today_rolls_to_tomorrow() -> None:
    now = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
    assert seconds_until_daily("12:00", now=now) == pytest.approx(22 * 3600)


def test_seconds_until_daily_exact_match_rolls_to_tomorrow() -> None:
    """A tick landing exactly on the target time must not busy-loop — the
    next occurrence is always strictly in the future."""
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    assert seconds_until_daily("12:00", now=now) == pytest.approx(24 * 3600)


async def test_interval_loop_records_ok_then_error_and_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None]] = []

    async def fake_record_tick_status(
        service: str, *, ok: bool, result: object = None, error: str | None = None, **_: object
    ) -> None:
        calls.append((service, ok, error))

    monkeypatch.setattr(worker_main, "record_tick_status", fake_record_tick_status)

    stop = asyncio.Event()
    attempts = {"n": 0}

    async def fake_tick() -> dict[str, int]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"processed": 1}
        stop.set()
        raise RuntimeError("boom")

    # interval_key=None skips the DB override round-trip entirely.
    await worker_main._interval_loop("fake_service", fake_tick, 5, None, stop)

    assert attempts["n"] == 2, "loop must survive the raised exception and re-enter once more"
    assert calls[0] == ("fake_service", True, None)
    assert calls[1][0] == "fake_service"
    assert calls[1][1] is False
    assert "boom" in (calls[1][2] or "")


async def test_record_tick_status_swallows_session_factory_error() -> None:
    """A DB outage while recording status must never propagate — it would
    otherwise kill the loop it is meant to be instrumenting."""

    def broken_factory() -> None:
        raise RuntimeError("db is down")

    await record_tick_status(
        "fake_service",
        ok=True,
        result={"x": 1},
        session_factory=broken_factory,  # type: ignore[arg-type]
    )
