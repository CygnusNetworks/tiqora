"""Template placeholders from plugins (D6): ``<TIQORA_PLUGIN_<SHORTID>_<NAME>>``.

The tag is resolved by :func:`resolve_plugin_placeholder`, called from
``_resolve_tag_raw`` so the value passes the same choke point (secret masking, HTML escaping,
the Znuny ``-`` for empty values) as any core tag. A plugin can never break a template: an
unknown tag, an inactive plugin, a failing or slow resolver or a non-string result all render
as an empty string.

Every expansion is bounded (the composer and the template list must open quickly): each
resolver by its own timeout, one :func:`expand_placeholders` call by
:data:`EXPAND_BUDGET_SECONDS`, and callers that expand many texts in a row (template list,
notifications) share one budget through :func:`placeholder_budget_scope`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import PluginCallBudget, call_plugin
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from tiqora.channels.email.placeholder import PlaceholderContext

logger = structlog.get_logger(__name__)

__all__ = [
    "EXPAND_BUDGET_SECONDS",
    "MAX_VALUE_CHARS",
    "PLACEHOLDERS",
    "Placeholder",
    "PlaceholderRequest",
    "PlaceholderResolve",
    "placeholder_budget_scope",
    "resolve_plugin_placeholder",
]

MAX_VALUE_CHARS: Final = 10_000
#: all plugin placeholders of ONE ``expand_placeholders`` call together
EXPAND_BUDGET_SECONDS: Final = 2.0
#: bounds of one resolver's own timeout (milliseconds)
MIN_TIMEOUT_MS: Final = 50
MAX_TIMEOUT_MS: Final = 2000


@dataclass(frozen=True, slots=True)
class PlaceholderRequest:
    plugin: PluginContext
    ticket_id: int | None
    user_id: int | None
    recipient: Mapping[str, str]  # notification recipient map (lower-cased keys), else empty
    time_zone: str
    bracket: str | None  # the "[n]" suffix of the tag, if any


PlaceholderResolve = Callable[[PlaceholderRequest], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Placeholder:
    tag: str  # "PLUGIN_<SHORTID>_<NAME>" (upper)
    plugin_id: str
    resolve: PlaceholderResolve
    timeout_ms: int  # 50..2000
    context: PluginContext | None = None


PLACEHOLDERS: Final[ExtensionPoint[Placeholder]] = ExtensionPoint(
    key="core.templates.placeholders",
    item_key=lambda p: p.tag,
    description="Additional <TIQORA_PLUGIN_…> tags for templates, signatures and notifications.",
    sdk_stable=True,
)

_scope_budget: ContextVar[PluginCallBudget | None] = ContextVar(
    "plugin_placeholder_budget", default=None
)


def current_scope_budget() -> PluginCallBudget | None:
    """The budget of the enclosing :func:`placeholder_budget_scope`, if any."""
    return _scope_budget.get()


@contextlib.contextmanager
def placeholder_budget_scope(seconds: float) -> Iterator[PluginCallBudget]:
    """One shared budget for every plugin placeholder expanded inside the block.

    For callers that render many texts in a row (template list, notification rendering per
    recipient): without it each text would get its own budget. Nested scopes join the outer.
    """
    existing = _scope_budget.get()
    if existing is not None:
        yield existing
        return
    budget = PluginCallBudget(seconds)
    token = _scope_budget.set(budget)
    try:
        yield budget
    finally:
        _scope_budget.reset(token)


def _find(tag_upper: str) -> Placeholder | None:
    for item in get_registry().items(PLACEHOLDERS):
        if item.tag == tag_upper:
            return item
    return None


async def resolve_plugin_placeholder(
    tag_upper: str, bracket: str | None, ctx: PlaceholderContext
) -> str:
    """Value of ``PLUGIN_<SHORTID>_<NAME>`` or ``""`` (never raises, never blocks long).

    The result is memoised per ``(tag, bracket)`` on the context, so a tag used three times in
    a template calls the plugin once.
    """
    key = (tag_upper, bracket)
    if key in ctx.plugin_memo:
        return ctx.plugin_memo[key]
    value = await _resolve(tag_upper, bracket, ctx)
    ctx.plugin_memo[key] = value
    return value


async def _resolve(tag_upper: str, bracket: str | None, ctx: PlaceholderContext) -> str:
    item = _find(tag_upper)
    if item is None or not is_active(item.plugin_id):
        logger.debug("plugin_placeholder_unknown", tag=tag_upper)
        return ""
    budget = ctx.plugin_budget or current_scope_budget()
    timeout = item.timeout_ms / 1000
    if budget is not None:
        timeout = budget.timeout_for(timeout)
    if timeout <= 0:
        logger.warning(
            "plugin_placeholder_skipped",
            plugin=item.plugin_id,
            tag=item.tag,
            reason="budget_exhausted",
        )
        return ""
    request = PlaceholderRequest(
        plugin=item.context or minimal_context(item.plugin_id),
        ticket_id=ctx.ticket_id,
        user_id=ctx.user_id,
        recipient=dict(ctx.notification_recipient),
        time_zone=ctx.time_zone,
        bracket=bracket,
    )
    outcome = await call_plugin(
        item.plugin_id,
        f"placeholder {item.tag}",
        lambda: item.resolve(request),
        timeout=timeout,
    )
    if not outcome.ok or not isinstance(outcome.value, str):
        if outcome.ok:
            logger.warning(
                "plugin_placeholder_bad_value",
                plugin=item.plugin_id,
                tag=item.tag,
                got=type(outcome.value).__name__,
            )
        return ""
    return outcome.value[:MAX_VALUE_CHARS]
