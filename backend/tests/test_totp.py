"""DB integration tests for TOTP 2FA enrollment + pending-2FA session isolation.

Mirrors the seeding pattern in test_compat_operations.py: real Znuny
``users`` row + ``TiqoraBase.metadata.create_all`` for ``tiqora_user_totp``,
against the mariadb/postgres testcontainer fixtures.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pyotp
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.domain.totp import TOTPService, TOTPStepUpRequired
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
PW_HASH = hash_password("secret123")


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)
            self._sets.pop(key, None)

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self._sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    return sync_url


def _seed_user(sync_url: str) -> tuple[int, str]:
    ns = uuid.uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 500_000
    login = f"totp.agent.{ns}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Totp', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return user_id, login


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enroll_confirm_verify_disable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings)

        assert await totp.is_enabled(user_id) is False

        secret, uri = await totp.enroll(user_id, login)
        assert uri.startswith("otpauth://totp/")
        assert login in uri
        # Not enabled until confirmed
        assert await totp.is_enabled(user_id) is False

        wrong_code = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"
        assert await totp.confirm(user_id, wrong_code) is False

        code = pyotp.TOTP(secret).now()
        assert await totp.confirm(user_id, code) is True
        assert await totp.is_enabled(user_id) is True

        assert await totp.verify(user_id, code) is True
        assert await totp.verify(user_id, "654321" if code != "654321" else "123456") is False

        # Disable requires a valid code
        assert await totp.disable(user_id, "000000" if code != "000000" else "111111") is False
        assert await totp.disable(user_id, pyotp.TOTP(secret).now()) is True
        assert await totp.is_enabled(user_id) is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_pending_session_cannot_resolve_as_full_session(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """After password login with TOTP enabled, the pending session must be
    invisible to the normal resolve_session()/get_current_user path — only
    promote_pending_session() (gated by a valid TOTP code) may upgrade it."""
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key", totp_pending_ttl_seconds=300)
    sessions = SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        totp = TOTPService(session, settings)
        secret, _uri = await totp.enroll(user_id, login)
        await totp.confirm(user_id, pyotp.TOTP(secret).now())

        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        assert await totp.is_enabled(user.id) is True

        pending_token = await auth.create_pending_session(user)

        # Pending session must NOT resolve via the normal session path —
        # this is what makes it inaccessible to every other endpoint.
        assert await auth.resolve_session(pending_token) is None

        # The auth.py endpoint gates promotion on a valid TOTP code; a wrong
        # code must never even be checked against promotion.
        assert await totp.verify(user_id, "000000") is False

        # Correct code verifies; the endpoint would then call promote.
        assert await totp.verify(user_id, pyotp.TOTP(secret).now()) is True

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_promote_pending_session_issues_full_session(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key", totp_pending_ttl_seconds=300)
    sessions = SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        pending_token = await auth.create_pending_session(user)

        result = await auth.promote_pending_session(pending_token)
        assert result is not None
        full_token, promoted_user = result
        assert promoted_user.login == login

        # New full token resolves normally; old pending token is consumed.
        resolved = await auth.resolve_session(full_token)
        assert resolved is not None
        assert resolved.login == login
        assert await auth.promote_pending_session(pending_token) is None

    await engine.dispose()


# ---------------------------------------------------------------------------
# Must-enroll (ENROLL) session + enforce_2fa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enroll_session_invisible_to_resolve_and_promotes_on_confirm(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """ENROLL session: not resolve_session; enroll+confirm promotes to full."""
    from tiqora.domain.auth_config import AuthConfigService

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key", totp_pending_ttl_seconds=300)
    sessions = SessionStore(_FakeRedis(), settings)  # type: ignore[arg-type]

    async with factory() as session:
        auth_cfg = AuthConfigService(session)
        await auth_cfg.set(user_id, enforce_2fa=True)
        assert await auth_cfg.effective_enforce(user_id) is True

        auth = AuthService(session, sessions, settings)
        user = await auth.authenticate_password(login, "secret123")
        assert user is not None
        assert await TOTPService(session, settings).is_enabled(user.id) is False

        enroll_token = await auth.create_enroll_session(user)

        # (a) does NOT resolve via normal session path
        assert await auth.resolve_session(enroll_token) is None
        assert await sessions.get(enroll_token) is None
        assert await sessions.get_enroll(enroll_token) == (user_id, login)

        # (b) enroll + confirm work against the user identity
        totp = TOTPService(session, settings)
        secret, _uri = await totp.enroll(user_id, login)
        code = pyotp.TOTP(secret).now()
        assert await totp.confirm(user_id, code) is True

        # (c) promote ENROLL → full session
        result = await auth.promote_enroll_session(enroll_token)
        assert result is not None
        full_token, promoted = result
        assert promoted.login == login
        resolved = await auth.resolve_session(full_token)
        assert resolved is not None
        assert resolved.login == login
        assert await auth.promote_enroll_session(enroll_token) is None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_effective_enforce_global_setting(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    from tiqora.domain.auth_config import AuthConfigService
    from tiqora.domain.settings_store import KEY_TOTP_ENFORCE_ALL, set_setting

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, _login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        svc = AuthConfigService(session)
        assert await svc.effective_enforce(user_id) is False
        await set_setting(session, KEY_TOTP_ENFORCE_ALL, "1")
        assert await svc.effective_enforce(user_id) is True
        await set_setting(session, KEY_TOTP_ENFORCE_ALL, "0")
        assert await svc.effective_enforce(user_id) is False
        await svc.set(user_id, enforce_2fa=True)
        assert await svc.effective_enforce(user_id) is True

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_effective_enforce_via_group_membership(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Member of an enforced group is forced to enroll; non-member is not."""
    import uuid
    from datetime import datetime

    from sqlalchemy import create_engine, text

    from tiqora.domain.auth_config import AuthConfigService, set_enforce_group_ids

    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, _login = _seed_user(sync_url)
    outsider_id, _ = _seed_user(sync_url)
    ns = uuid.uuid4().hex[:8]
    group_id = int(ns, 16) % 1_000_000 + 810_000
    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {
                "id": group_id,
                "name": f"totp-enforce-{ns}",
                "t": datetime(2024, 6, 1, 12, 0, 0),
            },
        )
        conn.execute(
            text(
                "INSERT INTO group_user"
                " (user_id, group_id, permission_key,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {
                "uid": user_id,
                "gid": group_id,
                "t": datetime(2024, 6, 1, 12, 0, 0),
            },
        )
    engine_sync.dispose()

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        svc = AuthConfigService(session)
        assert await svc.effective_enforce(user_id) is False
        assert await svc.effective_enforce(outsider_id) is False

        await set_enforce_group_ids(session, [group_id])
        assert await svc.effective_enforce(user_id) is True
        assert await svc.effective_enforce(outsider_id) is False

        await set_enforce_group_ids(session, [])
        assert await svc.effective_enforce(user_id) is False

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_force_disable_without_code(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings)
        secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True
        assert await totp.is_enabled(user_id) is True
        assert await totp.force_disable(user_id) is True
        assert await totp.is_enabled(user_id) is False
        assert await totp.force_disable(user_id) is False

    await engine.dispose()


# ---------------------------------------------------------------------------
# Re-enrollment step-up (security review H-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reenroll_without_current_code_is_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A session alone must not be able to replace a live second factor.

    Before the fix ``enroll()`` cleared ``enabled`` on the way in, so merely
    *calling* it turned 2FA off — a hijacked session could downgrade the
    account without ever completing the enrollment.
    """
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings, _FakeRedis())
        secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True
        assert await totp.is_enabled(user_id) is True

        with pytest.raises(TOTPStepUpRequired):
            await totp.enroll(user_id, login)
        with pytest.raises(TOTPStepUpRequired):
            await totp.enroll(user_id, login, current_code="000000")

        # The live factor survived both attempts, secret unchanged.
        assert await totp.is_enabled(user_id) is True
        assert await totp.verify(user_id, pyotp.TOTP(secret).now()) is True

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reenroll_with_current_code_keeps_old_factor_until_confirm(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """With a valid current code the swap is allowed — but only lands on confirm.

    An abandoned re-enrollment must leave the original authenticator working.
    """
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings, _FakeRedis())
        old_secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(old_secret).now()) is True

        new_secret, uri = await totp.enroll(
            user_id, login, current_code=pyotp.TOTP(old_secret).now()
        )
        assert new_secret != old_secret
        assert login in uri

        # Pending only: the live row still holds the OLD secret, untouched.
        assert await totp.is_enabled(user_id) is True
        live_row = await totp._get_row(user_id)  # noqa: SLF001 — asserting storage, not behaviour
        assert live_row is not None
        assert totp._decrypt(live_row.secret) == old_secret  # noqa: SLF001
        # A code from the old authenticator does not confirm the pending one.
        assert await totp.confirm(user_id, pyotp.TOTP(old_secret).now()) is False
        assert await totp.is_enabled(user_id) is True

        assert await totp.confirm(user_id, pyotp.TOTP(new_secret).now()) is True
        live_row = await totp._get_row(user_id)  # noqa: SLF001
        assert live_row is not None
        assert totp._decrypt(live_row.secret) == new_secret  # noqa: SLF001

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_first_enrollment_needs_no_step_up(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Nothing to protect yet — forced/first-time enrollment stays frictionless."""
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings, _FakeRedis())
        secret, _ = await totp.enroll(user_id, login)
        # Re-running an *unconfirmed* enrollment is also fine (user lost the QR).
        secret2, _ = await totp.enroll(user_id, login)
        assert secret2 != secret
        assert await totp.confirm(user_id, pyotp.TOTP(secret2).now()) is True

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_disable_consumes_timestep(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """A code already spent on a login cannot also be spent to switch 2FA off (L-4)."""
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_user(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")

    async with factory() as session:
        totp = TOTPService(session, settings, _FakeRedis())
        secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True

        code = pyotp.TOTP(secret).now()
        assert await totp.verify(user_id, code) is True  # spends this timestep
        assert await totp.disable(user_id, code) is False
        assert await totp.is_enabled(user_id) is True

    await engine.dispose()
