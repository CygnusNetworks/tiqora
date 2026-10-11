"""Run widget data providers with a per-provider budget (D6 rule 2, R-DASH-RUNNER-POOL).

Rules that keep one dashboard from starving the connection pool or the API:

- Every provider gets its **own** session (``AsyncSession`` is not concurrency-safe) and at
  most :data:`DASHBOARD_PROVIDER_CONCURRENCY` run at once per call; a widget waiting for such a
  per-request slot does not spend its budget.
- Process-wide, provider connections of one engine are capped at ``max(1, pool_size // 2)``
  (R-DASH-GLOBAL-CAP, :func:`provider_slots`), shared by batches, single-widget fetches and
  previews, so many agents opening the home page at once leave the rest of the pool to the API.
  Waiting for that slot and for the connection counts against the widget's budget; when no more
  than the grace second is left, the widget is ``pending`` without running its statements.
- A provider's whole unit — global slot, session, connection checkout, ``statement_timeout``
  of the budget remaining at statement start, and the run — sits inside
  ``asyncio.wait_for(budget + grace)``, so the database aborts a slow statement first and the
  connection stays clean, also under pool pressure.
- When the client side has to cancel anyway (budget, batch deadline, request cancelled), the
  connection is invalidated before anything else touches it: a cancelled aiomysql/asyncpg call
  leaves it mid-protocol and it must never go back to the pool.
- The whole batch ends after :data:`BATCH_DEADLINE_SECONDS`; unfinished and unstarted widgets
  are ``pending`` (the client fetches them alone, with :data:`SINGLE_WIDGET_TIMEOUT_SECONDS`).
- The runner commits after the statement timeout is lifted (providers never commit), so a
  MariaDB session limit never leaks into the pool with a released connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import datetime
from typing import Any, Final, Protocol
from weakref import WeakKeyDictionary

import structlog
from pydantic import ValidationError
from redis.asyncio import Redis

from tiqora.config import Settings
from tiqora.dashboard.filters import DashboardFilters, effective_filters
from tiqora.dashboard.providers import (
    DASHBOARD_PROVIDERS,
    DashboardProvider,
    ProviderContext,
    ProviderError,
    ProviderForbidden,
)
from tiqora.dashboard.schemas import WidgetDataOut
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.reporting.sql import (
    ReportQueryTimeout,
    invalidate_quietly,
    is_timeout_error,
    statement_timeout,
)

logger = structlog.get_logger(__name__)

DASHBOARD_PROVIDER_CONCURRENCY: Final = 4
BATCH_DEADLINE_SECONDS: Final = 20.0
SINGLE_WIDGET_TIMEOUT_SECONDS: Final = 25.0
#: client-side slack on top of a provider's budget: the database statement timeout fires first
CLIENT_GRACE_SECONDS: Final = 1.0
#: how long a batch waits for cancelled providers to invalidate and close their sessions
CANCEL_WAIT_SECONDS: Final = 2.0

ERROR_MESSAGE: Final = "Diese Daten konnten nicht geladen werden."
INVALID_SETTINGS_MESSAGE: Final = "Die Einstellungen dieses Widgets sind ungültig."
NEWER_VERSION_MESSAGE: Final = "Dieses Widget braucht eine neuere Version."

#: cancelled provider tasks still closing their sessions after a batch returned
_CLOSING: set[asyncio.Task[Any]] = set()
#: process-wide provider slots per engine (and event loop), see :func:`provider_slots`
_ENGINE_SLOTS: WeakKeyDictionary[Any, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)


class _Starved(Exception):  # noqa: N818 - internal signal
    """Too little budget left after waiting for a slot or a connection."""


class WidgetRow(Protocol):
    """What the runner needs of a stored widget (``WidgetOut`` fits)."""

    @property
    def id(self) -> str: ...
    @property
    def type_key(self) -> str: ...
    @property
    def type_version(self) -> int: ...
    @property
    def settings(self) -> Mapping[str, Any]: ...
    @property
    def pinned(self) -> bool: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[Any]]


def provider_slots(factory: SessionFactory) -> asyncio.Semaphore | None:
    """The process-wide provider semaphore of the factory's engine (R-DASH-GLOBAL-CAP).

    Sized ``max(1, pool_size // 2)`` (overflow not counted). ``None`` when the factory has no
    engine with a sized pool (test stubs, ``NullPool``).
    """
    kw = getattr(factory, "kw", None)
    engine = kw.get("bind") if isinstance(kw, dict) else None
    if engine is None:
        return None
    pool = getattr(getattr(engine, "sync_engine", engine), "pool", None)
    size = getattr(pool, "size", None)
    if not callable(size):
        return None
    loop = asyncio.get_running_loop()
    cached = _ENGINE_SLOTS.get(engine)
    if cached is not None and cached[0] is loop:
        return cached[1]
    slots = asyncio.Semaphore(max(1, int(size()) // 2))
    _ENGINE_SLOTS[engine] = (loop, slots)
    return slots


def _pending() -> WidgetDataOut:
    return WidgetDataOut(status="pending")


def _error(message: str) -> WidgetDataOut:
    return WidgetDataOut(status="error", error=message)


def _ctx_filters(filters: DashboardFilters, widget: WidgetRow) -> dict[str, Any]:
    """``ProviderContext.filters`` of a widget: the empty filters when it is pinned."""
    eff = effective_filters(filters, pinned=bool(getattr(widget, "pinned", False)))
    return {"queue_ids": list(eff.queue_ids), "time_range": eff.time_range}


def _prepare(provider: DashboardProvider, widget: WidgetRow) -> Any:
    """Validated settings, or a ready ``WidgetDataOut`` when the widget cannot run."""
    if widget.type_version > provider.version:
        return _error(NEWER_VERSION_MESSAGE)
    try:
        return provider.settings_model.model_validate(dict(widget.settings))
    except ValidationError:
        return _error(INVALID_SETTINGS_MESSAGE)


class _Connection:
    """Tracks whether the unit holds a connection that a cancellation must invalidate."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.held = False
        self.invalidated = False

    async def invalidate(self) -> None:
        # never checks out a fresh connection just to throw it away (cancel during checkout)
        if self.held and not self.invalidated:
            self.invalidated = True
            await invalidate_quietly(self.session)


async def _unit(
    factory: SessionFactory,
    provider: DashboardProvider,
    settings: Any,
    *,
    user_id: int,
    budget: float,
    grace: float,
    redis: Redis | None,
    now: datetime,
    preview: bool,
    filters: Mapping[str, Any],
    app_settings: Settings | None,
    report_cache_backend: Any | None,
    kiosk: bool,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    started = loop.time()

    def remaining() -> float:
        """Budget left; ``_Starved`` when no more than the grace is left."""
        left = budget - (loop.time() - started)
        if left <= grace:
            raise _Starved
        return left

    slots = provider_slots(factory)
    async with slots if slots is not None else nullcontext():
        remaining()  # waiting for the slot counts against the budget
        async with factory() as session:
            conn = _Connection(session)
            ctx = ProviderContext(
                user_id=user_id,
                session=session,
                now=now,
                filters=filters,
                redis=redis,
                preview=preview,
                app_settings=app_settings,
                report_cache_backend=report_cache_backend,
                kiosk=kiosk,
                deadline=started + budget,
            )
            try:
                if getattr(session, "bind", None) is not None:
                    await session.connection()  # checkout inside the budget
                    conn.held = True
                    # whole seconds of what is left: the database aborts before the client
                    seconds = max(1, int(remaining()))
                    # a self-limited provider bounds its statements itself (its engine would
                    # reset a MariaDB session limit to 0 after the first statement)
                    limit = (
                        nullcontext()
                        if provider.self_limited
                        else statement_timeout(session, seconds)
                    )
                    async with limit:
                        try:
                            data = await provider.run(ctx, settings)
                        except asyncio.CancelledError:
                            # before statement_timeout's reset runs on a mid-protocol connection
                            await conn.invalidate()
                            raise
                else:  # sessions without a bind (tests): nothing to bound server-side
                    conn.held = True
                    remaining()
                    data = await provider.run(ctx, settings)
                await session.commit()
                return data
            except asyncio.CancelledError:
                await conn.invalidate()
                raise


async def _run_one(
    factory: SessionFactory,
    provider: DashboardProvider,
    widget: WidgetRow,
    settings: Any,
    *,
    user_id: int,
    budget: float,
    grace: float,
    redis: Redis | None,
    now: datetime,
    preview: bool,
    filters: Mapping[str, Any],
    app_settings: Settings | None,
    report_cache_backend: Any | None,
    kiosk: bool,
) -> WidgetDataOut:
    try:
        data = await asyncio.wait_for(
            _unit(
                factory,
                provider,
                settings,
                user_id=user_id,
                budget=budget,
                grace=grace,
                redis=redis,
                now=now,
                preview=preview,
                filters=filters,
                app_settings=app_settings,
                report_cache_backend=report_cache_backend,
                kiosk=kiosk,
            ),
            budget + grace,
        )
    except TimeoutError:
        return _pending()
    except _Starved:
        logger.info("dashboard_provider_starved", key=provider.key, widget_id=widget.id)
        return _pending()
    except ProviderForbidden as exc:
        return WidgetDataOut(status="forbidden", error=exc.message)
    except ProviderError as exc:
        return _error(exc.message)
    except ReportQueryTimeout:
        return _pending()
    except Exception as exc:
        if is_timeout_error(exc):
            return _pending()
        logger.warning(
            "dashboard_provider_failed",
            key=provider.key,
            widget_id=widget.id,
            error_type=type(exc).__name__,
        )
        return _error(ERROR_MESSAGE)
    return WidgetDataOut(status="ok", data=data)


async def run_batch(
    factory: SessionFactory,
    registry: Registry,
    user_id: int,
    widgets: Sequence[WidgetRow],
    *,
    timeout: float | None = None,  # noqa: ASYNC109 - per-provider budget, signature fixed by D6
    redis: Redis | None = None,
    now: datetime | None = None,
    preview: bool = False,
    deadline: float = BATCH_DEADLINE_SECONDS,
    concurrency: int | None = None,
    grace: float = CLIENT_GRACE_SECONDS,
    filters: DashboardFilters | None = None,
    app_settings: Settings | None = None,
    report_cache_backend: Any | None = None,
    kiosk: bool = False,
) -> dict[str, WidgetDataOut]:
    """Data of every widget that has a provider, keyed by widget id.

    ``timeout`` overrides the providers' own budgets (``None`` = each provider's
    ``timeout_seconds``). Widgets without a provider are absent from the result. ``filters``
    are the dashboard's; a pinned widget gets the empty filters instead.
    """
    base_filters = filters or DashboardFilters()
    run_now = now or utc_now()
    results: dict[str, WidgetDataOut] = {}
    runnable: list[tuple[WidgetRow, DashboardProvider, Any]] = []
    for w in widgets:
        provider = registry.get(DASHBOARD_PROVIDERS, w.type_key)
        if provider is None:
            continue
        prepared = _prepare(provider, w)
        if isinstance(prepared, WidgetDataOut):
            results[w.id] = prepared
        else:
            runnable.append((w, provider, prepared))
    if not runnable:
        return results

    slots = asyncio.Semaphore(concurrency or DASHBOARD_PROVIDER_CONCURRENCY)

    async def guarded(w: WidgetRow, provider: DashboardProvider, settings: Any) -> WidgetDataOut:
        async with slots:
            return await _run_one(
                factory,
                provider,
                w,
                settings,
                user_id=user_id,
                budget=provider.timeout_seconds if timeout is None else timeout,
                grace=grace,
                redis=redis,
                now=run_now,
                preview=preview,
                filters=_ctx_filters(base_filters, w),
                app_settings=app_settings,
                report_cache_backend=report_cache_backend,
                kiosk=kiosk,
            )

    tasks = {w.id: asyncio.create_task(guarded(w, p, s)) for w, p, s in runnable}
    try:
        await asyncio.wait(tasks.values(), timeout=deadline)
    finally:
        leftover = [t for t in tasks.values() if not t.done()]
        for t in leftover:
            t.cancel()
        if leftover:
            # let cancelled providers invalidate and close their sessions before we answer
            _, still = await asyncio.wait(leftover, timeout=CANCEL_WAIT_SECONDS)
            for t in still:  # keep a reference until they are done
                _CLOSING.add(t)
                t.add_done_callback(_CLOSING.discard)
    for wid, task in tasks.items():
        if task.cancelled() or not task.done():
            results[wid] = _pending()
        elif task.exception() is not None:
            results[wid] = _error(ERROR_MESSAGE)
        else:
            results[wid] = task.result()
    return results


async def run_widget(
    factory: SessionFactory,
    registry: Registry,
    user_id: int,
    widget: WidgetRow,
    *,
    timeout: float = SINGLE_WIDGET_TIMEOUT_SECONDS,  # noqa: ASYNC109 - D6 single-widget budget
    redis: Redis | None = None,
    now: datetime | None = None,
    preview: bool = False,
    grace: float = CLIENT_GRACE_SECONDS,
    filters: DashboardFilters | None = None,
    app_settings: Settings | None = None,
    report_cache_backend: Any | None = None,
    kiosk: bool = False,
) -> WidgetDataOut | None:
    """One widget with the single-widget budget; ``None`` when its type has no provider."""
    provider = registry.get(DASHBOARD_PROVIDERS, widget.type_key)
    if provider is None:
        return None
    prepared = _prepare(provider, widget)
    if isinstance(prepared, WidgetDataOut):
        return prepared
    return await _run_one(
        factory,
        provider,
        widget,
        prepared,
        user_id=user_id,
        budget=timeout,
        grace=grace,
        redis=redis,
        now=now or utc_now(),
        preview=preview,
        filters=_ctx_filters(filters or DashboardFilters(), widget),
        app_settings=app_settings,
        report_cache_backend=report_cache_backend,
        kiosk=kiosk,
    )
