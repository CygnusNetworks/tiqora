"""DB integration tests for admin auth-config (SSO + 2FA policy)."""

from __future__ import annotations

import uuid
from datetime import datetime

import pyotp
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import auth_config as admin_auth_config
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import AuthConfigGlobalUpdate, AuthConfigUpdate
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.settings_store import KEY_TOTP_ENFORCE_ALL, get_setting_bool
from tiqora.domain.totp import TOTPService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

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


def _seed_agent(sync_url: str) -> tuple[int, str]:
    ns = uuid.uuid4().hex[:8]
    user_id = int(ns, 16) % 1_000_000 + 700_000
    login = f"authcfg.agent.{ns}"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Auth', 'Cfg', 1, :t, 1, :t, 1)"
            ),
            {"id": user_id, "login": login, "pw": PW_HASH, "t": NOW},
        )
    engine.dispose()
    return user_id, login


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1,
        login="root@localhost",
        first_name="Admin",
        last_name="User",
        auth_method="session",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_put_reset_global(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    user_id, login = _seed_agent(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(secret_key="unit-test-secret-key")
    admin = _admin()

    async with factory() as session:
        # Seed TOTP enabled so list shows totp_enabled=true
        totp = TOTPService(session, settings)
        secret, _ = await totp.enroll(user_id, login)
        assert await totp.confirm(user_id, pyotp.TOTP(secret).now()) is True

        page = await admin_auth_config.list_auth_config(
            admin, session, ListParams(page=1, page_size=500, valid="valid")
        )
        match = next((i for i in page.items if i.user_id == user_id), None)
        assert match is not None
        assert match.login == login
        assert match.totp_enabled is True
        assert match.sso_eligible is False
        assert match.enforce_2fa is False

        updated = await admin_auth_config.update_auth_config(
            user_id,
            AuthConfigUpdate(sso_eligible=True, enforce_2fa=True),
            admin,
            session,
        )
        assert updated.sso_eligible is True
        assert updated.enforce_2fa is True
        assert updated.totp_enabled is True

        cfg = await AuthConfigService(session).get(user_id)
        assert cfg.sso_eligible is True
        assert cfg.enforce_2fa is True

        await admin_auth_config.reset_2fa(user_id, admin, session, settings)
        assert await totp.is_enabled(user_id) is False

        # Global toggle
        g0 = await admin_auth_config.get_global_auth_config(admin, session)
        assert g0.enforce_all is False
        g1 = await admin_auth_config.put_global_auth_config(
            AuthConfigGlobalUpdate(enforce_all=True), admin, session
        )
        assert g1.enforce_all is True
        assert await get_setting_bool(session, KEY_TOTP_ENFORCE_ALL, default=False) is True
        # effective_enforce true via global even without per-agent flag on a fresh user
        assert await AuthConfigService(session).effective_enforce(user_id) is True

        await admin_auth_config.put_global_auth_config(
            AuthConfigGlobalUpdate(enforce_all=False), admin, session
        )

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_update_unknown_user_404(url_fixture: str, request: pytest.FixtureRequest) -> None:
    from fastapi import HTTPException

    sync_url: str = request.getfixturevalue(url_fixture)
    _seed_agent(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        with pytest.raises(HTTPException) as ei:
            await admin_auth_config.update_auth_config(
                9_999_999,
                AuthConfigUpdate(sso_eligible=True),
                _admin(),
                session,
            )
        assert ei.value.status_code == 404

    await engine.dispose()
