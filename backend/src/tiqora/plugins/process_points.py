"""Process transition actions and condition modules from plugins (D5).

A plugin action runs inside the engine's session. An ordinary failure rolls back a savepoint
and the transition continues (Znuny logs a failing TransitionAction and goes on); a timeout
cannot be contained that way (an async connection is not cancellation-safe in the middle of a
statement), so it raises :class:`PluginActionTimedOut` and the request stores nothing.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.plugins.calls import PluginCallBudget, call_plugin, call_plugin_sync
from tiqora.plugins.context import PluginContext
from tiqora.plugins.state import is_active
from tiqora.plugins.sync_hooks import OperationVetoed
from tiqora.plugins.writes import PluginWriteError
from tiqora.process.actions.registry import (
    ActionHandler,
    ActionSkipped,
    TransitionActionModule,
)
from tiqora.process.condition_modules import (
    ConditionDataValue,
    ConditionModule,
    ConditionModuleFn,
)
from tiqora.process.exceptions import PluginActionTimedOut
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

__all__ = [
    "ACTION_BUDGET_SECONDS",
    "ActionContext",
    "ConditionRun",
    "PluginActionTimedOut",
    "TransitionActionRun",
    "abort_on_action_timeout",
    "action_budget_scope",
    "make_action_adapter",
    "make_condition_adapter",
]

#: all plugin actions of one dialog submit together (nginx cuts /api/ after 90 s)
ACTION_BUDGET_SECONDS: Final = 20.0
#: bounds of one action's own timeout
MIN_ACTION_TIMEOUT: Final = 1
MAX_ACTION_TIMEOUT: Final = 10


@dataclass(frozen=True, slots=True)
class ActionContext:
    plugin: PluginContext
    ticket_id: int
    user_id: int
    config: Mapping[str, Any]  # smart tags already replaced
    #: the engine's session: pass it only to ``ctx.plugin.tickets.*(..., session=ac.session)``
    session: AsyncSession


TransitionActionRun = Callable[[ActionContext], Awaitable[None]]
ConditionRun = Callable[[Mapping[str, str | list[str] | None], str], bool]

_budget: ContextVar[PluginCallBudget | None] = ContextVar("plugin_action_budget", default=None)


@contextlib.contextmanager
def action_budget_scope() -> Iterator[PluginCallBudget]:
    """One shared budget for every plugin action of one transition run (one dialog submit).

    Nested scopes join the outer one.
    """
    existing = _budget.get()
    if existing is not None:
        yield existing
        return
    budget = PluginCallBudget(ACTION_BUDGET_SECONDS)
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def _timed_out_message(plugin_name: str) -> str:
    return (
        f"Aktion der Erweiterung „{plugin_name}“ hat zu lange gebraucht; nichts wurde gespeichert"
    )


async def abort_on_action_timeout(
    session: AsyncSession, exc: PluginActionTimedOut
) -> HTTPException:
    """Route-side answer to a timed-out plugin action: the request stores nothing.

    The transaction is rolled back (the route's ``session.begin()`` block already did that on
    the way out) and the session's connection is invalidated: an async connection that was
    cancelled in the middle of a statement must not return to the pool.
    """
    with contextlib.suppress(Exception):
        await session.rollback()
    with contextlib.suppress(Exception):
        await session.invalidate()
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def make_action_adapter(
    *,
    plugin_id: str,
    plugin_name: str,
    qualified_name: str,
    run: TransitionActionRun,
    context: PluginContext,
    timeout_seconds: int,
) -> ActionHandler:
    async def adapter(
        session: AsyncSession,
        config: dict[str, Any],
        ticket_id: int,
        user_id: int,
        sysconfig: SysConfig,
    ) -> None:
        del sysconfig
        if not is_active(plugin_id):
            logger.warning(
                "plugin_transition_action_skipped",
                plugin=plugin_id,
                action=qualified_name,
                reason="inactive",
            )
            return
        budget = _budget.get() or PluginCallBudget(ACTION_BUDGET_SECONDS)
        timeout = budget.timeout_for(float(timeout_seconds))
        if timeout <= 0:
            logger.warning(
                "plugin_transition_action_failed",
                plugin=plugin_id,
                action=qualified_name,
                error="Zeitbudget der Aktionen erschöpft",
                timed_out=True,
            )
            raise PluginActionTimedOut(_timed_out_message(plugin_name))
        ac = ActionContext(
            plugin=context,
            ticket_id=ticket_id,
            user_id=user_id,
            config=dict(config),
            session=session,
        )
        savepoint = await session.begin_nested()
        refused: list[str] = []

        async def guarded() -> None:
            # a plugin write that a before-hook vetoed (or that names something unknown) is not
            # a plugin crash: the engine records it as a skipped action (reason ``vetoed``)
            try:
                await run(ac)
            except (OperationVetoed, PluginWriteError) as exc:
                refused.append(str(exc))
                raise

        outcome = await call_plugin(
            plugin_id, f"transition_action {qualified_name}", guarded, timeout=timeout
        )
        if outcome.timed_out:
            # Do not touch the connection: it may be in the middle of a cancelled statement.
            logger.warning(
                "plugin_transition_action_failed",
                plugin=plugin_id,
                action=qualified_name,
                error=outcome.error,
                timed_out=True,
            )
            raise PluginActionTimedOut(_timed_out_message(plugin_name))
        if outcome.ok:
            await savepoint.commit()
            return
        await savepoint.rollback()
        if refused:
            raise ActionSkipped("vetoed", refused[0])
        logger.warning(
            "plugin_transition_action_failed",
            plugin=plugin_id,
            action=qualified_name,
            error=outcome.error,
            timed_out=False,
        )

    return adapter


def make_condition_adapter(
    *, plugin_id: str, qualified_match: str, run: ConditionRun
) -> ConditionModuleFn:
    def adapter(data: Mapping[str, ConditionDataValue], field: str) -> bool:
        if not is_active(plugin_id):
            return False

        def call() -> bool:
            result: object = run(data, field)
            if not isinstance(result, bool):
                raise TypeError(f"Bedingung muss bool liefern, nicht {type(result).__name__}")
            return result

        outcome = call_plugin_sync(plugin_id, f"condition_module {qualified_match}", call)
        return outcome.ok and outcome.value is True

    return adapter


def stage_action(
    *, qualified_name: str, adapter: ActionHandler, params: tuple[Any, ...]
) -> TransitionActionModule:
    return TransitionActionModule(name=qualified_name, run=adapter, params=params, tiqora_only=True)


def stage_condition(*, match: str, adapter: ConditionModuleFn) -> ConditionModule:
    return ConditionModule(match=match, validate=adapter, tiqora_only=True)
