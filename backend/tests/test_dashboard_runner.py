"""Dashboard runner: per-provider sessions, budgets, statuses, concurrency cap (pure asyncio).

A private ``Registry`` with fake providers and fake session factories (async context managers
yielding a stub without a bind, so no statement timeout is applied). Most tests pass
``grace=0.0`` so a provider's own budget is the whole client-side budget; the default grace of
one second (R-DASH-RUNNER-POOL: the database aborts first) is pinned separately.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from tiqora.dashboard import runner
from tiqora.dashboard.providers import (
    DASHBOARD_PROVIDERS,
    DashboardProvider,
    ProviderContext,
    ProviderForbidden,
)
from tiqora.dashboard.runner import (
    DASHBOARD_PROVIDER_CONCURRENCY,
    ERROR_MESSAGE,
    INVALID_SETTINGS_MESSAGE,
    run_batch,
    run_widget,
)
from tiqora.extensions.registry import Registry
from tiqora.reporting.sql import ReportQueryTimeout

# --- helpers ---------------------------------------------------------------------------


class AnyModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class LimitModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(8, le=50)


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1


Run = Callable[[ProviderContext, Any], Awaitable[dict[str, Any]]]


def provider(
    key: str,
    run: Run,
    *,
    timeout_seconds: float = 5.0,
    settings_model: type[BaseModel] = AnyModel,
    version: int = 1,
) -> DashboardProvider:
    return DashboardProvider(
        key=key,
        version=version,
        settings_model=settings_model,
        run=run,
        timeout_seconds=timeout_seconds,
    )


def registry_with(*providers: DashboardProvider) -> Registry:
    reg = Registry()
    for p in providers:
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


async def fast(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
    return {"n": 1}


def slow_sleep(seconds: float) -> Run:
    async def run(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
        await asyncio.sleep(seconds)
        return {"slept": seconds}

    return run


def raises(exc: Exception) -> Run:
    async def run(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
        raise exc

    return run


class StubSession:
    """No ``bind``: the runner applies no statement timeout to it."""

    bind = None

    def __init__(self, seq: int) -> None:
        self.seq = seq
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def recording_factory(log: list[str], sessions: list[StubSession] | None = None) -> Any:
    """Each session appends ``enter`` / ``exit``; sessions are numbered in creation order."""
    counter = [0]

    @asynccontextmanager
    async def factory() -> AsyncIterator[StubSession]:
        counter[0] += 1
        s = StubSession(counter[0])
        if sessions is not None:
            sessions.append(s)
        log.append("enter")
        try:
            yield s
        finally:
            log.append("exit")

    return factory


def fake_factory() -> Any:
    return recording_factory([])()


class CapacityFactory:
    """A "pool" that counts open sessions; never more than ``capacity`` may be open."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.open = 0
        self.max_open = 0
        self.total = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[StubSession]:
        if self.open >= self.capacity:
            raise RuntimeError("pool exhausted")
        self.open += 1
        self.total += 1
        self.max_open = max(self.max_open, self.open)
        try:
            yield StubSession(self.total)
        finally:
            self.open -= 1


# --- statuses ----------------------------------------------------------------------------


async def test_slow_provider_is_pending_others_ok() -> None:
    reg = registry_with(
        provider("t.fast", fast), provider("t.slow", slow_sleep(0.5), timeout_seconds=0.05)
    )
    out = await run_batch(fake_factory, reg, 1, [W("a", "t.fast"), W("b", "t.slow")], grace=0.0)
    assert out["a"].status == "ok" and out["a"].data == {"n": 1}
    assert out["b"].status == "pending" and out["b"].data is None


async def test_default_grace_gives_the_database_a_second_to_abort_first() -> None:
    """Client budget = provider budget + 1 s: a 0.3 s run with a 0.05 s budget still finishes."""
    reg = registry_with(provider("t.slow", slow_sleep(0.3), timeout_seconds=0.05))
    out = await run_batch(fake_factory, reg, 1, [W("a", "t.slow")])
    assert out["a"].status == "ok" and out["a"].data == {"slept": 0.3}


async def test_provider_exception_is_error_status() -> None:
    out = await run_batch(
        fake_factory,
        registry_with(provider("t.boom", raises(RuntimeError("secret detail")))),
        1,
        [W("a", "t.boom")],
    )
    assert out["a"].status == "error" and out["a"].error == ERROR_MESSAGE
    assert ERROR_MESSAGE == "Diese Daten konnten nicht geladen werden."
    assert "secret detail" not in (out["a"].error or "")  # never leaks exception text


async def test_database_timeouts_are_pending() -> None:
    class PgTimeout(Exception):
        pgcode = "57014"

    reg = registry_with(
        provider("t.rq", raises(ReportQueryTimeout("statement exceeded 5s"))),
        provider("t.pg", raises(PgTimeout("canceling statement due to statement timeout"))),
    )
    out = await run_batch(fake_factory, reg, 1, [W("a", "t.rq"), W("b", "t.pg")])
    assert (out["a"].status, out["b"].status) == ("pending", "pending")
    assert out["a"].error is None and out["b"].data is None


async def test_forbidden_and_invalid_settings_statuses() -> None:
    reg = registry_with(
        provider("t.deny", raises(ProviderForbidden("Nein."))),
        provider("t.lim", fast, settings_model=LimitModel),
    )
    out = await run_batch(
        fake_factory, reg, 1, [W("a", "t.deny"), W("b", "t.lim", settings={"limit": 99})]
    )
    assert (out["a"].status, out["a"].error) == ("forbidden", "Nein.")
    assert (out["b"].status, out["b"].error) == ("error", INVALID_SETTINGS_MESSAGE)
    assert INVALID_SETTINGS_MESSAGE == "Die Einstellungen dieses Widgets sind ungültig."
    assert ProviderForbidden().message == "Dafür fehlt dir die Berechtigung."


async def test_invalid_settings_do_not_open_a_session() -> None:
    log: list[str] = []
    reg = registry_with(provider("t.lim", fast, settings_model=LimitModel))
    out = await run_batch(recording_factory(log), reg, 1, [W("b", "t.lim", settings={"x": 1})])
    assert out["b"].status == "error" and log == []


async def test_widgets_without_provider_are_absent_and_newer_version_is_an_error() -> None:
    reg = registry_with(provider("t.fast", fast))
    out = await run_batch(
        fake_factory, reg, 1, [W("a", "org.example.weather"), W("b", "t.fast", type_version=2)]
    )
    assert "a" not in out and out["b"].status == "error"
    assert out["b"].error == "Dieses Widget braucht eine neuere Version."


async def test_settings_defaults_are_applied_and_viewer_context_is_passed() -> None:
    seen: list[tuple[int, int, bool, Any]] = []

    async def record(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
        seen.append((ctx.user_id, settings.limit, ctx.preview, dict(ctx.filters)))
        return {}

    reg = registry_with(provider("t.lim", record, settings_model=LimitModel))
    await run_batch(fake_factory, reg, 49790, [W("a", "t.lim")])
    out = await run_widget(fake_factory, reg, 49791, W("b", "t.lim"), preview=True)
    assert out is not None and out.status == "ok"
    assert seen == [(49790, 8, False, {}), (49791, 8, True, {})]


# --- sessions ----------------------------------------------------------------------------


async def test_each_provider_gets_its_own_session() -> None:
    seen: list[int] = []

    async def record(ctx: ProviderContext, settings: Any) -> dict[str, Any]:
        seen.append(ctx.session.seq)
        return {}

    log: list[str] = []
    sessions: list[StubSession] = []
    await run_batch(
        recording_factory(log, sessions),
        registry_with(provider("t.a", record), provider("t.b", record)),
        1,
        [W("a", "t.a"), W("b", "t.b")],
    )
    # counted inside the factory: two sessions, never the request's or each other's
    assert sorted(seen) == [1, 2]
    assert log.count("enter") == 2 and log.count("exit") == 2
    assert [s.commits for s in sessions] == [1, 1]


async def test_cancelled_provider_session_is_closed() -> None:
    log: list[str] = []
    factory = recording_factory(log)
    reg = registry_with(provider("t.slow", slow_sleep(0.5), timeout_seconds=0.05))
    out = await run_batch(factory, reg, 1, [W("a", "t.slow")], grace=0.0)
    assert out["a"].status == "pending"
    assert log == ["enter", "exit"]  # the timed-out provider's session was closed


async def test_timed_out_provider_session_is_invalidated(monkeypatch: pytest.MonkeyPatch) -> None:
    invalidated: list[int] = []

    async def spy(session: Any) -> None:
        invalidated.append(session.seq)

    monkeypatch.setattr(runner, "invalidate_quietly", spy)
    sessions: list[StubSession] = []
    reg = registry_with(
        provider("t.fast", fast), provider("t.slow", slow_sleep(2.0), timeout_seconds=0.05)
    )
    out = await run_batch(
        recording_factory([], sessions), reg, 1, [W("a", "t.fast"), W("b", "t.slow")], grace=0.0
    )
    assert (out["a"].status, out["b"].status) == ("ok", "pending")
    slow = [s.seq for s in sessions if s.commits == 0]
    assert invalidated == slow and len(slow) == 1  # only the cancelled one, never returned as is


async def test_never_more_than_four_sessions_open() -> None:
    assert DASHBOARD_PROVIDER_CONCURRENCY == 4
    pool = CapacityFactory(capacity=DASHBOARD_PROVIDER_CONCURRENCY)
    reg = registry_with(provider("t.slow", slow_sleep(0.1)))
    widgets = [W(f"w{i}", "t.slow") for i in range(10)]
    out = await run_batch(pool, reg, 1, widgets)
    assert {r.status for r in out.values()} == {"ok"} and len(out) == 10
    assert pool.max_open == 4 and pool.total == 10 and pool.open == 0


async def test_queued_widgets_do_not_burn_their_budget_waiting_for_a_slot() -> None:
    """Budget starts when the provider starts: 8 x 0.2 s with a 0.3 s budget are all ok."""
    reg = registry_with(provider("t.slow", slow_sleep(0.2), timeout_seconds=0.3))
    out = await run_batch(fake_factory, reg, 1, [W(f"w{i}", "t.slow") for i in range(8)], grace=0)
    assert {r.status for r in out.values()} == {"ok"}


async def test_batch_deadline_turns_unfinished_and_unstarted_widgets_pending() -> None:
    log: list[str] = []
    reg = registry_with(
        provider("t.fast", fast), provider("t.slow", slow_sleep(5.0), timeout_seconds=10)
    )
    widgets: Sequence[W] = [W("f", "t.fast")] + [W(f"s{i}", "t.slow") for i in range(6)]
    started = time.monotonic()
    out = await run_batch(recording_factory(log), reg, 1, widgets, deadline=0.3)
    assert time.monotonic() - started < 1.5
    assert out["f"].status == "ok"
    assert {out[f"s{i}"].status for i in range(6)} == {"pending"}
    # the fast one plus four slow ones (cap) started, two never got a slot; all were closed
    assert log.count("enter") == log.count("exit") == 5


async def test_run_widget_uses_the_single_budget_not_the_provider_budget() -> None:
    reg = registry_with(provider("t.slow", slow_sleep(0.2), timeout_seconds=0.05))
    single = await run_widget(fake_factory, reg, 1, W("a", "t.slow"), grace=0.0)
    batch = await run_batch(fake_factory, reg, 1, [W("a", "t.slow")], grace=0.0)
    assert single is not None and single.status == "ok" and batch["a"].status == "pending"
    assert runner.SINGLE_WIDGET_TIMEOUT_SECONDS == 25.0
    assert runner.BATCH_DEADLINE_SECONDS == 20.0
    assert await run_widget(fake_factory, reg, 1, W("x", "org.example.none")) is None


async def test_cancelling_the_batch_closes_running_sessions() -> None:
    log: list[str] = []
    reg = registry_with(provider("t.slow", slow_sleep(5.0)))
    task = asyncio.create_task(run_batch(recording_factory(log), reg, 1, [W("a", "t.slow")]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == ["enter", "exit"]
