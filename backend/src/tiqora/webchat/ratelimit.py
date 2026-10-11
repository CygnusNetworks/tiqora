"""Fixed-window rate limiter for the public web-chat API (Redis, FAIL CLOSED).

Keys are ``tiqora:chat:rl:<scope>:<subject>``; subjects are session ids or ``hash_ip``
digests — never raw addresses. Any Redis error becomes :class:`ChatBackendUnavailable`
(the API layer answers 503 ``chat_unavailable``): chat needs Redis for fan-out anyway, and a
limiter that silently allows on errors would turn a Redis outage into an open door.
"""

from __future__ import annotations

from typing import Any, Final

# R-PREAUTH: per-IP-hash backstop checked before any DB access on every public endpoint.
PUBLIC_BACKSTOP_SCOPE: Final = "public"
PUBLIC_BACKSTOP_LIMIT: Final = 300
PUBLIC_BACKSTOP_WINDOW: Final = 60
# R-PREAUTH: token failures per IP hash.
AUTH_FAIL_SCOPE: Final = "auth_fail"
AUTH_FAIL_LIMIT: Final = 60
AUTH_FAIL_WINDOW: Final = 600

_KEY_PREFIX: Final = "tiqora:chat:rl:"


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(f"rate limited: {scope}")
        self.scope = scope
        self.retry_after = retry_after


class ChatBackendUnavailable(Exception):
    """Redis could not be reached; callers must deny (503), never allow."""


class ChatRateLimiter:
    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _key(scope: str, subject: str) -> str:
        return f"{_KEY_PREFIX}{scope}:{subject}"

    async def hit(self, scope: str, subject: str, *, limit: int, window_seconds: int) -> None:
        """Count one hit; raise :class:`RateLimited` once more than ``limit`` hit the window.

        INCR and TTL run in one MULTI pipeline. A key without a TTL (first hit, or a crash
        between INCR and EXPIRE on an earlier hit) gets its expiry set right here, so a
        counter can never become permanent (R-RL-ATOMIC).
        """
        key = self._key(scope, subject)
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.ttl(key)
                count, ttl = await pipe.execute()
            if ttl is None or int(ttl) < 0:
                await self._client.expire(key, window_seconds)
                ttl = window_seconds
        except Exception as exc:  # noqa: BLE001 - fail closed on every backend error
            raise ChatBackendUnavailable(str(exc)) from exc
        if int(count) > limit:
            raise RateLimited(scope, max(1, int(ttl)))

    async def check(self, scope: str, subject: str, *, limit: int, window_seconds: int) -> None:
        """Raise :class:`RateLimited` when the window already holds ``limit`` hits — without
        counting this call. Lets a caller refuse BEFORE expensive work (e.g. the ``auth_fail``
        bucket before a session lookup). Redis errors → :class:`ChatBackendUnavailable`."""
        key = self._key(scope, subject)
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.get(key)
                pipe.ttl(key)
                raw, ttl = await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - fail closed on every backend error
            raise ChatBackendUnavailable(str(exc)) from exc
        if raw is not None and ttl is not None and int(ttl) == -1:
            # A counter without expiry would refuse forever once full (R-RL-ATOMIC); heal it.
            try:
                await self._client.expire(key, window_seconds)
            except Exception as exc:  # noqa: BLE001 - fail closed on every backend error
                raise ChatBackendUnavailable(str(exc)) from exc
            ttl = window_seconds
        if raw is not None and int(raw) >= limit:
            raise RateLimited(scope, max(1, int(ttl or 0)))

    async def try_hit(self, scope: str, subject: str, *, window_seconds: int) -> bool:
        """True when this is the first call in the window (``SET NX EX``); Redis errors → False."""
        try:
            return bool(
                await self._client.set(self._key(scope, subject), "1", ex=window_seconds, nx=True)
            )
        except Exception:  # noqa: BLE001 - best-effort throttle, deny on error
            return False


async def check_public_backstop(limiter: ChatRateLimiter, ip_hash: str) -> None:
    """The per-IP-hash ``public`` bucket (300/min) every public endpoint spends first."""
    await limiter.hit(
        PUBLIC_BACKSTOP_SCOPE,
        ip_hash,
        limit=PUBLIC_BACKSTOP_LIMIT,
        window_seconds=PUBLIC_BACKSTOP_WINDOW,
    )
