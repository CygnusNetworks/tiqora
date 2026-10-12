"""Ticket number generators from plugins (D7).

An admin picks one generator (``tiqora_settings`` ``tickets.number_generator``); every new
ticket then gets its number from the plugin. A plugin can never stop a ticket from being
created: a generator that raises, hangs, returns something that is not a valid ticket number
or keeps returning an existing number makes :func:`ticket_number_build` fall through to the
Znuny generator configured in SysConfig.

A ticket number travels in mail headers and subjects, so core insists on
:data:`TN_RE` on top of the plugin's own pattern (header injection), and it wraps the pattern
for follow-up detection (``tiqora.znuny.followup``).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import call_plugin
from tiqora.plugins.context import PluginContext
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

__all__ = [
    "BUILD_TIMEOUT_SECONDS",
    "MAX_PATTERN_LEN",
    "MAX_PLUGIN_COLLISIONS",
    "SETTING_KEY",
    "TICKET_NUMBER_GENERATORS",
    "TN_MAX_LEN",
    "TN_RE",
    "NumberBuild",
    "NumberRequest",
    "TicketNumberGenerator",
    "active_generator",
    "active_generator_in",
    "anchored_pattern",
    "build_example",
    "build_with_plugin",
    "check_number",
    "plugin_tn_patterns",
]

SETTING_KEY: Final = "tickets.number_generator"  # "" / missing = the Znuny SysConfig generator
TN_MAX_LEN: Final = 50  # ticket.tn VARCHAR(50)
MAX_PATTERN_LEN: Final = 200
#: what core accepts as a ticket number, whatever the plugin's own pattern says
TN_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,49}")
#: a number build runs in a worker thread; this is how long a request waits for it (F24)
BUILD_TIMEOUT_SECONDS: Final = 1.0
#: after this many collisions with existing tickets the Znuny generator takes over (D7)
MAX_PLUGIN_COLLISIONS: Final = 3


@dataclass(frozen=True, slots=True)
class NumberRequest:
    counter: int | None  # ticket_number_counter value (None when uses_counter is False)
    system_id: str
    now_local: datetime  # aware, OTRSTimeZone
    offset: int  # collision-loop offset (0 on the first try)


NumberBuild = Callable[[NumberRequest], str]  # pure, sync


@dataclass(frozen=True, slots=True)
class TicketNumberGenerator:
    key: str  # qualified, e.g. "Plugin_points_Yearly"
    plugin_id: str
    label: str
    build: NumberBuild
    pattern: str  # regex for the WHOLE number; no capturing groups, <= 200 chars
    uses_counter: bool
    date_based: bool
    context: PluginContext | None = None


TICKET_NUMBER_GENERATORS: Final[ExtensionPoint[TicketNumberGenerator]] = ExtensionPoint(
    key="core.tickets.number_generators",
    item_key=lambda g: g.key,
    description="Ticket number schemes a plugin offers next to the Znuny generators.",
    sdk_stable=True,
)

_warned_unavailable: set[str] = set()


def anchored_pattern(pattern: str) -> str:
    """The capturing group follow-up detection puts after the ticket hook.

    The trailing boundary keeps ``\\d{4}`` from matching the first digits of a longer number;
    the leading boundary is the hook itself (a lookbehind there would reject a hook that ends
    in a letter and has no divider).
    """
    return rf"({pattern})(?![A-Za-z0-9])"


def check_number(gen: TicketNumberGenerator, value: object) -> str | None:
    """``None`` when ``value`` is a valid number of ``gen``, else the reason (no value in it)."""
    if not isinstance(value, str):
        return "not_a_string"
    if not value:
        return "empty"
    if len(value) > TN_MAX_LEN:
        return "too_long"
    if not TN_RE.fullmatch(value):
        return "bad_characters"
    if not re.fullmatch(gen.pattern, value):
        return "pattern_mismatch"
    return None


def _generators() -> list[TicketNumberGenerator]:
    return get_registry().items(TICKET_NUMBER_GENERATORS)


async def active_generator_in(session: AsyncSession) -> TicketNumberGenerator | None:
    """The selected plugin generator, if it is registered and its plugin is active."""
    registered = _generators()
    if not registered:  # no plugin offers one: no database read on the hot path
        return None
    from tiqora.domain.settings_store import get_setting

    key = ((await get_setting(session, SETTING_KEY)) or "").strip()
    if not key:
        return None
    gen = next((g for g in registered if g.key == key), None)
    if gen is None or not is_active(gen.plugin_id):
        if key not in _warned_unavailable:
            _warned_unavailable.add(key)
            logger.warning("plugin_number_generator_unavailable", generator=key)
        return None
    return gen


async def active_generator(
    session_factory: async_sessionmaker[AsyncSession],
) -> TicketNumberGenerator | None:
    """:func:`active_generator_in` with a fresh session."""
    if not _generators():
        return None
    async with session_factory() as session:
        return await active_generator_in(session)


async def plugin_tn_patterns(session: AsyncSession) -> tuple[str, ...]:
    """Pattern of the SELECTED generator (empty when none): other schemes are not followed."""
    gen = await active_generator_in(session)
    return (gen.pattern,) if gen is not None else ()


async def _call_build(gen: TicketNumberGenerator, request: NumberRequest) -> tuple[bool, object]:
    outcome = await call_plugin(
        gen.plugin_id,
        f"number generator {gen.key}",
        lambda: asyncio.to_thread(gen.build, request),
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    return outcome.ok, outcome.value


async def build_with_plugin(
    gen: TicketNumberGenerator,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    offset: int,
) -> str | None:
    """One number from ``gen``, or ``None`` (logged) if the plugin failed or built garbage."""
    from tiqora.znuny.ticket_number import _tzinfo, ticket_number_counter_add

    tz = await sysconfig.otrs_time_zone()
    counter: int | None = None
    if gen.uses_counter:
        counter = await ticket_number_counter_add(
            session_factory, offset=1 + offset, is_date_based=gen.date_based, tz=tz
        )
    request = NumberRequest(
        counter=counter,
        system_id=await sysconfig.system_id(),
        now_local=datetime.now(_tzinfo(tz)),
        offset=offset,
    )
    ok, value = await _call_build(gen, request)
    reason = "call_failed" if not ok else check_number(gen, value)
    if reason is not None:
        logger.warning(
            "plugin_ticket_number_invalid", plugin=gen.plugin_id, generator=gen.key, reason=reason
        )
        return None
    assert isinstance(value, str)
    return value


async def build_example(
    gen: TicketNumberGenerator, *, system_id: str = "10", now_local: datetime | None = None
) -> str | None:
    """What ``gen`` would build with counter 1 today; ``None`` when it fails (admin display)."""
    request = NumberRequest(
        counter=1 if gen.uses_counter else None,
        system_id=system_id,
        now_local=now_local or datetime.now(UTC),
        offset=0,
    )
    ok, value = await _call_build(gen, request)
    if not ok or check_number(gen, value) is not None:
        return None
    assert isinstance(value, str)
    return value
