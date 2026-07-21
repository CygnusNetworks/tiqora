"""Unit tests for Kerberos/SPNEGO negotiation.

``gssapi`` calls are indirected through ``_import_gssapi`` so we can swap in
a fake module here instead of requiring a real KDC/keytab. See
docs/deployment.md for manual KDC verification steps.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain import spnego as spnego_module
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.spnego import SpnegoService, SpnegoUnavailable, principal_to_login
from tiqora.domain.totp import TOTPService
from tiqora.znuny.password import hash_password


class _FakeCredentials:
    def __init__(self, usage: str) -> None:
        self.usage = usage


class _FakeSecurityContext:
    """Fake single-leg gssapi.SecurityContext: step() completes immediately."""

    def __init__(self, creds: _FakeCredentials, usage: str, *, principal: str) -> None:
        self.creds = creds
        self.usage = usage
        self.complete = False
        self._principal = principal
        self.initiator_name = principal

    def step(self, token: bytes) -> bytes:
        assert token == b"client-token"
        self.complete = True
        self.initiator_name = self._principal
        return b"server-token"


def _fake_gssapi_module(principal: str = "alice@EXAMPLE.COM") -> object:
    class _Module:
        Credentials = staticmethod(lambda usage: _FakeCredentials(usage))

        @staticmethod
        def SecurityContext(creds: _FakeCredentials, usage: str) -> _FakeSecurityContext:
            return _FakeSecurityContext(creds, usage, principal=principal)

    return _Module()


def test_principal_to_login_strips_realm_and_service_component() -> None:
    assert principal_to_login("alice@EXAMPLE.COM") == "alice"
    assert principal_to_login("HTTP/tiqora.example.com@EXAMPLE.COM") == "HTTP"
    assert principal_to_login("bob") == "bob"


@pytest.mark.asyncio
async def test_accept_returns_principal_with_mocked_gssapi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spnego_module, "_import_gssapi", lambda: _fake_gssapi_module("alice@EXAMPLE.COM")
    )
    settings = Settings(spnego_enabled=True)
    service = SpnegoService(settings)
    principal = await service.accept(b"client-token")
    assert principal == "alice@EXAMPLE.COM"
    assert principal_to_login(principal) == "alice"


@pytest.mark.asyncio
async def test_accept_raises_when_gssapi_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> object:
        raise SpnegoUnavailable("gssapi is not installed; install the 'kerberos' extra")

    monkeypatch.setattr(spnego_module, "_import_gssapi", _raise)
    settings = Settings(spnego_enabled=True)
    service = SpnegoService(settings)
    with pytest.raises(SpnegoUnavailable):
        await service.accept(b"client-token")


@pytest.mark.asyncio
async def test_spnego_endpoint_501_when_gssapi_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Negotiate header -> 401; Negotiate header + missing gssapi -> 501."""
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app

    def _raise() -> object:
        raise SpnegoUnavailable("gssapi is not installed")

    monkeypatch.setattr(spnego_module, "_import_gssapi", _raise)

    settings = Settings(spnego_enabled=True)
    app = create_app(settings)
    token = base64.b64encode(b"client-token").decode("ascii")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_no_header = await client.get("/api/v1/auth/spnego")
        assert resp_no_header.status_code == 401
        assert resp_no_header.headers.get("www-authenticate") == "Negotiate"

        resp = await client.get(
            "/api/v1/auth/spnego", headers={"Authorization": f"Negotiate {token}"}
        )
        assert resp.status_code == 501


@pytest.mark.asyncio
async def test_spnego_endpoint_404_when_disabled() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app

    settings = Settings(spnego_enabled=False)
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/spnego")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DB integration: sso_eligible gate + SSO skips 2FA + 302 on success
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)


def _seed_spnego_user(sync_url: str, login: str = "alice") -> int:
    ns = uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 600_000
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text("DELETE FROM users WHERE login = :login"),
            {"login": login},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Alice', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return user_id


async def _spnego_client(
    sync_url: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    principal: str = "alice@EXAMPLE.COM",
) -> tuple[Any, Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_redis

    monkeypatch.setattr(spnego_module, "_import_gssapi", lambda: _fake_gssapi_module(principal))

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fake_redis = _FakeRedis()

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    async def _override_get_redis() -> Any:
        return fake_redis

    settings = Settings(spnego_enabled=True, secret_key="unit-test-secret-key")
    app = create_app(settings)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = _override_get_redis
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test", follow_redirects=False)
    return client, engine, fake_redis


@pytest.mark.asyncio
@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_spnego_sso_eligible_false_returns_403(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_spnego_user(sync_url, login="alice")
    client, engine, _redis = await _spnego_client(sync_url, monkeypatch)
    token = base64.b64encode(b"client-token").decode("ascii")
    try:
        async with client:
            resp = await client.get(
                "/api/v1/auth/spnego", headers={"Authorization": f"Negotiate {token}"}
            )
        assert resp.status_code == 403
        assert "SSO not enabled" in resp.json()["detail"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_spnego_sso_eligible_true_full_session_skips_2fa(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eligible agent with TOTP enrolled still gets a full session (SSO skips 2FA)."""
    import pyotp

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id = _seed_spnego_user(sync_url, login="alice")

    engine_setup = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine_setup, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")
    async with factory() as session:
        await AuthConfigService(session).set(user_id, sso_eligible=True)
        totp = TOTPService(session, settings)
        secret, _ = await totp.enroll(user_id, "alice")
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True
        assert await totp.is_enabled(user_id) is True
    await engine_setup.dispose()

    client, engine, fake_redis = await _spnego_client(sync_url, monkeypatch)
    token = base64.b64encode(b"client-token").decode("ascii")
    try:
        async with client:
            resp = await client.get(
                "/api/v1/auth/spnego", headers={"Authorization": f"Negotiate {token}"}
            )
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/"
        # Full session cookie set (not PENDING:/ENROLL:)
        set_cookie = resp.headers.get("set-cookie", "")
        assert "tiqora_session=" in set_cookie
        # Redis holds a full session payload (user_id:login), not PENDING/ENROLL
        full_payloads = [
            v for k, v in fake_redis._store.items() if k.startswith("tiqora:session:") and ":" in v
        ]
        assert any(v.startswith(f"{user_id}:") for v in full_payloads)
        assert not any(v.startswith("PENDING:") or v.startswith("ENROLL:") for v in full_payloads)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_spnego_unknown_principal_403(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_spnego_user(sync_url, login="alice")
    client, engine, _redis = await _spnego_client(
        sync_url, monkeypatch, principal="nobody@EXAMPLE.COM"
    )
    token = base64.b64encode(b"client-token").decode("ascii")
    try:
        async with client:
            resp = await client.get(
                "/api/v1/auth/spnego", headers={"Authorization": f"Negotiate {token}"}
            )
        assert resp.status_code == 403
        assert "No local user" in resp.json()["detail"]
    finally:
        await engine.dispose()
