"""DB integration tests for WebAuthn passkeys as an alternative 2nd factor.

Verifier functions from py_webauthn are stubbed (real authenticators cannot run
in CI), mirroring test_spnego.py's fake-gssapi pattern. Challenges, Redis
single-use, sign_count, enforce last-factor, admin reset, and AuthMethodsOut
are covered against mariadb + postgres.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraUserPasskey
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.passkey import WebAuthnService, two_factor_enabled, webauthn_enabled
from tiqora.domain.totp import TOTPService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")
RP_ID = "localhost"
ORIGIN = "http://localhost:5173"
PUB_KEY = b"\x01fake-public-key-bytes\x02"


def _unique_cred_raw() -> bytes:
    """Globally unique credential id (table has UNIQUE on credential_id)."""
    return b"cred-" + uuid.uuid4().bytes


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


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    return sync_url


def _seed_user(sync_url: str) -> tuple[int, str]:
    ns = uuid.uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 600_000
    login = f"passkey.agent.{ns}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Pass', 'Key', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return user_id, login


def _settings(**overrides: Any) -> Settings:
    base = {
        "secret_key": "unit-test-secret-key",
        "totp_pending_ttl_seconds": 300,
        "webauthn_rp_id": RP_ID,
        "webauthn_rp_name": "Tiqora",
        "webauthn_origin": ORIGIN,
    }
    base.update(overrides)
    return Settings(**base)


def _fake_verified_registration(
    *,
    credential_id: bytes | None = None,
    public_key: bytes = PUB_KEY,
    sign_count: int = 0,
    aaguid: str = "00000000-0000-0000-0000-000000000000",
) -> SimpleNamespace:
    return SimpleNamespace(
        credential_id=credential_id if credential_id is not None else _unique_cred_raw(),
        credential_public_key=public_key,
        sign_count=sign_count,
        aaguid=aaguid,
    )


def _fake_verified_authentication(
    *,
    credential_id: bytes,
    new_sign_count: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        credential_id=credential_id,
        new_sign_count=new_sign_count,
    )


def _cred_payload(credential_id: bytes) -> dict[str, Any]:
    b64 = bytes_to_base64url(credential_id)
    return {
        "id": b64,
        "rawId": b64,
        "type": "public-key",
        "response": {
            "clientDataJSON": "e30",
            "attestationObject": "e30",
            "authenticatorData": "e30",
            "signature": "e30",
            "transports": ["internal"],
        },
    }


def test_webauthn_enabled_requires_rp_id_and_origin() -> None:
    assert webauthn_enabled(Settings()) is False
    assert webauthn_enabled(Settings(webauthn_rp_id=RP_ID)) is False
    assert webauthn_enabled(Settings(webauthn_origin=ORIGIN)) is False
    assert webauthn_enabled(_settings()) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_register_stores_credential_and_two_factor_enabled(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiqora.domain.passkey as passkey_mod

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()

    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        totp = TOTPService(session, settings)

        assert await two_factor_enabled(totp, svc, user_id) is False
        assert await svc.has_passkey(user_id) is False

        token = "session-token-reg-1"
        options = await svc.begin_registration(user_id=user_id, login=login, session_token=token)
        assert options["rp"]["id"] == RP_ID
        assert "challenge" in options
        # Challenge stored in Redis under the session token.
        assert any(k.endswith(token) for k in redis._store)

        row = await svc.finish_registration(
            user_id=user_id,
            session_token=token,
            credential=_cred_payload(cred_raw),
            name="YubiKey",
        )
        assert row is not None
        assert row.name == "YubiKey"
        assert row.credential_id == bytes_to_base64url(cred_raw)
        assert row.public_key == PUB_KEY
        assert row.sign_count == 0
        assert await svc.has_passkey(user_id) is True
        assert await two_factor_enabled(totp, svc, user_id) is True
        # Challenge is single-use (popped).
        assert not any(k.endswith(token) for k in redis._store)

        listed = await svc.list(user_id)
        assert len(listed) == 1
        assert listed[0].name == "YubiKey"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_challenge_single_use_rejects_replay(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiqora.domain.passkey as passkey_mod

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()
    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        token = "session-token-replay"
        await svc.begin_registration(user_id=user_id, login=login, session_token=token)
        first = await svc.finish_registration(
            user_id=user_id, session_token=token, credential=_cred_payload(cred_raw)
        )
        assert first is not None
        # Same challenge key already consumed.
        second = await svc.finish_registration(
            user_id=user_id, session_token=token, credential=_cred_payload(cred_raw)
        )
        assert second is None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_authenticate_promotes_pending_session(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passkey-only user: password → pending → authenticate-finish promotes."""
    import tiqora.domain.passkey as passkey_mod

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()
    sessions = SessionStore(redis, settings)  # type: ignore[arg-type]

    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )
    monkeypatch.setattr(
        passkey_mod,
        "verify_authentication_response",
        lambda **_kw: _fake_verified_authentication(credential_id=cred_raw, new_sign_count=3),
    )

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        totp = TOTPService(session, settings)
        reg_token = "reg-token"
        await svc.begin_registration(user_id=user_id, login=login, session_token=reg_token)
        row = await svc.finish_registration(
            user_id=user_id, session_token=reg_token, credential=_cred_payload(cred_raw)
        )
        assert row is not None
        assert await two_factor_enabled(totp, svc, user_id) is True
        assert await totp.is_enabled(user_id) is False

        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        pending_token = await auth.create_pending_session(user)
        assert await auth.resolve_session(pending_token) is None

        options = await svc.begin_authentication(user_id=user_id, session_token=pending_token)
        assert options is not None
        assert "challenge" in options
        allow = options.get("allowCredentials") or []
        assert len(allow) == 1

        used = await svc.finish_authentication(
            user_id=user_id,
            session_token=pending_token,
            credential=_cred_payload(cred_raw),
        )
        assert used is not None
        assert used.sign_count == 3
        assert used.last_used_at is not None

        promoted = await auth.promote_pending_session(pending_token)
        assert promoted is not None
        full_token, promoted_user = promoted
        assert promoted_user.login == login
        assert await auth.resolve_session(full_token) is not None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_sign_count_regression_detected(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiqora.domain.passkey as passkey_mod

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()

    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw, sign_count=5),
    )

    def _raise_regression(**_kw: Any) -> None:
        raise InvalidAuthenticationResponse("sign count did not increase")

    monkeypatch.setattr(passkey_mod, "verify_authentication_response", _raise_regression)

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        token = "reg"
        await svc.begin_registration(user_id=user_id, login=login, session_token=token)
        row = await svc.finish_registration(
            user_id=user_id, session_token=token, credential=_cred_payload(cred_raw)
        )
        assert row is not None
        assert row.sign_count == 5

        auth_token = "auth"
        await svc.begin_authentication(user_id=user_id, session_token=auth_token)
        failed = await svc.finish_authentication(
            user_id=user_id, session_token=auth_token, credential=_cred_payload(cred_raw)
        )
        assert failed is None
        # Sign count unchanged after rejection.
        reloaded = await svc.get_by_id(user_id, int(row.id))
        assert reloaded is not None
        assert reloaded.sign_count == 5

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_delete_last_factor_blocked_under_enforce(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the last passkey under effective_enforce is blocked at the API layer.

    Service-level delete still works; the enforce gate lives in the endpoint.
    """
    from fastapi import HTTPException

    import tiqora.domain.passkey as passkey_mod
    from tiqora.api.v1 import auth as auth_api
    from tiqora.domain.auth import AuthenticatedUser

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()
    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        totp = TOTPService(session, settings)
        token = "reg"
        await svc.begin_registration(user_id=user_id, login=login, session_token=token)
        row = await svc.finish_registration(
            user_id=user_id, session_token=token, credential=_cred_payload(cred_raw)
        )
        assert row is not None

        await AuthConfigService(session).set(user_id, enforce_2fa=True)
        user = AuthenticatedUser(
            id=user_id, login=login, first_name="P", last_name="K", auth_method="session"
        )

        with pytest.raises(HTTPException) as ei:
            await auth_api.passkey_delete(int(row.id), user, svc, totp, settings, session)
        assert ei.value.status_code == 400
        assert "last 2FA factor" in str(ei.value.detail)
        assert await svc.has_passkey(user_id) is True

        # With enforce off, delete succeeds.
        await AuthConfigService(session).set(user_id, enforce_2fa=False)
        resp = await auth_api.passkey_delete(int(row.id), user, svc, totp, settings, session)
        assert resp.status_code == 204
        assert await svc.has_passkey(user_id) is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_reset_2fa_clears_passkeys(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiqora.domain.passkey as passkey_mod
    from tiqora.api.v1.admin import auth_config as admin_auth_config
    from tiqora.domain.auth import AuthenticatedUser

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()
    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )

    async with factory() as session:
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        token = "reg"
        await svc.begin_registration(user_id=user_id, login=login, session_token=token)
        assert (
            await svc.finish_registration(
                user_id=user_id,
                session_token=token,
                credential=_cred_payload(cred_raw),
            )
            is not None
        )
        assert await svc.has_passkey(user_id) is True

        admin = AuthenticatedUser(
            id=1, login="root@localhost", first_name="A", last_name="B", auth_method="session"
        )
        await admin_auth_config.reset_2fa(user_id, admin, session, settings)
        assert await svc.has_passkey(user_id) is False
        assert await svc.count(user_id) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_auth_methods_webauthn_flag() -> None:
    from tiqora.api.v1.auth import auth_methods

    off = await auth_methods(Settings())  # type: ignore[arg-type]
    assert off.webauthn is False
    on = await auth_methods(_settings())  # type: ignore[arg-type]
    assert on.webauthn is True


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_two_factor_enabled_with_totp_only(
    url_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    import pyotp

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()

    async with factory() as session:
        totp = TOTPService(session, settings)
        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True
        assert await two_factor_enabled(totp, svc, user_id) is True
        assert await svc.has_passkey(user_id) is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enroll_session_promoted_on_passkey_register(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENROLL session + passkey register/finish promotes to a full session."""
    import tiqora.domain.passkey as passkey_mod

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = _settings()
    redis = _FakeRedis()
    sessions = SessionStore(redis, settings)  # type: ignore[arg-type]
    cred_raw = _unique_cred_raw()
    monkeypatch.setattr(
        passkey_mod,
        "verify_registration_response",
        lambda **_kw: _fake_verified_registration(credential_id=cred_raw),
    )

    async with factory() as session:
        await AuthConfigService(session).set(user_id, enforce_2fa=True)
        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        enroll_token = await auth.create_enroll_session(user)
        assert await auth.resolve_session(enroll_token) is None

        svc = WebAuthnService(session, redis, settings)  # type: ignore[arg-type]
        await svc.begin_registration(user_id=user_id, login=login, session_token=enroll_token)
        row = await svc.finish_registration(
            user_id=user_id,
            session_token=enroll_token,
            credential=_cred_payload(cred_raw),
            name="Phone",
        )
        assert row is not None

        promoted = await auth.promote_enroll_session(enroll_token)
        assert promoted is not None
        full_token, promoted_user = promoted
        assert promoted_user.login == login
        assert await auth.resolve_session(full_token) is not None

    await engine.dispose()


def test_model_table_name() -> None:
    assert TiqoraUserPasskey.__tablename__ == "tiqora_user_passkey"
    # Quiet unused import of json if mypy complains about structure.
    assert json.dumps({"ok": True})
