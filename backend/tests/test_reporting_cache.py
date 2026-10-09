"""Report result cache: keys per permission scope, round trip, failure tolerance (pure)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from tiqora.filters import Filter
from tiqora.reporting.cache import (
    CACHE_PREFIX,
    GENERATION_KEY,
    ReportCache,
    purge_report_cache,
    purge_report_cache_at,
    read_cache_generation,
)
from tiqora.reporting.definition import TimeWindow
from tiqora.reporting.schemas import (
    FactsStatusOut,
    ReportResultOut,
    ReportTotalsOut,
    ResultColumnOut,
    TimeWindowOut,
)


class _FakeBackend:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value
        self.ttls[key] = ex


class _BrokenBackend:
    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int) -> None:
        raise ConnectionError("redis down")


def _key(**over: Any) -> str:
    args: dict[str, Any] = {
        "definition_hash": "h",
        "queue_ids": [1, 2],
        "window": None,
        "time_zone": "UTC",
        "row_cap": 10,
        "compare_previous": False,
        "include_archived": False,
        "extra_filter": None,
        "last_snapshot_date": None,
        "near_miss_percent": 10,
    }
    args.update(over)
    return ReportCache.key_for(**args)


def _flt(field: str, value: Any) -> Filter:
    return Filter.model_validate(
        {"version": 1, "where": {"field": field, "op": "eq", "value": value}}
    )


def _result() -> ReportResultOut:
    return ReportResultOut(
        report_key="builtin.x",
        columns=[
            ResultColumnOut(key="queue", role="dimension", type="id", label_key="d"),
            ResultColumnOut(key="reopen_rate", role="measure", type="percent", label_key="m"),
        ],
        rows=[[7, 0.25], ["__other__", None]],
        labels={"queue": {"7": "rep47-queue"}},
        totals=ReportTotalsOut(grand=[None, 0.5]),
        previous=None,
        row_count=2,
        truncated=False,
        time_window=TimeWindowOut(
            start=datetime(2024, 6, 1),
            end=datetime(2024, 7, 1),
            start_date=date(2024, 6, 1),
            end_date=date(2024, 6, 30),
            time_zone="UTC",
        ),
        generated_at=datetime(2024, 6, 20, 12, 0),
        cached=False,
        facts=FactsStatusOut(
            ready=True,
            as_of=None,
            backfill_status="done",
            backfill_job_id=None,
            backfill_progress=None,
        ),
    )


def test_cache_key_differs_per_scope() -> None:
    k1 = _key(queue_ids=[1, 2])
    k2 = _key(queue_ids=[1])
    assert k1 != k2 and k1.startswith(CACHE_PREFIX)
    assert k1 == _key(queue_ids=[2, 1])


def test_cache_key_covers_every_result_input() -> None:
    """Each input that changes a viewer's numbers gives its own entry (Task 12 hand-off)."""
    base = _key()
    window = TimeWindow(
        datetime(2024, 6, 1), datetime(2024, 7, 1), date(2024, 6, 1), date(2024, 6, 30), "UTC"
    )
    variants = {
        "definition": _key(definition_hash="other"),
        "window": _key(window=window),
        "zone": _key(time_zone="Europe/Berlin"),
        "row_cap": _key(row_cap=11),
        "compare_previous": _key(compare_previous=True),
        "include_archived": _key(include_archived=True),
        "extra_filter": _key(extra_filter=_flt("state_type", "closed")),
        "last_snapshot_date": _key(last_snapshot_date=date(2024, 6, 19)),
        "near_miss_percent": _key(near_miss_percent=20),
    }
    assert base not in variants.values()
    assert len(set(variants.values())) == len(variants)
    # different extra filters never collide; the same filter is stable
    assert _key(extra_filter=_flt("priority", 3)) != _key(extra_filter=_flt("priority", 4))
    assert _key(extra_filter=_flt("priority", 3)) == _key(extra_filter=_flt("priority", 3))


def test_key_for_requires_the_scope_inputs() -> None:
    """No default for include_archived & co.: a caller cannot forget them silently."""
    with pytest.raises(TypeError):
        ReportCache.key_for(  # type: ignore[call-arg]
            definition_hash="h",
            queue_ids=[1],
            window=None,
            time_zone="UTC",
            row_cap=10,
            compare_previous=False,
        )


async def test_round_trip_and_ttl_zero() -> None:
    backend = _FakeBackend()
    cache = ReportCache(backend, ttl_seconds=300)
    result = _result()
    await cache.put("k", result)
    assert backend.ttls == {"k:g0": 300}  # generation 0: never purged
    hit = await cache.get("k")
    assert hit is not None and hit.cached is True
    assert hit.model_dump(mode="json", exclude={"cached"}) == result.model_dump(
        mode="json", exclude={"cached"}
    )
    assert hit.rows == [[7, 0.25], ["__other__", None]]  # int stays int, float stays float
    assert await cache.get("missing") is None

    off = ReportCache(_FakeBackend(), ttl_seconds=0)
    await off.put("k", result)
    assert await off.get("k") is None
    assert await ReportCache(None, ttl_seconds=300).get("k") is None
    await ReportCache(None, ttl_seconds=300).put("k", result)  # no backend: no-op


async def test_backend_errors_are_swallowed() -> None:
    cache = ReportCache(_BrokenBackend(), ttl_seconds=300)
    assert await cache.get("k") is None
    await cache.put("k", _result())  # logged, never raised


async def test_corrupt_entry_is_a_miss() -> None:
    backend = _FakeBackend()
    backend.store["k"] = '{"not": "a result"}'
    assert await ReportCache(backend, ttl_seconds=300).get("k") is None


class _ScanBackend:
    """scan_iter/delete like redis.asyncio, with a fnmatch filter."""

    def __init__(self, keys: list[str], fail: bool = False) -> None:
        self.store = dict.fromkeys(keys, "x")
        self.fail = fail
        self.deletes: list[int] = []

    async def scan_iter(self, match: str | None = None, count: int | None = None) -> Any:
        import fnmatch

        if self.fail:
            raise ConnectionError("redis down")
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, *names: str) -> int:
        self.deletes.append(len(names))
        for name in names:
            self.store.pop(name, None)
        return len(names)

    async def incr(self, name: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        value = int(self.store.get(name, "0")) + 1
        self.store[name] = str(value)
        return value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


async def test_purge_report_cache_drops_only_report_entries() -> None:
    reports = [f"{CACHE_PREFIX}{i}" for i in range(5)]
    backend = _ScanBackend([*reports, "tiqora:session:abc", "tiqora:avatar:x"])
    assert await purge_report_cache(backend, batch=2) == 5
    assert set(backend.store) == {"tiqora:session:abc", "tiqora:avatar:x", GENERATION_KEY}
    assert backend.deletes == [2, 2, 1]  # batched
    assert await purge_report_cache(_ScanBackend(reports, fail=True)) == 0  # logged, not raised


async def test_purge_report_cache_at_never_raises() -> None:
    """A GDPR erasure must not fail because Redis is down or misconfigured."""
    assert await purge_report_cache_at("redis://127.0.0.1:1/0") == 0  # connection refused
    assert await purge_report_cache_at("not-a-redis-url") == 0  # rejected by from_url


async def test_purge_bumps_the_generation_so_in_flight_runs_cannot_recache() -> None:
    """T13 M2: a run that read the generation before a purge stores where nobody reads."""
    backend = _ScanBackend([])
    result = _result()
    before = ReportCache(backend, ttl_seconds=300, generation=await read_cache_generation(backend))
    assert await before.get("k") is None  # a run starts (generation 0) ...
    assert await purge_report_cache(backend) == 0  # ... a GDPR erasure purges meanwhile ...
    await before.put("k", result)  # ... and the run stores its (pre-erasure) numbers
    assert backend.store["k:g0"]  # stored, but under the old generation
    after = ReportCache(backend, ttl_seconds=300)  # every later request (lazy read)
    assert await after.get("k") is None
    await after.put("k", result)
    assert (await after.get("k")) is not None and "k:g1" in backend.store
    assert await read_cache_generation(backend) == 1
    # the generation counter itself survives purges (it is outside CACHE_PREFIX)
    await purge_report_cache(backend)
    assert backend.store[GENERATION_KEY] == "2"
    assert "k:g1" in backend.store  # the toy key "k" lies outside CACHE_PREFIX
    assert not GENERATION_KEY.startswith(CACHE_PREFIX)


async def test_unreadable_generation_turns_the_cache_off() -> None:
    assert await read_cache_generation(_BrokenBackend()) is None
    assert await read_cache_generation(None) is None
    backend = _FakeBackend()
    backend.store[GENERATION_KEY] = "garbage"
    cache = ReportCache(backend, ttl_seconds=300)
    await cache.put("k", _result())
    assert await cache.get("k") is None and set(backend.store) == {GENERATION_KEY}
    off = ReportCache(backend, ttl_seconds=300, generation=None)
    await off.put("k", _result())
    assert set(backend.store) == {GENERATION_KEY}
