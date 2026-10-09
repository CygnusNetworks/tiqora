"""Customer (portal) authentication: password verify, Redis sessions.

Mirrors ``tiqora.domain.auth`` (agent auth) but resolves identity against
``customer_user`` instead of ``users`` and uses a dedicated Redis key prefix
and cookie so agent and customer sessions never collide, even if both are
issued to the same browser.
"""

from __future__ import annotations

import contextlib
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.identity.sso_sessions import SsoSessionInfo, drop_sso, load_sso, store_sso
from tiqora.znuny.password import hash_password, is_weak_scheme, needs_rehash, verify_password

CUSTOMER_SESSION_KEY_PREFIX = "tiqora:csession:"
CUSTOMER_SESSION_AUTH_AT_KEY_PREFIX = "tiqora:csession:authat:"
_PENDING_PAYLOAD_PREFIX = "PENDING:"


@dataclass(frozen=True, slots=True)
class AuthenticatedCustomer:
    """Resolved customer identity for portal request handling."""

    id: int
    login: str
    email: str
    customer_id: str
    first_name: str
    last_name: str


def _to_authenticated(user: CustomerUser) -> AuthenticatedCustomer:
    return AuthenticatedCustomer(
        id=user.id,
        login=user.login,
        email=user.email,
        customer_id=user.customer_id,
        first_name=user.first_name,
        last_name=user.last_name,
    )


class CustomerSessionStore:
    """Opaque session tokens for customers, stored in Redis with sliding TTL."""

    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self._client = client
        self._ttl = settings.session_ttl_seconds
        self._absolute_ttl = getattr(settings, "session_absolute_ttl_seconds", 43200)
        self._prefix = CUSTOMER_SESSION_KEY_PREFIX
        self._born_prefix = f"{CUSTOMER_SESSION_KEY_PREFIX}born:"

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    def _born_key(self, token: str) -> str:
        return f"{self._born_prefix}{token}"

    def _auth_at_key(self, token: str) -> str:
        return f"{CUSTOMER_SESSION_AUTH_AT_KEY_PREFIX}{token}"

    def _user_index_key(self, customer_id: int) -> str:
        return f"{self._prefix}user:{customer_id}"

    async def create(
        self, customer_id: int, login: str, *, sso: SsoSessionInfo | None = None
    ) -> str:
        token = secrets.token_urlsafe(32)
        payload = f"{customer_id}:{login}"
        await self._client.set(self._key(token), payload, ex=self._ttl)
        # Absolute-lifetime marker, never renewed by touch() (security review L-2).
        await self._client.set(self._born_key(token), "1", ex=self._absolute_ttl)
        # Authentication timestamp for step-up checks, same fixed TTL.
        await self._client.set(
            self._auth_at_key(token), str(int(time.time())), ex=self._absolute_ttl
        )
        # Per-customer reverse index for revoke_customer_sessions().
        with contextlib.suppress(Exception):
            index_key = self._user_index_key(customer_id)
            await self._client.sadd(index_key, token)
            await self._client.expire(index_key, self._absolute_ttl)
        if sso is not None:
            await store_sso(self._client, "c", token, sso, self._absolute_ttl)
        return token

    async def get_sso(self, token: str) -> SsoSessionInfo | None:
        return await load_sso(self._client, "c", token)

    async def seconds_since_auth(self, token: str) -> int | None:
        """Age of the authentication event behind *token*; ``None`` if unknown."""
        raw = await self._client.get(self._auth_at_key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return max(0, int(time.time()) - int(str(raw).strip()))
        except (TypeError, ValueError):
            return None

    async def create_pending(self, customer_id: int, login: str, ttl_seconds: int) -> str:
        """Short-lived 'pending 2FA' session, invisible to :meth:`get`.

        No born marker is written and the payload carries ``PENDING:``, so
        :meth:`get` (and with it every portal route) rejects the token; only
        :meth:`get_pending` and :meth:`promote_pending` read it.
        """
        token = secrets.token_urlsafe(32)
        payload = f"{_PENDING_PAYLOAD_PREFIX}{customer_id}:{login}"
        await self._client.set(self._key(token), payload, ex=ttl_seconds)
        # Same per-customer index as full sessions, so revoke-all (password
        # change, admin action) also removes a pending token.
        with contextlib.suppress(Exception):
            index_key = self._user_index_key(customer_id)
            await self._client.sadd(index_key, token)
            await self._client.expire(index_key, self._absolute_ttl)
        return token

    async def get_pending(self, token: str) -> tuple[int, str] | None:
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw.startswith(_PENDING_PAYLOAD_PREFIX):
            return None
        try:
            _, customer_id_s, login = raw.split(":", 2)
            return int(customer_id_s), login
        except (ValueError, TypeError):
            return None

    async def promote_pending(self, token: str) -> str | None:
        """Consume a pending session and issue a full session token."""
        data = await self.get_pending(token)
        if data is None:
            return None
        customer_id, login = data
        # DEL's reply is the atomic claim: of two parallel promotions only one
        # sees the key (a fake returning None counts as "removed").
        if await self._client.delete(self._key(token)) == 0:
            return None
        await self.delete(token)
        with contextlib.suppress(Exception):
            await self._client.srem(self._user_index_key(customer_id), token)
        return await self.create(customer_id, login)

    async def revoke_customer_sessions(
        self, customer_id: int, *, keep_token: str | None = None
    ) -> int:
        """Delete every live session of *customer_id*; return how many were removed."""
        index_key = self._user_index_key(customer_id)
        try:
            members = await self._client.smembers(index_key)
        except Exception:  # noqa: BLE001 — a missing index must not break the write
            return 0
        removed = 0
        for raw in members or set():
            token = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            if keep_token is not None and token == keep_token:
                continue
            await self.delete(token)
            removed += 1
        with contextlib.suppress(Exception):
            await self._client.delete(index_key)
            if keep_token is not None:
                await self._client.sadd(index_key, keep_token)
                await self._client.expire(index_key, self._absolute_ttl)
        return removed

    async def get(self, token: str) -> tuple[int, str] | None:
        if not await self._client.get(self._born_key(token)):
            return None
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            customer_id_s, login = raw.split(":", 1)
            return int(customer_id_s), login
        except (ValueError, TypeError):
            return None

    async def touch(self, token: str) -> None:
        """Sliding renewal: reset TTL if the session still exists."""
        await self._client.expire(self._key(token), self._ttl)

    async def delete(self, token: str) -> None:
        await self._client.delete(self._key(token), self._born_key(token), self._auth_at_key(token))
        await drop_sso(self._client, "c", token)

    async def revoke_logins(self, logins: Iterable[str]) -> int:
        """Delete every portal session of *logins* (no per-login index: scan the tokens)."""
        wanted = set(logins)
        if not wanted:
            return 0
        removed = 0
        async for raw_key in self._client.scan_iter(match=f"{self._prefix}*", count=500):
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
            token = key[len(self._prefix) :]
            if not token or ":" in token:  # the born: side keys
                continue
            value = await self._client.get(key)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            if value is not None and str(value).split(":", 1)[-1] in wanted:
                await self.delete(token)
                removed += 1
        return removed


async def revoke_customer_redis_sessions(
    client: redis.Redis, settings: Settings, logins: Iterable[str]
) -> int:
    """Every Redis session of the customers *logins*: portal sessions and GI customer tokens."""
    from tiqora.domain.auth import SessionStore

    wanted = set(logins)
    portal = await CustomerSessionStore(client, settings).revoke_logins(wanted)
    compat = await SessionStore(client, settings).revoke_customer_sessions(wanted)
    return portal + compat


class CustomerAuthService:
    """Login / session resolution against Znuny's ``customer_user`` table."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: CustomerSessionStore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._settings = settings

    async def authenticate_password(
        self, login: str, password: str
    ) -> AuthenticatedCustomer | None:
        """Verify customer credentials against ``customer_user.pw`` (valid_id must be 1).

        Mirrors agent rehash-on-login for weak/legacy schemes (H-06).
        """
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.login == login, CustomerUser.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.pw:
            return None
        stored = user.pw
        reject_weak = bool(getattr(self._settings, "password_reject_weak_hashes", False))
        rehash_on = bool(getattr(self._settings, "password_rehash_on_login", True))
        if reject_weak and is_weak_scheme(stored):
            return None
        if not verify_password(password, stored):
            return None
        if rehash_on and needs_rehash(stored):
            user.pw = hash_password(password)
            try:
                await self._session.commit()
            except Exception:  # noqa: BLE001 — login must not fail on rehash write
                await self._session.rollback()
        return _to_authenticated(user)

    async def create_session(
        self, customer: AuthenticatedCustomer, *, sso: SsoSessionInfo | None = None
    ) -> str:
        return await self._sessions.create(customer.id, customer.login, sso=sso)

    async def create_pending_session(self, customer: AuthenticatedCustomer) -> str:
        return await self._sessions.create_pending(
            customer.id, customer.login, self._settings.totp_pending_ttl_seconds
        )

    async def get_pending_session(self, token: str) -> tuple[int, str] | None:
        return await self._sessions.get_pending(token)

    async def promote_pending_session(self, token: str) -> tuple[str, AuthenticatedCustomer] | None:
        """Issue a full session for a still-valid pending one (customer must be valid)."""
        pending = await self._sessions.get_pending(token)
        if pending is None:
            return None
        customer_id, login = pending
        result = await self._session.execute(
            select(CustomerUser).where(
                CustomerUser.id == customer_id,
                CustomerUser.login == login,
                CustomerUser.valid_id == 1,
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        new_token = await self._sessions.promote_pending(token)
        if new_token is None:
            return None
        return new_token, _to_authenticated(user)

    async def resolve_session(self, token: str) -> AuthenticatedCustomer | None:
        data = await self._sessions.get(token)
        if data is None:
            return None
        customer_id, login = data
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.id == customer_id, CustomerUser.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None or user.login != login:
            return None
        await self._sessions.touch(token)
        return _to_authenticated(user)

    async def logout(self, token: str) -> None:
        await self._sessions.delete(token)

    async def get_customer_by_id(self, customer_id: int) -> AuthenticatedCustomer | None:
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.id == customer_id, CustomerUser.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return _to_authenticated(user)

    async def get_customer_by_login(self, login: str) -> AuthenticatedCustomer | None:
        """Look up an existing, valid customer by login — used by LDAP auth.

        No auto-provisioning: returns ``None`` if no matching valid customer
        exists, and the caller must reject the login.
        """
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.login == login, CustomerUser.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return _to_authenticated(user)


def customer_to_dict(customer: AuthenticatedCustomer) -> dict[str, Any]:
    return {
        "id": customer.id,
        "login": customer.login,
        "email": customer.email,
        "customer_id": customer.customer_id,
        "first_name": customer.first_name,
        "last_name": customer.last_name,
    }
