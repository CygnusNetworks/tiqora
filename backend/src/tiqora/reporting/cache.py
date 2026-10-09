"""Result cache for report runs (Redis in production, any :class:`CacheBackend` in tests).

A key is per permission scope: it covers everything that changes the numbers
for one viewer — the EFFECTIVE definition (choices, grain and parameter values
applied), the viewer's queue-id set, whether archived tickets are included
(admins only), the internal ``extra_filter`` of the ``/stats`` wrappers, the
last snapshot date (it moves the stock dates), the near-miss percentage, the
window, the zone, the row cap and ``compare_previous``. All of them are
required arguments so no caller can forget one. Cache failures never fail a
report: errors are logged and treated as a miss.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from datetime import date
from typing import Any, Final, Protocol, cast

import structlog
from pydantic import ValidationError

from tiqora.config import Settings
from tiqora.filters import Filter
from tiqora.reporting.definition import TimeWindow
from tiqora.reporting.schemas import ReportResultOut

logger = structlog.get_logger(__name__)

CACHE_PREFIX: Final = "tiqora:report:v1:"


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ex: int) -> Any: ...


def _filter_digest(flt: Filter | None) -> str | None:
    if flt is None:
        return None
    payload = json.dumps(flt.model_dump(mode="json", exclude_none=True), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReportCache:
    def __init__(self, backend: CacheBackend | None, ttl_seconds: int) -> None:
        self._backend = backend
        self._ttl = ttl_seconds

    @staticmethod
    def key_for(
        *,
        definition_hash: str,
        queue_ids: Iterable[int],
        window: TimeWindow | None,
        time_zone: str,
        row_cap: int,
        compare_previous: bool,
        include_archived: bool,
        extra_filter: Filter | None,
        last_snapshot_date: date | None,
        near_miss_percent: int,
    ) -> str:
        """``CACHE_PREFIX`` + sha256 over every input that changes a viewer's result."""
        payload = {
            "definition": definition_hash,
            "queues": sorted({int(q) for q in queue_ids}),
            "window": None
            if window is None
            else [window.start_utc.isoformat(), window.end_utc.isoformat()],
            "zone": time_zone,
            "row_cap": row_cap,
            "compare_previous": compare_previous,
            "include_archived": include_archived,
            "extra_filter": _filter_digest(extra_filter),
            "last_snapshot_date": None
            if last_snapshot_date is None
            else last_snapshot_date.isoformat(),
            "near_miss_percent": near_miss_percent,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return CACHE_PREFIX + digest

    async def get(self, key: str) -> ReportResultOut | None:
        """The cached result (``cached=True``), or ``None`` on a miss or any backend error."""
        if self._backend is None or self._ttl <= 0:
            return None
        try:
            raw = await self._backend.get(key)
        except Exception as exc:  # noqa: BLE001 - a broken cache is a miss
            logger.warning("report_cache_get_failed", error=str(exc))
            return None
        if raw is None:
            return None
        try:
            result = ReportResultOut.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("report_cache_entry_invalid", error=str(exc))
            return None
        return result.model_copy(update={"cached": True})

    async def put(self, key: str, result: ReportResultOut) -> None:
        """Store ``result`` for the TTL; no-op without backend or with TTL 0."""
        if self._backend is None or self._ttl <= 0:
            return
        try:
            await self._backend.set(key, result.model_dump_json(), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001 - never fail a report over the cache
            logger.warning("report_cache_put_failed", error=str(exc))


class PurgeableBackend(Protocol):
    def scan_iter(
        self, match: str | None = None, count: int | None = None
    ) -> AsyncIterator[Any]: ...

    async def delete(self, *names: Any) -> Any: ...


async def purge_report_cache(backend: PurgeableBackend, *, batch: int = 500) -> int:
    """Delete every cached report result (all scopes); returns the number of keys.

    For data removals whose effect must not wait for the TTL, e.g. a GDPR
    erasure (cached rows/labels may hold the erased customer ids/names).
    Best effort: errors are logged, never raised.
    """
    deleted = 0
    pending: list[Any] = []
    try:
        async for key in backend.scan_iter(match=CACHE_PREFIX + "*", count=batch):
            pending.append(key)
            if len(pending) >= batch:
                await backend.delete(*pending)
                deleted += len(pending)
                pending = []
        if pending:
            await backend.delete(*pending)
            deleted += len(pending)
    except Exception as exc:  # noqa: BLE001 - entries still expire with their TTL
        logger.warning("report_cache_purge_failed", error=str(exc), deleted=deleted)
    return deleted


async def purge_report_cache_at(redis_url: str) -> int:
    """:func:`purge_report_cache` for code paths without a request (GDPR jobs)."""
    import redis.asyncio as redis

    try:
        client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    except Exception as exc:  # noqa: BLE001 - e.g. a malformed URL; never fail the caller
        logger.warning("report_cache_purge_failed", error=str(exc), deleted=0)
        return 0
    try:
        return await purge_report_cache(cast(PurgeableBackend, client))
    finally:
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("report_cache_purge_close_failed", error=str(exc))


def cache_backend_from_settings(settings: Settings) -> CacheBackend:
    import redis.asyncio as redis

    return cast(CacheBackend, redis.from_url(settings.redis_url, decode_responses=True))
