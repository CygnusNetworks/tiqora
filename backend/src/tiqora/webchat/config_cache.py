"""In-process cache of the web-chat master switch and config rows for the public API (R-PREAUTH).

The frame and ``GET /config`` are loaded on every host-page view, so the public endpoints read
the channel switch and the config row from here (TTL ``CONFIG_CACHE_SECONDS``) instead of the
DB. Unknown keys are cached too (as ``None``), so random keys do not cost a lookup each; the
number of entries is bounded.

Every admin write that changes what this cache holds calls :func:`clear_config_cache` in the
same process (``PUT /admin/channels/webchat``, chat-config create/update/delete/rotate-key) —
a change made elsewhere (direct DB edit, another API replica) applies after at most 10 s.

Entries hold a detached copy of the row with the columns the services read (``id``, ``name``,
``public_key``, ``valid``) plus the typed values; nothing here is ever added to a session.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import channel_enabled
from tiqora.db.tiqora.models import TiqoraChatConfig
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.store import config_values, load_config, load_config_by_key

CONFIG_CACHE_SECONDS: Final = 10.0
MAX_ENTRIES: Final = 2000
_CHANNEL: Final = "webchat"


@dataclass(frozen=True, slots=True)
class CachedConfig:
    config: TiqoraChatConfig
    values: ChatConfigValues


def _snapshot(row: TiqoraChatConfig) -> CachedConfig:
    copy = TiqoraChatConfig(id=row.id, name=row.name, public_key=row.public_key, valid=row.valid)
    return CachedConfig(config=copy, values=config_values(row))


class PublicConfigCache:
    def __init__(
        self, ttl: float = CONFIG_CACHE_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._enabled: tuple[float, bool] | None = None
        self._entries: dict[str, tuple[float, CachedConfig | None]] = {}

    def clear(self) -> None:
        self._enabled = None
        self._entries.clear()

    def _get(self, key: str) -> tuple[bool, CachedConfig | None]:
        hit = self._entries.get(key)
        if hit is None:
            return False, None
        if hit[0] <= self._clock():
            self._entries.pop(key, None)
            return False, None
        return True, hit[1]

    def _put(self, key: str, value: CachedConfig | None) -> None:
        now = self._clock()
        if len(self._entries) >= MAX_ENTRIES:
            for k in [k for k, (exp, _) in self._entries.items() if exp <= now]:
                del self._entries[k]
        if len(self._entries) >= MAX_ENTRIES:
            # Unknown-key probes must not evict the live configs.
            for k in [k for k, (_, v) in self._entries.items() if v is None]:
                del self._entries[k]
            if len(self._entries) >= MAX_ENTRIES:
                self._entries.clear()
        self._entries[key] = (now + self._ttl, value)

    async def channel_enabled(self, db: AsyncSession) -> bool:
        if self._enabled is not None and self._enabled[0] > self._clock():
            return self._enabled[1]
        enabled = await channel_enabled(db, _CHANNEL)
        self._enabled = (self._clock() + self._ttl, enabled)
        return enabled

    async def by_key(self, db: AsyncSession, public_key: str) -> CachedConfig | None:
        """The VALID config of *public_key* (``None``: unknown or switched off)."""
        found, value = self._get("k:" + public_key)
        if found:
            return value
        row = await load_config_by_key(db, public_key)
        value = _snapshot(row) if row is not None else None
        self._put("k:" + public_key, value)
        return value

    async def by_id(self, db: AsyncSession, config_id: int) -> CachedConfig | None:
        """The config of an existing session, also when it was switched off since."""
        key = f"i:{config_id}"
        found, value = self._get(key)
        if found:
            return value
        row = await load_config(db, config_id)
        value = _snapshot(row) if row is not None else None
        self._put(key, value)
        return value


public_config_cache: Final = PublicConfigCache()


def clear_config_cache() -> None:
    """Drop every cached switch/config at once (admin writes, tests)."""
    public_config_cache.clear()
