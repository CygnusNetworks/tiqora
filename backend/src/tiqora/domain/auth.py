"""Agent authentication: password verify, Redis sessions, API keys."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.znuny.password import verify_password

SESSION_KEY_PREFIX = "tiqora:session:"
SESSION_AVATAR_KEY_PREFIX = "tiqora:session:avatar:"


def _utcnow() -> datetime:
    """Naive UTC now — matches DateTime columns (server stores naive)."""
    return datetime.utcnow()  # noqa: DTZ003 — intentional naive UTC for DB columns


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Resolved agent identity for request handling."""

    id: int
    login: str
    first_name: str
    last_name: str
    auth_method: str  # "session" | "api_key" | "sso" | "ldap" | "spnego"
    email: str | None = None
    avatar_url: str | None = None


class SessionStore:
    """Opaque session tokens stored in Redis with sliding TTL renewal."""

    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self._client = client
        self._ttl = settings.session_ttl_seconds
        self._prefix = SESSION_KEY_PREFIX
        self._avatar_prefix = SESSION_AVATAR_KEY_PREFIX

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    def _avatar_key(self, token: str) -> str:
        return f"{self._avatar_prefix}{token}"

    async def create(self, user_id: int, login: str, *, avatar_url: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        payload = f"{user_id}:{login}"
        await self._client.set(self._key(token), payload, ex=self._ttl)
        if avatar_url:
            await self._client.set(self._avatar_key(token), avatar_url, ex=self._ttl)
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

    async def get_avatar_url(self, token: str) -> str | None:
        raw = await self._client.get(self._avatar_key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = str(raw).strip()
        return value or None

    async def touch(self, token: str) -> None:
        """Sliding renewal: reset TTL if the session still exists."""
        await self._client.expire(self._key(token), self._ttl)
        await self._client.expire(self._avatar_key(token), self._ttl)

    async def delete(self, token: str) -> None:
        await self._client.delete(self._key(token), self._avatar_key(token))

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

    async def promote_pending(self, token: str, *, avatar_url: str | None = None) -> str | None:
        """Verify+consume a pending session and issue a full session token."""
        data = await self.get_pending(token)
        if data is None:
            return None
        user_id, login = data
        await self.delete(token)
        return await self.create(user_id, login, avatar_url=avatar_url)


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

    async def _load_user_email(self, user_id: int) -> str | None:
        """Znuny stores the agent mailbox in ``user_preferences.UserEmail``."""
        result = await self._session.execute(
            select(UserPreferences.preferences_value).where(
                UserPreferences.user_id == user_id,
                UserPreferences.preferences_key == "UserEmail",
            )
        )
        raw = result.scalar_one_or_none()
        if raw is None:
            return None
        if isinstance(raw, bytes | bytearray | memoryview):
            value = bytes(raw).decode("utf-8", errors="replace").strip()
        else:
            value = str(raw).strip()
        return value or None

    async def _user_from_row(
        self,
        user: Users,
        *,
        auth_method: str,
        avatar_url: str | None = None,
    ) -> AuthenticatedUser:
        email = await self._load_user_email(user.id)
        return AuthenticatedUser(
            id=user.id,
            login=user.login,
            first_name=user.first_name,
            last_name=user.last_name,
            auth_method=auth_method,
            email=email,
            avatar_url=avatar_url,
        )

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
        return await self._user_from_row(user, auth_method="session")

    async def create_session(
        self, user: AuthenticatedUser, *, avatar_url: str | None = None
    ) -> str:
        # Prefer an explicit avatar_url (OIDC picture); fall back to whatever
        # was already attached to the AuthenticatedUser (usually None).
        resolved = avatar_url if avatar_url is not None else user.avatar_url
        return await self._sessions.create(user.id, user.login, avatar_url=resolved)

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
        avatar_url = await self._sessions.get_avatar_url(token)
        return await self._user_from_row(user, auth_method="session", avatar_url=avatar_url)

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
        return await self._user_from_row(user, auth_method=auth_method)

    async def get_pending_session(self, token: str) -> tuple[int, str] | None:
        return await self._sessions.get_pending(token)

    async def create_pending_session(
        self, user: AuthenticatedUser, *, avatar_url: str | None = None
    ) -> str:
        """Create a short-lived pending-2FA session (password/SSO/SPNEGO step 1).

        ``avatar_url`` is accepted for API symmetry with :meth:`create_session`
        but is not stored on the pending token (pending sessions never reach
        ``/me``). Callers with 2FA + OIDC re-capture picture after promote.
        """
        _ = avatar_url
        return await self._sessions.create_pending(
            user.id, user.login, self._settings.totp_pending_ttl_seconds
        )

    async def promote_pending_session(
        self, token: str, *, avatar_url: str | None = None
    ) -> tuple[str, AuthenticatedUser] | None:
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
        new_token = await self._sessions.promote_pending(token, avatar_url=avatar_url)
        if new_token is None:
            return None
        return new_token, await self._user_from_row(
            user, auth_method="session", avatar_url=avatar_url
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
        now = _utcnow()
        if row.expires_at is not None and row.expires_at <= now:
            return None
        user_result = await self._session.execute(
            select(Users).where(Users.id == row.user_id, Users.valid_id == 1)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return None
        # Stamp last_used_at; auth must not fail if the metadata write fails.
        try:
            row.last_used_at = now
            await self._session.commit()
        except Exception:  # noqa: BLE001 — non-fatal metadata stamp
            await self._session.rollback()
        return await self._user_from_row(user, auth_method="api_key")

    async def get_user_by_id(self, user_id: int) -> AuthenticatedUser | None:
        result = await self._session.execute(
            select(Users).where(Users.id == user_id, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        return await self._user_from_row(user, auth_method="session")


def user_to_dict(user: AuthenticatedUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "login": user.login,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "auth_method": user.auth_method,
        "email": user.email,
        "avatar_url": user.avatar_url,
    }
