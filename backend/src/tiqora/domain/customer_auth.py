"""Customer (portal) authentication: password verify, Redis sessions.

Mirrors ``tiqora.domain.auth`` (agent auth) but resolves identity against
``customer_user`` instead of ``users`` and uses a dedicated Redis key prefix
and cookie so agent and customer sessions never collide, even if both are
issued to the same browser.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.znuny.password import verify_password

CUSTOMER_SESSION_KEY_PREFIX = "tiqora:csession:"


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
        self._prefix = CUSTOMER_SESSION_KEY_PREFIX

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    async def create(self, customer_id: int, login: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = f"{customer_id}:{login}"
        await self._client.set(self._key(token), payload, ex=self._ttl)
        return token

    async def get(self, token: str) -> tuple[int, str] | None:
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
        await self._client.delete(self._key(token))


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
        """Verify customer credentials against ``customer_user.pw`` (valid_id must be 1)."""
        result = await self._session.execute(
            select(CustomerUser).where(CustomerUser.login == login, CustomerUser.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.pw:
            return None
        if not verify_password(password, user.pw):
            return None
        return _to_authenticated(user)

    async def create_session(self, customer: AuthenticatedCustomer) -> str:
        return await self._sessions.create(customer.id, customer.login)

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
