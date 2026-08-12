"""Agent authentication: password verify, Redis sessions, API keys."""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.znuny.password import hash_password, is_weak_scheme, needs_rehash, verify_password

SESSION_KEY_PREFIX = "tiqora:session:"
SESSION_AVATAR_KEY_PREFIX = "tiqora:session:avatar:"
SESSION_BORN_KEY_PREFIX = "tiqora:session:born:"
# Wall-clock of the authentication event that produced the session. Never
# renewed by touch() — "recent" must mean recently *authenticated*, not
# recently active, or the marker would be worthless for step-up (H-2).
SESSION_AUTH_AT_KEY_PREFIX = "tiqora:session:authat:"
# Reverse index token-set per user, so a password change can revoke every live
# session of that account (M-2). Sessions are keyed by opaque token only, so
# without this there is no way to enumerate them.
SESSION_USER_INDEX_KEY_PREFIX = "tiqora:session:user:"

# A never-matching bcrypt hash so authentication does the same bcrypt work for a
# non-existent login as for a wrong password — closes the username-enumeration
# timing side channel (security review L-3).
_DECOY_PW_HASH = hash_password(secrets.token_urlsafe(24))


def _utcnow() -> datetime:
    """Naive UTC now — matches DateTime columns (server stores naive)."""
    return datetime.utcnow()  # noqa: DTZ003 — intentional naive UTC for DB columns


# Znuny-compatible language codes accepted for the ``UserLanguage`` preference.
# Keep in sync with frontend/src/i18n/locales.ts SUPPORTED_LOCALES.
USER_LANGUAGE_CODES = frozenset(
    {
        "en",
        "de",
        "ar_SA",
        "bg",
        "ca",
        "cs",
        "da",
        "el",
        "en_CA",
        "en_GB",
        "es",
        "es_CO",
        "es_MX",
        "et",
        "fa",
        "fi",
        "fr",
        "fr_CA",
        "gl",
        "he",
        "hi",
        "hr",
        "hu",
        "id",
        "it",
        "ja",
        "ko",
        "lt",
        "lv",
        "mk",
        "ms",
        "nb_NO",
        "nl",
        "pl",
        "pt",
        "pt_BR",
        "ro",
        "ru",
        "sk_SK",
        "sl",
        "sr",
        "sv",
        "sw",
        "th_TH",
        "tr",
        "uk",
        "vi_VN",
        "zh_CN",
        "zh_TW",
    }
)


def normalize_language_code(raw: str) -> str | None:
    """Normalise a BCP-47/Znuny language code and validate against
    :data:`USER_LANGUAGE_CODES`. Returns ``None`` when unsupported."""
    code = (raw or "").strip().replace("-", "_")
    if "_" in code:
        lang, _, region = code.partition("_")
        code = f"{lang.lower()}_{region.upper()}"
    else:
        code = code.lower()
    return code if code in USER_LANGUAGE_CODES else None


def decode_preference_value(raw: object) -> str | None:
    """Decode ``user_preferences.preferences_value`` (MySQL LONGBLOB / PG TEXT).

    Drivers differ: MariaDB returns ``bytes``; PostgreSQL's Znuny schema uses
    TEXT, and LargeBinary inserts may surface as a hex literal (``\\x6465`` for
    ``de``). Accept bytes, memoryview, plain text, and the hex form.
    """
    if raw is None:
        return None
    if isinstance(raw, memoryview | bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace").strip()
        return text or None
    if isinstance(raw, str):
        if raw.startswith("\\x"):
            try:
                text = bytes.fromhex(raw[2:]).decode("utf-8", errors="replace").strip()
                return text or None
            except ValueError:
                pass
        text = raw.strip()
        return text or None
    text = str(raw).strip()
    return text or None


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
    # None = unrestricted (session or unscoped API key). Non-empty frozenset
    # limits the key (read / write / mcp). write implies HTTP mutations; read
    # alone is GET/HEAD/OPTIONS only.
    api_key_scopes: frozenset[str] | None = None


class SessionStore:
    """Opaque session tokens stored in Redis with sliding TTL renewal."""

    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self._client = client
        self._ttl = settings.session_ttl_seconds
        self._absolute_ttl = getattr(settings, "session_absolute_ttl_seconds", 43200)
        self._prefix = SESSION_KEY_PREFIX
        self._avatar_prefix = SESSION_AVATAR_KEY_PREFIX
        self._born_prefix = SESSION_BORN_KEY_PREFIX
        self._auth_at_prefix = SESSION_AUTH_AT_KEY_PREFIX
        self._user_index_prefix = SESSION_USER_INDEX_KEY_PREFIX

    def _key(self, token: str) -> str:
        return f"{self._prefix}{token}"

    def _avatar_key(self, token: str) -> str:
        return f"{self._avatar_prefix}{token}"

    def _born_key(self, token: str) -> str:
        return f"{self._born_prefix}{token}"

    def _auth_at_key(self, token: str) -> str:
        return f"{self._auth_at_prefix}{token}"

    def _user_index_key(self, user_id: int) -> str:
        return f"{self._user_index_prefix}{user_id}"

    async def create(self, user_id: int, login: str, *, avatar_url: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        payload = f"{user_id}:{login}"
        await self._client.set(self._key(token), payload, ex=self._ttl)
        # Absolute-lifetime marker: fixed TTL, never renewed by touch(), so the
        # session dies session_absolute_ttl_seconds after creation regardless of
        # activity (L-2).
        await self._client.set(self._born_key(token), "1", ex=self._absolute_ttl)
        # Authentication timestamp for step-up checks (H-2), same fixed TTL.
        await self._client.set(
            self._auth_at_key(token), str(int(time.time())), ex=self._absolute_ttl
        )
        # Reverse index so revoke_user_sessions() can find this token (M-2).
        with contextlib.suppress(Exception):
            index_key = self._user_index_key(user_id)
            await self._client.sadd(index_key, token)
            await self._client.expire(index_key, self._absolute_ttl)
        if avatar_url:
            await self._client.set(self._avatar_key(token), avatar_url, ex=self._ttl)
        return token

    async def seconds_since_auth(self, token: str) -> int | None:
        """Age of the authentication event behind *token*, or ``None`` if unknown.

        ``None`` means the session predates this marker (rolling upgrade) or its
        key expired — callers decide how to treat that; the step-up helper
        treats it as "too old" so the safe answer does not depend on Redis
        bookkeeping surviving a deploy.
        """
        raw = await self._client.get(self._auth_at_key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return max(0, int(time.time()) - int(str(raw).strip()))
        except (TypeError, ValueError):
            return None

    async def revoke_user_sessions(self, user_id: int, *, keep_token: str | None = None) -> int:
        """Delete every live session of *user_id*; return how many were removed.

        Used after a password change so a stolen session cannot outlive the
        credential it was obtained with (M-2). ``keep_token`` spares the caller's
        own session, which is what a self-service password change wants.
        """
        index_key = self._user_index_key(user_id)
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
        if keep_token is not None:
            with contextlib.suppress(Exception):
                await self._client.delete(index_key)
                await self._client.sadd(index_key, keep_token)
                await self._client.expire(index_key, self._absolute_ttl)
        else:
            with contextlib.suppress(Exception):
                await self._client.delete(index_key)
        return removed

    async def get(self, token: str) -> tuple[int, str] | None:
        # Absolute-lifetime gate: once the (non-renewed) born marker has expired,
        # the session is dead even if the sliding key was kept warm.
        if not await self._client.get(self._born_key(token)):
            return None
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
        await self._client.delete(
            self._key(token),
            self._avatar_key(token),
            self._born_key(token),
            self._auth_at_key(token),
        )

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

    async def create_enroll(self, user_id: int, login: str, ttl_seconds: int) -> str:
        """Create a short-lived 'must-enroll-2FA' session (not resolvable by :meth:`get`).

        Mirrored after :meth:`create_pending`: the ``ENROLL:`` prefix makes the
        payload invisible to normal ``resolve_session`` / ``get_current_user``
        and only readable via :meth:`get_enroll`. Callers use this after
        password login when 2FA is enforced but the agent has not enrolled yet.
        """
        token = secrets.token_urlsafe(32)
        payload = f"ENROLL:{user_id}:{login}"
        await self._client.set(self._key(token), payload, ex=ttl_seconds)
        return token

    async def get_enroll(self, token: str) -> tuple[int, str] | None:
        raw = await self._client.get(self._key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw.startswith("ENROLL:"):
            return None
        try:
            _, user_id_s, login = raw.split(":", 2)
            return int(user_id_s), login
        except (ValueError, TypeError):
            return None

    async def promote_enroll(self, token: str, *, avatar_url: str | None = None) -> str | None:
        """Verify+consume an enroll session and issue a full session token."""
        data = await self.get_enroll(token)
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


class ApiKeyRateLimited(Exception):
    """Raised when a bearer API key exceeds its request rate limit."""

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = max(1, int(retry_after))
        super().__init__(f"API key rate limit exceeded; retry after {self.retry_after}s")


def parse_api_key_scopes(raw: str | None) -> frozenset[str] | None:
    """Parse scopes column; None/empty means unrestricted.

    Implementation lives in :mod:`tiqora.domain.api_key_scopes` (area RO/RW +
    legacy ``read``/``write``/``mcp``/``*`` tokens).
    """
    from tiqora.domain.api_key_scopes import parse_api_key_scopes as _parse

    return _parse(raw)


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

    async def _load_preference(self, user_id: int, key: str) -> str | None:
        """Read a single ``user_preferences`` value (UTF-8 LONGBLOB / TEXT).

        Uses raw SQL: the ORM maps the column as ``LargeBinary`` (correct for
        MySQL LONGBLOB) but Znuny PG schema uses TEXT, and LargeBinary coercion
        can drop plain-text language codes on asyncpg.
        """
        from sqlalchemy import text

        result = await self._session.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id = :uid AND preferences_key = :k"
            ),
            {"uid": user_id, "k": key},
        )
        return decode_preference_value(result.scalar_one_or_none())

    async def _load_user_email(self, user_id: int) -> str | None:
        """Znuny stores the agent mailbox in ``user_preferences.UserEmail``."""
        return await self._load_preference(user_id, "UserEmail")

    async def load_user_language(self, user_id: int) -> str | None:
        """Znuny UI / notification language from ``UserLanguage`` preference."""
        return await self._load_preference(user_id, "UserLanguage")

    async def set_user_language(self, user_id: int, language: str) -> None:
        """Upsert Znuny-compatible ``UserLanguage`` preference as UTF-8 text.

        Bind a Python ``str`` (not ``bytes``) so PostgreSQL TEXT and MySQL
        LONGBLOB both store readable language codes rather than PG hex escapes.
        """
        from sqlalchemy import text

        result = await self._session.execute(
            select(UserPreferences.user_id).where(
                UserPreferences.user_id == user_id,
                UserPreferences.preferences_key == "UserLanguage",
            )
        )
        if result.scalar_one_or_none() is None:
            await self._session.execute(
                text(
                    "INSERT INTO user_preferences"
                    " (user_id, preferences_key, preferences_value)"
                    " VALUES (:uid, 'UserLanguage', :v)"
                ),
                {"uid": user_id, "v": language},
            )
        else:
            await self._session.execute(
                text(
                    "UPDATE user_preferences SET preferences_value = :v"
                    " WHERE user_id = :uid AND preferences_key = 'UserLanguage'"
                ),
                {"uid": user_id, "v": language},
            )
        await self._session.commit()

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
        """Verify agent credentials against ``users.pw`` (valid_id must be 1).

        On success, weak/legacy hashes (SHA1, md5-crypt, DES, plain SHA-*) are
        transparently rehashed to Znuny ``BCRYPT:`` when
        ``password_rehash_on_login`` is enabled (H-06). When
        ``password_reject_weak_hashes`` is set, weak schemes are refused entirely.
        """
        result = await self._session.execute(
            select(Users).where(Users.login == login, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            # Equalize timing vs. the wrong-password path (L-3).
            verify_password(password, _DECOY_PW_HASH)
            return None
        stored = user.pw or ""
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

    async def get_enroll_session(self, token: str) -> tuple[int, str] | None:
        return await self._sessions.get_enroll(token)

    async def create_enroll_session(self, user: AuthenticatedUser) -> str:
        """Create a short-lived must-enroll-2FA session (password login step)."""
        return await self._sessions.create_enroll(
            user.id, user.login, self._settings.totp_pending_ttl_seconds
        )

    async def promote_enroll_session(
        self, token: str, *, avatar_url: str | None = None
    ) -> tuple[str, AuthenticatedUser] | None:
        """Verify an enroll session still exists and issue a full session token."""
        enroll = await self._sessions.get_enroll(token)
        if enroll is None:
            return None
        user_id, login = enroll
        result = await self._session.execute(
            select(Users).where(Users.id == user_id, Users.login == login, Users.valid_id == 1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        new_token = await self._sessions.promote_enroll(token, avatar_url=avatar_url)
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
        # Per-key fixed-window throttle (REST bearer). Fail open if Redis down.
        from tiqora.security.ratelimit import ApiKeyRateLimiter

        decision = await ApiKeyRateLimiter(self._sessions._client, self._settings).check_and_incr(
            int(row.id)
        )
        if not decision.allowed:
            raise ApiKeyRateLimited(decision.retry_after)
        user_result = await self._session.execute(
            select(Users).where(Users.id == row.user_id, Users.valid_id == 1)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return None
        scopes = parse_api_key_scopes(getattr(row, "scopes", None))
        # Stamp last_used_at; auth must not fail if the metadata write fails.
        try:
            row.last_used_at = now
            await self._session.commit()
        except Exception:  # noqa: BLE001 — non-fatal metadata stamp
            await self._session.rollback()
        resolved = await self._user_from_row(user, auth_method="api_key")
        if scopes is None:
            return resolved
        return AuthenticatedUser(
            id=resolved.id,
            login=resolved.login,
            first_name=resolved.first_name,
            last_name=resolved.last_name,
            auth_method=resolved.auth_method,
            email=resolved.email,
            avatar_url=resolved.avatar_url,
            api_key_scopes=scopes,
        )

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
