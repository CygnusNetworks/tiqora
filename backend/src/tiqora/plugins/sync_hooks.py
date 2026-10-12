"""Synchronous "before" hooks (D4): a plugin may veto or, on ticket creation, enrich a write.

Hooks run inside the agent's request, so every call is bounded: each hook by its own
timeout, the whole chain of one operation by :data:`OPERATION_BUDGET_SECONDS`. A failing hook
is contained (fail-open skips it, fail-closed vetoes the operation) and counted by the
:class:`SyncBreaker`, which stops calling a hook after :data:`TRIP_AFTER` consecutive failures.

Hooks get plain operation objects, never a session; they read through ``ctx.plugin.tickets``
(own session, committed data only) and must not write.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal, get_args

import structlog
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraPluginHook
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint, Registry
from tiqora.jobs.service import utc_now
from tiqora.plugins.calls import PluginCallBudget, call_plugin
from tiqora.plugins.context import PluginContext, TicketSummary, minimal_context
from tiqora.plugins.state import is_active
from tiqora.plugins.store import (
    MAX_ERROR_LEN,
    ensure_hook,
    load_hooks,
    record_hook_failure,
)

logger = structlog.get_logger(__name__)

SyncOperation = Literal[
    "ticket.create",
    "article.create",
    "ticket.queue_move",
    "ticket.state_change",
    "ticket.dynamic_fields_update",
]
SYNC_OPERATIONS: Final[tuple[SyncOperation, ...]] = get_args(SyncOperation)

#: wall time of all hooks of ONE operation together (a request may fire several operations)
OPERATION_BUDGET_SECONDS: float = 5.0
#: consecutive failures after which a hook is no longer called
TRIP_AFTER: Final = 5
#: tripped hooks get one half-open probe call this often; states are re-read from the database
BREAKER_REFRESH_SECONDS: float = 30
MAX_VETO_MESSAGE_LEN: Final = 300
MAX_TITLE_LEN: Final = 255
HOOK_KIND: Final = "sync"


@dataclass(frozen=True, slots=True)
class ArticleOp:
    ticket_id: int | None  # None inside ticket.create
    channel: str
    sender_type: str
    visible_for_customer: bool
    subject: str
    body: str
    content_type: str
    to: str | None
    cc: str | None
    attachment_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TicketCreateOp:
    queue_id: int
    queue: str
    title: str
    state: str
    priority: str
    type: str | None
    customer_user: str | None
    customer_id: str | None
    dynamic_fields: Mapping[str, tuple[str, ...]]
    article: ArticleOp | None


@dataclass(frozen=True, slots=True)
class QueueMoveOp:
    ticket: TicketSummary
    new_queue_id: int
    new_queue: str


@dataclass(frozen=True, slots=True)
class StateChangeOp:
    ticket: TicketSummary
    new_state: str
    new_state_type: str
    pending_until: datetime | None


@dataclass(frozen=True, slots=True)
class DynamicFieldsOp:
    ticket: TicketSummary
    values: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class TicketCreatePatch:
    """Only honoured for ``ticket.create``; returned for anything else it counts as a failure."""

    title: str | None = None
    priority: str | None = None
    type: str | None = None
    dynamic_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)


class OperationVetoed(InvalidInput):
    """A hook refused the operation (mapped to 422); the message is German and shown to agents."""

    def __init__(self, message: str, *, plugin_id: str | None = None) -> None:
        super().__init__(message[:MAX_VETO_MESSAGE_LEN])
        self.plugin_id = plugin_id


@dataclass(frozen=True, slots=True)
class HookContext:
    plugin: PluginContext
    operation: SyncOperation
    agent_id: int


SyncHandler = Callable[[HookContext, Any], Awaitable[TicketCreatePatch | None]]


@dataclass(frozen=True, slots=True)
class SyncHook:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    operation: SyncOperation
    handler: SyncHandler
    timeout_ms: int  # 50..3000
    on_error: Literal["allow", "deny"]
    order: int  # 0..1000
    context: PluginContext | None = None

    @property
    def name(self) -> str:
        return self.key.split(":", 1)[1] if ":" in self.key else self.key


SYNC_HOOKS: Final[ExtensionPoint[SyncHook]] = ExtensionPoint(
    key="core.events.sync_hooks",
    item_key=lambda h: h.key,
    description="Synchronous checks before a ticket write (veto, enrich on create).",
    sdk_stable=True,
)

NameExists = Callable[[str, str], Awaitable[bool]]


def hook_key(hook: SyncHook) -> str:
    """Key of the hook's ``tiqora_plugin_hook`` row (admin display and breaker match on it)."""
    return f"{HOOK_KIND}:{hook.key}"


# --- circuit breaker --------------------------------------------------------------------------


class SyncBreaker:
    """Process-wide failure counter per hook (class-level singleton).

    In memory it counts consecutive failures and trips at :data:`TRIP_AFTER`. A tripped hook is
    not called, except for one half-open probe every :data:`BREAKER_REFRESH_SECONDS`; a
    successful probe closes the breaker. Failures and recoveries are mirrored into the hook's
    ``tiqora_plugin_hook`` row by fire-and-forget tasks with their own session (never the
    caller's), and the row states are re-read lazily so an admin reset or a trip in another
    process takes effect.
    """

    _consecutive: dict[str, int] = {}  # noqa: RUF012
    _tripped: set[str] = set()  # noqa: RUF012
    _last_attempt: dict[str, float] = {}  # noqa: RUF012
    _last_refresh: float | None = None
    _pending: set[asyncio.Task[None]] = set()  # noqa: RUF012
    _tail: asyncio.Task[None] | None = None
    #: mirror states into the database (pure unit tests switch this off)
    persistence: bool = True
    clock: Callable[[], float] = staticmethod(time.monotonic)

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._consecutive = {}
        cls._tripped = set()
        cls._last_attempt = {}
        cls._last_refresh = None
        for task in cls._pending:
            task.cancel()
        cls._pending = set()
        cls._tail = None
        cls.persistence = True
        cls.clock = staticmethod(time.monotonic)

    @classmethod
    def is_tripped(cls, key: str) -> bool:
        return key in cls._tripped

    @classmethod
    def failures(cls, key: str) -> int:
        return cls._consecutive.get(key, 0)

    @classmethod
    def reset(cls, key: str) -> None:
        """Forget a hook's failures and close its breaker (in this process only)."""
        cls._consecutive.pop(key, None)
        cls._tripped.discard(key)
        cls._last_attempt.pop(key, None)

    @classmethod
    def claim_probe(cls, key: str) -> bool:
        """True when this call may probe a tripped hook (at most one per refresh interval)."""
        now = cls.clock()
        if now - cls._last_attempt.get(key, now) >= BREAKER_REFRESH_SECONDS:
            cls._last_attempt[key] = now
            return True
        return False

    @classmethod
    def failure(cls, key: str, plugin_id: str, error: str) -> bool:
        """Count a failure; True when this one tripped the breaker."""
        count = cls._consecutive.get(key, 0) + 1
        cls._consecutive[key] = count
        tripped_now = count >= TRIP_AFTER and key not in cls._tripped
        if tripped_now:
            cls._tripped.add(key)
            cls._last_attempt[key] = cls.clock()
            logger.error("plugin_sync_hook_tripped", hook=key, plugin=plugin_id, failures=count)
        cls._spawn(_persist_failure(key, plugin_id, error))
        return tripped_now

    @classmethod
    def success(cls, key: str, plugin_id: str) -> None:
        had_trouble = cls._consecutive.pop(key, 0) > 0 or key in cls._tripped
        cls._tripped.discard(key)
        cls._last_attempt.pop(key, None)
        if had_trouble:  # a healthy hook costs no database write
            cls._spawn(_persist_recovery(key))

    @classmethod
    def _spawn(cls, coro: Awaitable[None]) -> None:
        if not cls.persistence:
            if asyncio.iscoroutine(coro):
                coro.close()
            return
        previous = cls._tail

        async def after_previous() -> None:
            # one write at a time: two first-time inserts of the same row would race
            if previous is not None and not previous.done():
                await asyncio.wait({previous})
            await coro

        task = asyncio.ensure_future(after_previous())
        cls._tail = task
        cls._pending.add(task)
        task.add_done_callback(cls._pending.discard)

    @classmethod
    async def flush(cls) -> None:
        """Wait for scheduled database writes (tests, shutdown)."""
        while cls._pending:
            await asyncio.gather(*list(cls._pending), return_exceptions=True)

    @classmethod
    async def refresh_if_due(cls) -> None:
        """Re-read ``tripped_at`` of the sync hook rows (admin reset, trips elsewhere)."""
        if not cls.persistence:
            return
        now = cls.clock()
        if cls._last_refresh is not None and now - cls._last_refresh < BREAKER_REFRESH_SECONDS:
            return
        cls._last_refresh = now
        try:
            await asyncio.wait_for(cls.flush(), 2.0)  # own writes first, or they look stale
            async with get_session_factory()() as session:
                rows = await load_hooks(session, kind=HOOK_KIND)
                states = {k: h.tripped_at is not None for k, h in rows.items()}
        except Exception as exc:  # noqa: BLE001 - a flaky database must not block writes
            logger.warning(
                "plugin_sync_breaker_refresh_failed", error=f"{type(exc).__name__}: {exc}"[:300]
            )
            return
        for key, tripped in states.items():
            if tripped and key not in cls._tripped:
                cls._tripped.add(key)
                cls._last_attempt[key] = now
            elif not tripped and key in cls._tripped:
                cls.reset(key)


async def _persist_failure(key: str, plugin_id: str, error: str) -> None:
    try:
        async with get_session_factory()() as session:
            now = utc_now()
            hook = await ensure_hook(
                session,
                hook_key=key,
                plugin_id=plugin_id,
                kind=HOOK_KIND,
                initial_cursor=None,
                now=now,
            )
            await record_hook_failure(session, hook, error=error, now=now, trip_after=TRIP_AFTER)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - bookkeeping must never break a write
        logger.warning(
            "plugin_sync_hook_state_write_failed",
            hook=key,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


async def _persist_recovery(key: str) -> None:
    try:
        async with get_session_factory()() as session:
            await session.execute(
                update(TiqoraPluginHook)
                .where(TiqoraPluginHook.hook_key == key)
                .values(consecutive_failures=0, tripped_at=None, last_ok_at=utc_now())
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "plugin_sync_hook_state_write_failed",
            hook=key,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


async def ensure_sync_hook_rows(
    registry: Registry, factory: async_sessionmaker[AsyncSession]
) -> None:
    """One ``sync`` row per registered hook so the plugin page lists them (idempotent)."""
    hooks = registry.items(SYNC_HOOKS)
    if not hooks:
        return
    async with factory() as session:
        now = utc_now()
        for hook in hooks:
            await ensure_hook(
                session,
                hook_key=hook_key(hook),
                plugin_id=hook.plugin_id,
                kind=HOOK_KIND,
                initial_cursor=None,
                now=now,
            )
        await session.commit()


# --- running hooks ----------------------------------------------------------------------------

_NAME_TABLES: Final = frozenset({"ticket_priority", "ticket_type"})


def _db_name_exists(session: AsyncSession | None) -> NameExists:
    async def exists(table: str, name: str) -> bool:
        if table not in _NAME_TABLES:
            raise ValueError(table)
        sql = text(f"SELECT 1 FROM {table} WHERE name = :n AND valid_id = 1 LIMIT 1")  # noqa: S608
        if session is not None:
            return (await session.execute(sql, {"n": name})).first() is not None
        async with get_session_factory()() as own:
            return (await own.execute(sql, {"n": name})).first() is not None

    return exists


async def _validated_merge(
    op: TicketCreateOp, patch: TicketCreatePatch, name_exists: NameExists
) -> TicketCreateOp:
    """Apply a hook's patch to the create op; ``ValueError`` when the patch is not acceptable."""
    changes: dict[str, Any] = {}
    if patch.title is not None:
        title = patch.title.strip() if isinstance(patch.title, str) else ""
        if not title or len(title) > MAX_TITLE_LEN:
            raise ValueError(f"Ungültiger Titel (1 bis {MAX_TITLE_LEN} Zeichen)")
        changes["title"] = title
    for attr, table in (("priority", "ticket_priority"), ("type", "ticket_type")):
        value = getattr(patch, attr)
        if value is None:
            continue
        if not isinstance(value, str) or not await name_exists(table, value):
            raise ValueError(f"{attr} „{value}“ gibt es nicht")
        changes[attr] = value
    if patch.dynamic_fields:
        merged = dict(op.dynamic_fields)
        for name, values in patch.dynamic_fields.items():
            if not isinstance(name, str) or isinstance(values, str):
                raise ValueError("Ungültige Werte für dynamische Felder")
            merged[name] = tuple(str(v) for v in values)
        changes["dynamic_fields"] = merged
    return dataclasses.replace(op, **changes) if changes else op


def _denied(hook: SyncHook, why: str) -> OperationVetoed:
    if why == "tripped":
        message = f"Erweiterung „{hook.name}“ ist angehalten; Vorgang abgelehnt"
    else:
        message = f"Prüfung durch Erweiterung „{hook.name}“ fehlgeschlagen; Vorgang abgelehnt"
    return OperationVetoed(message, plugin_id=hook.plugin_id)


async def run_sync_hooks(
    operation: SyncOperation,
    op: Any,
    *,
    agent_id: int,
    registry: Registry | None = None,
    session: AsyncSession | None = None,
    name_exists: NameExists | None = None,
) -> Any:
    """Run the hooks of ``operation``; returns ``op`` (``ticket.create``: possibly patched).

    Raises :class:`OperationVetoed`. ``session`` (the caller's) is only used to check names a
    ``ticket.create`` patch introduces, so they are looked up in the same transaction.
    """
    reg = registry if registry is not None else get_registry()
    hooks = sorted(
        (h for h in reg.items(SYNC_HOOKS) if h.operation == operation and is_active(h.plugin_id)),
        key=lambda h: (h.order, h.key),
    )
    if not hooks:
        return op
    lookup = name_exists or _db_name_exists(session)
    await SyncBreaker.refresh_if_due()
    budget = PluginCallBudget(OPERATION_BUDGET_SECONDS)
    for hook in hooks:
        key = hook_key(hook)
        if SyncBreaker.is_tripped(key) and not SyncBreaker.claim_probe(key):
            if hook.on_error == "deny":
                raise _denied(hook, "tripped")
            continue
        op = await _run_one(hook, key, operation, op, agent_id, budget, lookup)
    return op


async def _run_one(
    hook: SyncHook,
    key: str,
    operation: SyncOperation,
    op: Any,
    agent_id: int,
    budget: PluginCallBudget,
    name_exists: NameExists,
) -> Any:
    ctx = HookContext(
        plugin=hook.context or minimal_context(hook.plugin_id),
        operation=operation,
        agent_id=agent_id,
    )

    async def invoke() -> tuple[TicketCreatePatch | None, OperationVetoed | None]:
        try:
            return await hook.handler(ctx, op), None
        except OperationVetoed as veto:  # a decision, not a failure
            return None, veto

    timeout = budget.timeout_for(hook.timeout_ms / 1000)
    error: str | None
    if timeout <= 0:
        error = "Zeitbudget für Prüfungen aufgebraucht"
        logger.warning("plugin_call_failed", plugin=hook.plugin_id, what=key, error=error)
    else:
        outcome = await call_plugin(hook.plugin_id, f"sync hook {key}", invoke, timeout=timeout)
        error = outcome.error if not outcome.ok else None
        if outcome.ok:
            assert outcome.value is not None
            patch, veto = outcome.value
            if veto is not None:
                if veto.plugin_id is None:
                    veto.plugin_id = hook.plugin_id
                raise veto
            try:
                op = await _apply_patch(hook, operation, op, patch, name_exists)
            except ValueError as exc:
                error = f"Ungültiges Ergebnis: {exc}"[:MAX_ERROR_LEN]
                logger.warning("plugin_call_failed", plugin=hook.plugin_id, what=key, error=error)
    if error is None:
        SyncBreaker.success(key, hook.plugin_id)
        return op
    SyncBreaker.failure(key, hook.plugin_id, error)
    if hook.on_error == "deny":
        raise _denied(hook, "failed")
    return op


async def _apply_patch(
    hook: SyncHook, operation: SyncOperation, op: Any, patch: Any, name_exists: NameExists
) -> Any:
    if patch is None:
        return op
    if not isinstance(patch, TicketCreatePatch):
        raise ValueError(f"unerwarteter Rückgabewert {type(patch).__name__}")
    if operation != "ticket.create":
        raise ValueError("Änderungen sind nur beim Anlegen eines Tickets erlaubt")
    return await _validated_merge(op, patch, name_exists)
