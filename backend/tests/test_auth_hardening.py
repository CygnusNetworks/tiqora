"""Security batch E: rate-limit, TOTP replay, rehash-on-login, logout flags, CSRF.

Unit + integration coverage for M-7/H-01, M-04, H-06, M-10, M-02.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime
from typing import Any

import pyotp
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.domain.totp import TOTPService
from tiqora.security.csrf import csrf_check_required, origin_allowed, request_has_session_cookie
from tiqora.security.ratelimit import AuthRateLimiter
from tiqora.znuny.password import detect_scheme, hash_password, needs_rehash, verify_password

NOW = datetime(2024, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# In-memory Redis stub (pipeline + sorted-set ops for the rate limiter)
# ---------------------------------------------------------------------------


class _MemRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._exp: dict[str, float] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def _alive(self, key: str) -> bool:
        exp = self._exp.get(key)
        if exp is not None and exp <= time.time():
            self._kv.pop(key, None)
            self._zsets.pop(key, None)
            self._exp.pop(key, None)
            return False
        return key in self._kv or key in self._zsets

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._kv[key] = str(value)
        if ex is not None:
            self._exp[key] = time.time() + int(ex)
        else:
            self._exp.pop(key, None)

    async def get(self, key: str) -> str | None:
        if not self._alive(key):
            return None
        return self._kv.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._kv.pop(key, None)
            self._zsets.pop(key, None)
            self._exp.pop(key, None)

    async def expire(self, key: str, ttl: int) -> None:
        if key in self._kv or key in self._zsets:
            self._exp[key] = time.time() + int(ttl)

    async def ttl(self, key: str) -> int:
        if not self._alive(key):
            return -2
        exp = self._exp.get(key)
        if exp is None:
            return -1
        return max(0, int(exp - time.time()))

    async def zremrangebyscore(self, key: str, min_s: float, max_s: float) -> int:
        zs = self._zsets.get(key, {})
        drop = [m for m, sc in zs.items() if min_s <= sc <= max_s]
        for m in drop:
            del zs[m]
        if zs:
            self._zsets[key] = zs
        else:
            self._zsets.pop(key, None)
        return len(drop)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        zs = self._zsets.setdefault(key, {})
        for m, sc in mapping.items():
            zs[m] = float(sc)
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def pipeline(self) -> _MemPipe:
        return _MemPipe(self)


class _MemPipe:
    def __init__(self, r: _MemRedis) -> None:
        self._r = r
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def zremrangebyscore(self, key: str, min_s: float, max_s: float) -> _MemPipe:
        self._ops.append(("zremrangebyscore", (key, min_s, max_s), {}))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> _MemPipe:
        self._ops.append(("zadd", (key, mapping), {}))
        return self

    def zcard(self, key: str) -> _MemPipe:
        self._ops.append(("zcard", (key,), {}))
        return self

    def expire(self, key: str, ttl: int) -> _MemPipe:
        self._ops.append(("expire", (key, ttl), {}))
        return self

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for name, args, _kw in self._ops:
            meth = getattr(self._r, name)
            out.append(await meth(*args))
        return out


# ---------------------------------------------------------------------------
# Rate limit unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_rate_limit_lockout_after_n_failures_and_reset_on_success() -> None:
    redis = _MemRedis()
    settings = Settings(
        environment="test",
        auth_rate_limit_enabled=True,
        auth_rate_limit_login_max=3,
        auth_rate_limit_ip_max=50,
        auth_rate_limit_window_seconds=60,
        auth_rate_limit_lockout_seconds=120,
    )
    limiter = AuthRateLimiter(redis, settings)
    login = "brute.me"
    ip = "203.0.113.9"

    assert (await limiter.check(login=login, ip=ip)).allowed is True
    for _ in range(2):
        locked = await limiter.record_failure(login=login, ip=ip)
        assert locked is None
        assert (await limiter.check(login=login, ip=ip)).allowed is True

    locked = await limiter.record_failure(login=login, ip=ip)
    assert locked is not None
    assert locked.allowed is False
    assert locked.retry_after >= 1
    assert locked.reason == "login_lockout"

    pre = await limiter.check(login=login, ip=ip)
    assert pre.allowed is False
    assert pre.retry_after >= 1

    await limiter.reset(login=login, ip=ip)
    assert (await limiter.check(login=login, ip=ip)).allowed is True


@pytest.mark.asyncio
async def test_auth_rate_limit_disabled_is_noop() -> None:
    redis = _MemRedis()
    settings = Settings(
        environment="test",
        auth_rate_limit_enabled=False,
        auth_rate_limit_login_max=1,
    )
    limiter = AuthRateLimiter(redis, settings)
    for _ in range(20):
        assert await limiter.record_failure(login="x", ip="1.2.3.4") is None
    assert (await limiter.check(login="x", ip="1.2.3.4")).allowed is True


# ---------------------------------------------------------------------------
# TOTP replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_replay_same_code_rejected_twice() -> None:
    """M-04: second verify of the same timestep fails."""
    redis = _MemRedis()
    # Minimal async session stub is heavy — use real engine only when Docker;
    # here we exercise the pure replay path via a lightweight fake row path.
    from unittest.mock import AsyncMock, MagicMock

    settings = Settings(secret_key="unit-test-secret-key-for-totp-replay")
    session = MagicMock()
    totp_svc = TOTPService(session, settings, redis)

    secret = pyotp.random_base32()
    encrypted = totp_svc._encrypt(secret)
    row = MagicMock()
    row.secret = encrypted
    row.enabled = True

    totp_svc._get_row = AsyncMock(return_value=row)  # type: ignore[method-assign]

    code = pyotp.TOTP(secret).now()
    assert await totp_svc.verify(42, code) is True
    assert await totp_svc.verify(42, code) is False


# ---------------------------------------------------------------------------
# Password rehash helpers
# ---------------------------------------------------------------------------


def test_needs_rehash_detects_legacy_not_bcrypt() -> None:
    assert needs_rehash(hashlib.sha1(b"pw").hexdigest())  # noqa: S324
    assert needs_rehash(hashlib.sha256(b"pw").hexdigest())
    assert not needs_rehash(hash_password("pw", cost=10))


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_login_rehashes_legacy_sha256_to_bcrypt(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """H-06: successful password login upgrades weak hash to BCRYPT:."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 600_000
    login = f"rehash.agent.{ns}"
    password = "legacy-pass-99"
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    assert detect_scheme(legacy) == "sha256"

    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Re', 'Hash', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": legacy, "t": NOW},
        )
    engine_sync.dispose()

    if sync_url.startswith("postgresql+psycopg2://"):
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    else:
        async_url = sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")

    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(
        secret_key="unit-test-secret-key",
        password_rehash_on_login=True,
        password_reject_weak_hashes=False,
    )
    sessions = SessionStore(_MemRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, password)
        assert user is not None
        assert user.login == login

        row = (await session.execute(select(Users).where(Users.id == user_id))).scalar_one()
        assert row.pw.startswith("BCRYPT:")
        assert verify_password(password, row.pw)
        assert detect_scheme(row.pw) == "bcrypt"

    await engine.dispose()


# ---------------------------------------------------------------------------
# CSRF Origin check
# ---------------------------------------------------------------------------


def _fake_request(
    *,
    method: str = "POST",
    path: str = "/api/v1/tickets",
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    host: str = "localhost:8000",
) -> Any:
    from starlette.requests import Request

    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    hdrs.append((b"host", host.encode()))
    cookie_header = ""
    if cookies:
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        hdrs.append((b"cookie", cookie_header.encode()))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": hdrs,
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
    }
    return Request(scope)


def test_csrf_cookie_post_origin_mismatch_rejected() -> None:
    settings = Settings(
        environment="test",
        csrf_origin_check_enabled=True,
        cors_origins="http://localhost:8000,http://localhost:5173",
        session_cookie_name="tiqora_session",
    )
    req = _fake_request(
        cookies={"tiqora_session": "tok"},
        headers={"origin": "https://evil.example"},
    )
    assert csrf_check_required(req, settings) is True
    assert origin_allowed(req, settings) is False


def test_csrf_same_origin_cookie_post_allowed() -> None:
    settings = Settings(
        environment="test",
        csrf_origin_check_enabled=True,
        cors_origins="http://localhost:8000",
        session_cookie_name="tiqora_session",
    )
    req = _fake_request(
        cookies={"tiqora_session": "tok"},
        headers={"origin": "http://localhost:8000"},
        host="localhost:8000",
    )
    assert csrf_check_required(req, settings) is True
    assert origin_allowed(req, settings) is True


def test_csrf_api_key_authorization_exempt() -> None:
    settings = Settings(
        environment="test",
        csrf_origin_check_enabled=True,
        session_cookie_name="tiqora_session",
    )
    req = _fake_request(
        cookies={"tiqora_session": "tok"},
        headers={
            "authorization": "Bearer tiqora_abc",
            "origin": "https://evil.example",
        },
    )
    assert csrf_check_required(req, settings) is False


def test_csrf_no_cookie_missing_origin_ok() -> None:
    """Login and other unauthenticated POSTs must not require Origin."""
    settings = Settings(
        environment="test",
        csrf_origin_check_enabled=True,
        session_cookie_name="tiqora_session",
    )
    req = _fake_request(path="/api/v1/auth/login", cookies=None, headers={})
    assert request_has_session_cookie(req, settings) is False
    assert csrf_check_required(req, settings) is False


@pytest.mark.asyncio
async def test_csrf_middleware_blocks_cookie_post_mismatch() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user
    from tiqora.domain.auth import AuthenticatedUser

    app = create_app(
        Settings(
            environment="test",
            csrf_origin_check_enabled=True,
            cors_origins="http://localhost:8000",
            session_cookie_name="tiqora_session",
        )
    )
    fake_user = AuthenticatedUser(
        id=1, login="root", first_name="R", last_name="Oot", auth_method="session"
    )
    app.dependency_overrides[get_current_user] = lambda: fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Cookie present + evil Origin → 403 before auth/handler.
        resp = await client.post(
            "/api/v1/agents/presence/ping",
            cookies={"tiqora_session": "dummy-session-token"},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

        # Same-origin Origin (matches Host "test") → passes CSRF (may 401/204).
        resp2 = await client.post(
            "/api/v1/agents/presence/ping",
            cookies={"tiqora_session": "dummy-session-token"},
            headers={"Origin": "http://test"},
        )
        assert resp2.status_code != 403

        # API key style Authorization exempt even with evil Origin.
        resp3 = await client.post(
            "/api/v1/agents/presence/ping",
            cookies={"tiqora_session": "dummy-session-token"},
            headers={
                "Origin": "https://evil.example",
                "Authorization": "Bearer tiqora_not_a_real_key",
            },
        )
        assert resp3.status_code != 403


# ---------------------------------------------------------------------------
# Logout cookie flags (M-10)
# ---------------------------------------------------------------------------


def test_logout_clear_cookie_matches_set_flags() -> None:
    from fastapi.responses import Response

    from tiqora.api.v1.auth import _clear_session_cookie, _set_session_cookie

    settings = Settings(
        environment="test",
        session_cookie_name="tiqora_session",
        session_cookie_secure=True,
        session_cookie_samesite="lax",
    )
    set_resp = Response()
    _set_session_cookie(set_resp, settings, "token-value")
    set_header = set_resp.headers.get("set-cookie", "")
    assert "tiqora_session=token-value" in set_header
    assert "Secure" in set_header or "secure" in set_header.lower()

    clear_resp = Response()
    _clear_session_cookie(clear_resp, settings)
    clear_header = clear_resp.headers.get("set-cookie", "")
    assert "tiqora_session=" in clear_header
    # Clearing must restate Secure/SameSite so browsers drop the cookie.
    assert "Secure" in clear_header or "secure" in clear_header.lower()
    assert "samesite=lax" in clear_header.lower()
