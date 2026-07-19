"""Agent authentication: password verify, Redis sessions, API keys."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.znuny.password import verify_password

SESSION_KEY_PREFIX = "tiqora:session:"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Resolved agent identity for request handling."""

    id: int
    login: str
    first_name: str
    last_name: str
    auth_method: str  # "session" | "api_key"


class SessionStore:
    """Opaque session tokens stored in Redis with sliding TTL renewal."""

    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self._client = client
        self._ttl = settings.session_ttl_seconds
        self._prefix = SESSION_KEY_PREFIX

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    async def create(self, user_id: int, login: str) -> str:
        token = secrets.token_urlsafe(32)
        payload = f"{user_id}:{login}"
        await self._client.set(self._key(token), payload, ex=self._ttl)
        return token

    async def get(self, token: str) -> tuple[int, str] | None:
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            user_id_s, login = raw.split(":", 1)
            return int(user_id_s), login
        except (ValueError, TypeError):
            return None

    async def touch(self, token: str) -> None:
        """Sliding renewal: reset TTL if the session still exists."""
        await self._client.expire(self._key(token), self._ttl)

    async def delete(self, token: str) -> None:
        await self._client.delete(self._key(token))

    async def create_pending(self, user_id: int, login: str, ttl_seconds: int) -> str:
        """Create a short-lived 'pending 2FA' session (not resolvable by :meth:`get`).

        The payload is tagged with a ``PENDING:`` prefix so that
        :meth:`get`'s ``int(user_id)`` parse fails and returns ``None`` —
        pending sessions are deliberately invisible to the normal
        ``get_current_user`` path and only resolvable via :meth:`get_pending`.
        """
        token = secrets.token_urlsafe(32)
        payload = f"PENDING:{user_id}:{login}"
        await self._client.set(self._key(token), payload, ex=ttl_seconds)
        return token

    async def get_pending(self, token: str) -> tuple[int, str] | None:
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw.startswith("PENDING:"):
            return None
        try:
            _, user_id_s, login = raw.split(":", 2)
            return int(user_id_s), login
        except (ValueError, TypeError):
            return None

    async def promote_pending(self, token: str) -> str | None:
        """Verify+consume a pending session and issue a full session token."""
        data = await self.get_pending(token)
        if data is None:
            return None
        user_id, login = data
        await self.delete(token)
        return await self.create(user_id, login)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of the opaque API key (never store plaintext)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Return a new opaque API key (caller stores the hash only)."""
    return f"tiqora_{secrets.token_urlsafe(32)}"


class AuthService:
    """Login / session / API-key resolution against Znuny users + tiqora tables."""

    def __init__(
        self,
        session: AsyncSession,
        sessions: SessionStore,
        settings: Settings,
    ) -> None:
        self._session = session
        self._sessions = sessions
        self._settings = settings

    async def authenticate_password(self, login: str, password: str) -> AuthenticatedUser | None:
        """Verify agent credentials against ``users.pw`` (valid_id must be 1)."""
        result = await self._session.execute(
            select(Users).where(Users.login == login, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        if not verify_password(password, user.pw):
            return None
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method="session",
        )

    async def create_session(self, user: AuthenticatedUser) -> str:
        return await self._sessions.create(user.id, user.login)

    async def resolve_session(self, token: str) -> AuthenticatedUser | None:
        data = await self._sessions.get(token)
        if data is None:
            return None
        user_id, login = data
        result = await self._session.execute(
            select(Users).where(Users.id == user_id, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None or user.login != login:
            return None
        await self._sessions.touch(token)
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method="session",
        )

    async def logout(self, token: str) -> None:
        await self._sessions.delete(token)

    async def get_user_by_login(
        self, login: str, *, auth_method: str = "session"
    ) -> AuthenticatedUser | None:
        """Look up an existing, valid user by login — used by SSO/SPNEGO.

        No auto-provisioning: returns ``None`` if no matching valid user
        exists, and the caller must reject the login.
        """
        result = await self._session.execute(
            select(Users).where(Users.login == login, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method=auth_method,
        )

    async def get_pending_session(self, token: str) -> tuple[int, str] | None:
        return await self._sessions.get_pending(token)

    async def create_pending_session(self, user: AuthenticatedUser) -> str:
        """Create a short-lived pending-2FA session (password/SSO/SPNEGO step 1)."""
        return await self._sessions.create_pending(
            user.id, user.login, self._settings.totp_pending_ttl_seconds
        )

    async def promote_pending_session(self, token: str) -> tuple[str, AuthenticatedUser] | None:
        """Verify a pending session still exists and issue a full session token."""
        pending = await self._sessions.get_pending(token)
        if pending is None:
            return None
        user_id, login = pending
        result = await self._session.execute(
            select(Users).where(Users.id == user_id, Users.login == login, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        new_token = await self._sessions.promote_pending(token)
        if new_token is None:
            return None
        return new_token, AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method="session",
        )

    async def resolve_api_key(self, raw_key: str) -> AuthenticatedUser | None:
        key_hash = hash_api_key(raw_key)
        result = await self._session.execute(
            select(TiqoraApiKey).where(
                TiqoraApiKey.key_hash == key_hash,
                TiqoraApiKey.valid.is_(True),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        user_result = await self._session.execute(
            select(Users).where(Users.id == row.user_id, Users.valid_id == 1)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return None
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method="api_key",
        )

    async def get_user_by_id(self, user_id: int) -> AuthenticatedUser | None:
        result = await self._session.execute(
            select(Users).where(Users.id == user_id, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method="session",
        )


def user_to_dict(user: AuthenticatedUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "login": user.login,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "auth_method": user.auth_method,
    }
