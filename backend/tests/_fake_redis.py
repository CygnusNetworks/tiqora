"""Shared in-memory Redis stand-in for the web-chat tests (no ``fakeredis`` dependency).

Supports the string/TTL commands the chat rate limiter needs, ``pipeline()`` (MULTI-style:
commands queue synchronously, ``execute()`` runs them in order), ``publish`` and a pub/sub
object with ``subscribe``/``psubscribe`` (fnmatch patterns). ``advance(seconds)`` moves the
fake clock so TTLs expire without sleeping; ``fail = True`` makes every command raise
``ConnectionError`` (fail-closed tests).
"""

from __future__ import annotations

import asyncio
import fnmatch
from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.fail = False
        self._now = 0.0
        self._data: dict[str, str] = {}
        self._expires: dict[str, float] = {}
        self._pubsubs: list[FakePubSub] = []

    # -- helpers ---------------------------------------------------------------------------
    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("fake redis is down")

    def _purge(self, key: str) -> None:
        exp = self._expires.get(key)
        if exp is not None and exp <= self._now:
            self._data.pop(key, None)
            self._expires.pop(key, None)

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def pattern_subscription_count(self) -> int:
        return sum(len(p._patterns) for p in self._pubsubs)

    def drop_ttl(self, key: str) -> None:
        """Test helper: simulate a crash between INCR and EXPIRE (key without TTL)."""
        self._expires.pop(key, None)

    # -- commands --------------------------------------------------------------------------
    async def get(self, key: str) -> str | None:
        self._check()
        self._purge(key)
        return self._data.get(key)

    async def set(
        self, key: str, value: Any, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        self._check()
        self._purge(key)
        if nx and key in self._data:
            return None
        self._data[key] = str(value)
        if ex is not None:
            self._expires[key] = self._now + ex
        else:
            self._expires.pop(key, None)
        return True

    async def delete(self, *keys: str) -> int:
        self._check()
        n = 0
        for key in keys:
            self._purge(key)
            if self._data.pop(key, None) is not None:
                n += 1
            self._expires.pop(key, None)
        return n

    async def exists(self, *keys: str) -> int:
        self._check()
        n = 0
        for key in keys:
            self._purge(key)
            n += key in self._data
        return n

    async def incr(self, key: str) -> int:
        self._check()
        self._purge(key)
        value = int(self._data.get(key, "0")) + 1
        self._data[key] = str(value)
        return value

    async def incrby(self, key: str, amount: int) -> int:
        self._check()
        self._purge(key)
        value = int(self._data.get(key, "0")) + int(amount)
        self._data[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self._check()
        self._purge(key)
        if key not in self._data:
            return False
        if nx and key in self._expires:
            return False
        self._expires[key] = self._now + seconds
        return True

    async def ttl(self, key: str) -> int:
        self._check()
        self._purge(key)
        if key not in self._data:
            return -2
        exp = self._expires.get(key)
        if exp is None:
            return -1
        return max(0, int(exp - self._now + 0.999))

    async def mget(self, *keys: str) -> list[str | None]:
        self._check()
        out: list[str | None] = []
        for key in keys:
            self._purge(key)
            out.append(self._data.get(key))
        return out

    async def keys(self, pattern: str = "*") -> list[str]:
        self._check()
        for key in list(self._data):
            self._purge(key)
        return [k for k in self._data if fnmatch.fnmatchcase(k, pattern)]

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        del transaction
        return FakePipeline(self)

    # -- pub/sub ---------------------------------------------------------------------------
    async def publish(self, channel: str, message: str) -> int:
        self._check()
        self.published.append((channel, message))
        delivered = 0
        for ps in list(self._pubsubs):
            delivered += ps._deliver(channel, message)
        return delivered

    def pubsub(self) -> FakePubSub:
        ps = FakePubSub(self)
        self._pubsubs.append(ps)
        return ps


class FakePipeline:
    """Queues commands synchronously (like redis-py), runs them in order on ``execute``."""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queue: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        if name not in {"get", "set", "delete", "exists", "incr", "incrby", "expire", "ttl"}:
            raise AttributeError(name)

        def _queue(*args: Any, **kwargs: Any) -> FakePipeline:
            self._queue.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self) -> list[Any]:
        self._client._check()
        queued, self._queue = self._queue, []
        return [await getattr(self._client, n)(*a, **kw) for n, a, kw in queued]

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._queue.clear()


class FakePubSub:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._channels: set[str] = set()
        self._patterns: set[str] = set()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _deliver(self, channel: str, message: str) -> int:
        n = 0
        if channel in self._channels:
            self._queue.put_nowait({"type": "message", "channel": channel, "data": message})
            n += 1
        for pattern in self._patterns:
            if fnmatch.fnmatchcase(channel, pattern):
                self._queue.put_nowait(
                    {"type": "pmessage", "pattern": pattern, "channel": channel, "data": message}
                )
                n += 1
        return n

    async def subscribe(self, *channels: str) -> None:
        self._client._check()
        self._channels.update(channels)

    async def psubscribe(self, *patterns: str) -> None:
        self._client._check()
        self._patterns.update(patterns)

    async def unsubscribe(self, *channels: str) -> None:
        self._channels.difference_update(channels or tuple(self._channels))

    async def punsubscribe(self, *patterns: str) -> None:
        self._patterns.difference_update(patterns or tuple(self._patterns))

    async def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,  # noqa: ASYNC109 - mirrors redis.asyncio.PubSub.get_message
    ) -> dict[str, Any] | None:
        del ignore_subscribe_messages
        self._client._check()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout or 0.01)
        except TimeoutError:
            return None

    async def aclose(self) -> None:
        self._channels.clear()
        self._patterns.clear()
        if self in self._client._pubsubs:
            self._client._pubsubs.remove(self)
