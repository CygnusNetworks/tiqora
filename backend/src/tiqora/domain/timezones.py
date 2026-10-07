"""Display time zones, resolved the way Znuny does.

Storage is always UTC (``OTRSTimeZone``). What a person *sees* follows, in
order: their own ``UserTimeZone`` preference, then the system-wide
``UserDefaultTimeZone``, then ``OTRSTimeZone``. Server-rendered text (reply
quote headers, template placeholders, notifications) uses the same chain for
whoever it is written for, because the backend cannot know a browser's zone.
"""

from __future__ import annotations

import functools
import zoneinfo
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from tiqora.domain.auth import decode_preference_value

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiqora.znuny.sysconfig import SysConfig

#: Znuny ``user_preferences`` key written by its "Time Zone" preference.
USER_TIME_ZONE_KEY = "UserTimeZone"


@functools.cache
def _known_zones() -> frozenset[str]:
    # ``available_timezones()`` scans the tz database on every call (~20 ms);
    # notifications validate a zone per recipient.
    return frozenset(zoneinfo.available_timezones() | {"UTC"})


def valid_time_zone(name: object) -> str | None:
    """Return *name* if it is a usable IANA zone, else ``None``."""
    if not isinstance(name, str):
        return None
    candidate = name.strip()
    if not candidate or candidate not in _known_zones():
        return None
    return candidate


async def user_time_zone_preference(session: AsyncSession, user_id: int) -> str | None:
    """The agent's own ``UserTimeZone`` preference, or ``None`` when unset/invalid."""
    raw = (
        await session.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id = :uid AND preferences_key = :k"
            ),
            {"uid": user_id, "k": USER_TIME_ZONE_KEY},
        )
    ).scalar_one_or_none()
    return valid_time_zone(decode_preference_value(raw))


async def default_time_zone(sysconfig: SysConfig) -> str:
    """System default for people without a preference (``UserDefaultTimeZone``)."""
    for name in ("UserDefaultTimeZone", "OTRSTimeZone"):
        zone = valid_time_zone(await sysconfig.get(name))
        if zone:
            return zone
    return "UTC"


async def resolve_user_time_zone(
    session: AsyncSession | None, sysconfig: SysConfig, user_id: int | None
) -> str:
    """Preference of *user_id* if set, otherwise the system default."""
    if session is not None and user_id:
        zone = await user_time_zone_preference(session, int(user_id))
        if zone:
            return zone
    return await default_time_zone(sysconfig)


def to_zone(value: datetime, zone: str) -> datetime:
    """Convert a DB datetime (naive = UTC) to wall-clock time in *zone*."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(zoneinfo.ZoneInfo(zone))


def format_in_zone(value: datetime, zone: str, fmt: str) -> str:
    """``strftime`` a DB datetime (naive = UTC) as wall-clock time in *zone*."""
    return to_zone(value, zone).strftime(fmt)
