"""Per-provider session lifetime: a provider can only shorten the global TTLs (S10)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tiqora.identity.types import SESSION_FLOOR_SECONDS

if TYPE_CHECKING:
    from tiqora.config import Settings
    from tiqora.identity.store import ProviderRecord

BORN_PREFIX = "i="


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def effective_lifetime(
    record: ProviderRecord,
    settings: Settings,
    *,
    not_on_or_after: datetime | None,
    now: datetime,
) -> tuple[int, int]:
    """``(idle_seconds, absolute_seconds)`` of a session created by *record*.

    Both values are at most the global ones; a SAML ``SessionNotOnOrAfter`` shortens the
    absolute lifetime further. Neither goes below ``SESSION_FLOOR_SECONDS``, and idle never
    exceeds absolute.
    """
    idle = settings.session_ttl_seconds
    if record.session_idle_seconds:
        idle = min(idle, record.session_idle_seconds)
    absolute = settings.session_absolute_ttl_seconds
    if record.session_absolute_seconds:
        absolute = min(absolute, record.session_absolute_seconds)
    if not_on_or_after is not None:
        remaining = (_aware(not_on_or_after) - _aware(now)).total_seconds()
        absolute = min(absolute, int(remaining))
    absolute = max(SESSION_FLOOR_SECONDS, absolute)
    idle = max(SESSION_FLOOR_SECONDS, min(idle, absolute))
    return idle, absolute


def born_value(idle: int) -> str:
    """Value of the (never renewed) born marker; it carries the idle TTL for ``touch``."""
    return f"{BORN_PREFIX}{idle}"


def idle_from_born(raw: object, default: int) -> int:
    """Idle TTL stored in a born marker; legacy ``"1"`` or garbage falls back to *default*."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str) and raw.startswith(BORN_PREFIX):
        digits = raw[len(BORN_PREFIX) :]
        if digits.isascii() and digits.isdigit():
            value = int(digits)
            if value >= SESSION_FLOOR_SECONDS:
                return min(value, default)
    return default
