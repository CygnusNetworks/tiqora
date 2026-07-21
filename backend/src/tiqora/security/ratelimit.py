"""Redis-backed sliding-window rate limit + temporary lockout for password auth.

Applied to agent login, portal login, and compat SessionCreate / UserLogin+Password
paths (SECURITY M-7 / H-01). Counts **failed** attempts only; success resets the
per-login counters. Disable with ``TIQORA_AUTH_RATE_LIMIT_ENABLED=0`` for tests.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

import structlog

from tiqora.config import Settings

logger = structlog.get_logger(__name__)

_FAIL_LOGIN_PREFIX = "tiqora:auth:fail:login:"
_FAIL_IP_PREFIX = "tiqora:auth:fail:ip:"
_LOCK_LOGIN_PREFIX = "tiqora:auth:lock:login:"
_LOCK_IP_PREFIX = "tiqora:auth:lock:ip:"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of a pre-auth check."""

    allowed: bool
    retry_after: int = 0
    reason: str = ""


class AuthRateLimiter:
    """Per-login and per-IP failed-attempt throttle with temporary lockout."""

    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._enabled = settings.auth_rate_limit_enabled
        self._login_max = max(1, settings.auth_rate_limit_login_max)
        self._ip_max = max(1, settings.auth_rate_limit_ip_max)
        self._window = max(1, settings.auth_rate_limit_window_seconds)
        self._lockout = max(1, settings.auth_rate_limit_lockout_seconds)

    def _norm_login(self, login: str) -> str:
        return (login or "").strip().lower()

    def _norm_ip(self, ip: str) -> str:
        return (ip or "").strip() or "unknown"

    async def check(self, *, login: str, ip: str) -> RateLimitDecision:
        """Return whether a password-auth attempt may proceed."""
        if not self._enabled:
            return RateLimitDecision(allowed=True)

        login_n = self._norm_login(login)
        ip_n = self._norm_ip(ip)

        for key, reason in (
            (f"{_LOCK_LOGIN_PREFIX}{login_n}", "login_lockout") if login_n else (None, ""),
            (f"{_LOCK_IP_PREFIX}{ip_n}", "ip_lockout"),
        ):
            if key is None:
                continue
            ttl = await self._ttl(key)
            if ttl > 0:
                return RateLimitDecision(allowed=False, retry_after=ttl, reason=reason)

        return RateLimitDecision(allowed=True)

    async def record_failure(self, *, login: str, ip: str) -> RateLimitDecision | None:
        """Record a failed password attempt; return a lockout decision if one triggered.

        Returns ``None`` when still under the limit (caller continues with 401).
        """
        if not self._enabled:
            return None

        login_n = self._norm_login(login)
        ip_n = self._norm_ip(ip)
        now = time.time()

        locked: RateLimitDecision | None = None

        if login_n:
            login_count = await self._sliding_incr(f"{_FAIL_LOGIN_PREFIX}{login_n}", now)
            if login_count >= self._login_max:
                await self._set_lock(f"{_LOCK_LOGIN_PREFIX}{login_n}")
                logger.warning(
                    "auth_lockout",
                    scope="login",
                    login=login_n,
                    failures=login_count,
                    window_seconds=self._window,
                    lockout_seconds=self._lockout,
                    ip=ip_n,
                )
                locked = RateLimitDecision(
                    allowed=False,
                    retry_after=self._lockout,
                    reason="login_lockout",
                )

        ip_count = await self._sliding_incr(f"{_FAIL_IP_PREFIX}{ip_n}", now)
        if ip_count >= self._ip_max:
            await self._set_lock(f"{_LOCK_IP_PREFIX}{ip_n}")
            logger.warning(
                "auth_lockout",
                scope="ip",
                ip=ip_n,
                failures=ip_count,
                window_seconds=self._window,
                lockout_seconds=self._lockout,
                login=login_n or None,
            )
            if locked is None:
                locked = RateLimitDecision(
                    allowed=False,
                    retry_after=self._lockout,
                    reason="ip_lockout",
                )

        return locked

    async def reset(self, *, login: str, ip: str | None = None) -> None:
        """Clear fail counters (and lock) after a successful authentication."""
        if not self._enabled:
            return
        login_n = self._norm_login(login)
        keys: list[str] = []
        if login_n:
            keys.extend(
                [
                    f"{_FAIL_LOGIN_PREFIX}{login_n}",
                    f"{_LOCK_LOGIN_PREFIX}{login_n}",
                ]
            )
        if ip is not None:
            ip_n = self._norm_ip(ip)
            keys.extend(
                [
                    f"{_FAIL_IP_PREFIX}{ip_n}",
                    f"{_LOCK_IP_PREFIX}{ip_n}",
                ]
            )
        if keys:
            await self._client.delete(*keys)

    async def _sliding_incr(self, key: str, now: float) -> int:
        """Add a failure at *now* and return count within the sliding window."""
        window_start = now - self._window
        member = f"{now:.6f}:{secrets.token_hex(4)}"
        # Unique member per call — score carries the timestamp.
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, self._window + self._lockout)
        results = await pipe.execute()
        # results: [removed, added, count, expire_ok]
        count = int(results[2])
        return count

    async def _set_lock(self, key: str) -> None:
        await self._client.set(key, "1", ex=self._lockout)

    async def _ttl(self, key: str) -> int:
        raw = await self._client.ttl(key)
        if raw is None:
            return 0
        try:
            ttl = int(raw)
        except (TypeError, ValueError):
            return 0
        # Redis: -2 missing, -1 no expiry. Treat present-without-ttl as lockout window.
        if ttl == -1:
            return self._lockout
        if ttl < 0:
            return 0
        return ttl


def client_ip(request: Any) -> str:
    """Best-effort client IP for rate-limit keys (no trusted-proxy chain)."""
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        return str(client.host)
    return "unknown"
