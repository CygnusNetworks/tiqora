"""Avatar resolution, caching and uploads.

Order per picture:

* person (``email`` + SHA-256 of the address): upload → Gravatar → none
* company (``domain``): upload → website icon (not for freemail) → none

Uploads live in ``tiqora_avatar``. Fetched pictures live only in Redis
(``tiqora:avatar:{email|domain}:{key}``, base64 PNG; an empty value caches
"no picture"). Redis is best-effort: when it is down every lookup simply
fetches again. A per-process single-flight lock per key keeps a page with 30
avatars of one company from fetching that company's website 30 times.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal
from urllib.parse import urlparse

import httpx
import redis.asyncio as redis
import structlog
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.avatars.freemail import is_freemail
from tiqora.avatars.normalize import Fit, InvalidImageError, normalize_image, sniff_upload_type
from tiqora.avatars.sources import FetchError, fetch_favicon, fetch_gravatar
from tiqora.db.tiqora.models import TiqoraAvatar
from tiqora.domain.settings_store import (
    KEY_AVATARS_FAVICON_ENABLED,
    KEY_AVATARS_GRAVATAR_ENABLED,
    get_setting_bool,
)
from tiqora.security.outbound import HostResolver

logger = structlog.get_logger(__name__)

Kind = Literal["email", "domain"]
EMAIL: Final = "email"
DOMAIN: Final = "domain"

CACHE_PREFIX = "tiqora:avatar"
HIT_TTL_SECONDS = 7 * 24 * 3600
MISS_TTL_SECONDS = 24 * 3600
#: Timeouts / 5xx: retry sooner than a definite "no picture".
ERROR_TTL_SECONDS = 15 * 60
#: Wall-clock budget for one outbound lookup (page + a few icon candidates).
LOOKUP_BUDGET_SECONDS = 10.0
#: Largest picture an agent may upload.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
PNG = "image/png"

SHA256_PATTERN = r"^[0-9a-f]{64}$"
DOMAIN_PATTERN = (
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$"
)
_SHA256_RE = re.compile(SHA256_PATTERN)
_DOMAIN_RE = re.compile(DOMAIN_PATTERN)


@dataclass(frozen=True)
class AvatarImage:
    data: bytes
    content_type: str = PNG


def email_key(email: str) -> str:
    """SHA-256 hex of the normalized address — the person avatar key."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def is_email_key(value: str) -> bool:
    return bool(_SHA256_RE.match(value))


def normalize_domain(raw: str) -> str | None:
    """Lowercase, no trailing dot, no leading ``www.``; ``None`` if not a domain.

    ``www.example.com`` and ``example.com`` share one logo, so a company's
    website URL and its staff's ``@example.com`` addresses meet on one key.
    """
    domain = raw.strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain if _DOMAIN_RE.match(domain) else None


def domain_from_url(url: str | None) -> str | None:
    """Logo domain of a company website (``customer_company.url``)."""
    text = (url or "").strip()
    if not text:
        return None
    if "//" not in text:
        text = f"https://{text}"
    try:
        host = urlparse(text).hostname
    except ValueError:
        return None
    return normalize_domain(host) if host else None


def cache_key(kind: Kind, subject: str) -> str:
    return f"{CACHE_PREFIX}:{kind}:{subject}"


def upload_version(updated_at: datetime) -> str:
    """Cache-buster the frontend appends as ``?v=`` (epoch milliseconds)."""
    return str(int(updated_at.replace(tzinfo=UTC).timestamp() * 1000))


def prepare_upload(data: bytes, *, fit: Fit) -> bytes:
    """Agent-supplied picture → stored PNG. PNG / JPEG / WebP only (magic bytes)."""
    if sniff_upload_type(data) is None:
        raise InvalidImageError("only PNG, JPEG or WebP images are accepted")
    return normalize_image(data, fit=fit)


class _KeyedLocks:
    """One :class:`asyncio.Lock` per key, dropped once nobody holds or waits."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: dict[str, int] = {}

    @contextlib.asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[key] -= 1
            if self._users[key] == 0:
                del self._users[key]
                del self._locks[key]


_single_flight = _KeyedLocks()


class AvatarService:
    def __init__(
        self,
        session: AsyncSession,
        redis_client: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        self._session = session
        self._redis = redis_client
        self._transport = transport
        self._resolver = resolver

    # ---- read -----------------------------------------------------------

    async def person(self, sha256: str) -> AvatarImage | None:
        upload = await self.uploaded(EMAIL, sha256)
        if upload is not None:
            return upload
        if not await get_setting_bool(self._session, KEY_AVATARS_GRAVATAR_ENABLED, default=True):
            return None
        return await self._fetch_cached(
            EMAIL,
            sha256,
            lambda client: fetch_gravatar(client, sha256, resolver=self._resolver),
        )

    async def company(self, domain: str) -> AvatarImage | None:
        upload = await self.uploaded(DOMAIN, domain)
        if upload is not None:
            return upload
        if is_freemail(domain):
            return None
        if not await get_setting_bool(self._session, KEY_AVATARS_FAVICON_ENABLED, default=True):
            return None
        return await self._fetch_cached(
            DOMAIN,
            domain,
            lambda client: fetch_favicon(client, domain, resolver=self._resolver),
        )

    async def uploaded(self, kind: Kind, subject: str) -> AvatarImage | None:
        row = (
            await self._session.execute(
                select(TiqoraAvatar.data, TiqoraAvatar.content_type).where(
                    TiqoraAvatar.kind == kind, TiqoraAvatar.subject_key == subject
                )
            )
        ).first()
        if row is None:
            return None
        return AvatarImage(data=bytes(row.data), content_type=row.content_type)

    async def _fetch_cached(
        self,
        kind: Kind,
        subject: str,
        fetcher: Callable[[httpx.AsyncClient], Awaitable[bytes | None]],
    ) -> AvatarImage | None:
        key = cache_key(kind, subject)
        cached = await self._cache_get(key)
        if cached is not None:
            return AvatarImage(cached) if cached else None
        # Release the read-only transaction (upload + setting lookups) before
        # waiting on the lock or the network: a ticket list full of uncached
        # avatars must not park one pooled DB connection per outbound fetch.
        await self._session.rollback()
        async with _single_flight.hold(key):
            # Another request may have filled the cache while we waited.
            cached = await self._cache_get(key)
            if cached is not None:
                return AvatarImage(cached) if cached else None
            data: bytes | None
            try:
                async with (
                    asyncio.timeout(LOOKUP_BUDGET_SECONDS),
                    httpx.AsyncClient(transport=self._transport, follow_redirects=False) as client,
                ):
                    data = await fetcher(client)
                ttl = HIT_TTL_SECONDS if data else MISS_TTL_SECONDS
            except (FetchError, TimeoutError) as exc:
                # Log the kind and key prefix only — never the address/domain.
                logger.info(
                    "avatar_fetch_failed", kind=kind, key=subject[:8], error=type(exc).__name__
                )
                data, ttl = None, ERROR_TTL_SECONDS
            await self._cache_set(key, data or b"", ttl)
            return AvatarImage(data) if data else None

    async def _cache_get(self, key: str) -> bytes | None:
        try:
            raw = await self._redis.get(key)
        except (RedisError, OSError):
            return None
        if raw is None:
            return None
        text = raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
        try:
            return base64.b64decode(text, validate=True) if text else b""
        except ValueError:
            return None

    async def _cache_set(self, key: str, data: bytes, ttl: int) -> None:
        with contextlib.suppress(RedisError, OSError):
            await self._redis.set(key, base64.b64encode(data).decode("ascii"), ex=ttl)

    async def forget(self, kind: Kind, subject: str) -> None:
        with contextlib.suppress(RedisError, OSError):
            await self._redis.delete(cache_key(kind, subject))

    # ---- uploads ----------------------------------------------------------

    async def save_upload(
        self, kind: Kind, subject: str, png: bytes, *, owner_ref: str, user_id: int
    ) -> datetime:
        """Insert or replace the upload for *subject*; commits. Returns ``updated_at``."""
        updated_at = await self.stage_upload(
            kind, subject, png, owner_ref=owner_ref, user_id=user_id
        )
        await self._session.commit()
        await self.forget(kind, subject)
        return updated_at

    async def stage_upload(
        self, kind: Kind, subject: str, png: bytes, *, owner_ref: str, user_id: int
    ) -> datetime:
        """Like :meth:`save_upload` without commit; call :meth:`forget` after it."""
        now = datetime.now(UTC).replace(tzinfo=None)
        row = (
            await self._session.execute(
                select(TiqoraAvatar).where(
                    TiqoraAvatar.kind == kind, TiqoraAvatar.subject_key == subject
                )
            )
        ).scalar_one_or_none()
        if row is None:
            self._session.add(
                TiqoraAvatar(
                    kind=kind,
                    subject_key=subject,
                    owner_ref=owner_ref,
                    content_type=PNG,
                    data=png,
                    created_by=user_id,
                    updated_at=now,
                )
            )
        else:
            row.owner_ref = owner_ref
            row.content_type = PNG
            row.data = png
            row.created_by = user_id
            row.updated_at = now
        await self._session.flush()
        return now

    async def delete_upload(self, kind: Kind, subject: str) -> None:
        await self._session.execute(
            delete(TiqoraAvatar).where(
                TiqoraAvatar.kind == kind, TiqoraAvatar.subject_key == subject
            )
        )
        await self._session.commit()
        await self.forget(kind, subject)


async def forget_cached(redis_url: str, keys: list[str]) -> None:
    """Drop cached pictures from code paths without a request (GDPR jobs).

    Best-effort with short timeouts: an unreachable Redis must not fail an
    erasure — the entries expire on their own within :data:`HIT_TTL_SECONDS`.
    """
    if not keys:
        return
    client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        await client.delete(*keys)
    except (RedisError, OSError) as exc:
        logger.warning("avatar_cache_forget_failed", count=len(keys), error=type(exc).__name__)
    finally:
        with contextlib.suppress(RedisError, OSError):
            await client.aclose()
