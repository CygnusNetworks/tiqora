"""Env OIDC/LDAP appear as read-only provider rows (wave 1, Task 4).

Rows use the reserved ``env-*`` slugs; every test removes them again. No Znuny
ids are seeded.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.identity import env_providers
from tiqora.identity.config_models import LdapProviderConfig, OidcProviderConfig
from tiqora.identity.env_providers import (
    ensure_env_providers,
    env_runtime_config,
    runtime_config,
    sync_env_providers,
)
from tiqora.identity.store import get_provider_by_slug, list_providers

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-env"
_URLS: set[str] = set()
_CLEAN = (
    "DELETE FROM tiqora_identity_link WHERE provider_id IN"
    " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
    "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN"
    " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
    "DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'",
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt in _CLEAN:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def maker(request: pytest.FixtureRequest) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        _clean(url)


def _settings(**kw: Any) -> Settings:
    return Settings(environment="test", secret_key=KEY, **kw)


OIDC = {
    "oidc_enabled": True,
    "oidc_issuer": "https://idp.example.org/realms/x",
    "oidc_client_id": "tiqora",
    "oidc_client_secret": "s",
    "oidc_claim": "email",
    "oidc_redirect_uri": "https://helpdesk.example.org/api/v1/auth/oidc/callback",
}


async def _raw(session: AsyncSession, slug: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT config, secrets_enc, source, enabled, audience, require_sso_opt_in,"
                " jit_enabled FROM tiqora_identity_provider WHERE slug = :s"
            ),
            {"s": slug},
        )
    ).one_or_none()


def test_reset_clears_the_cache() -> None:
    # conftest resets before every test; the flag starts unset.
    assert env_providers._SYNCED_FINGERPRINT is None


async def test_oidc_row_is_created_read_only(maker: async_sessionmaker[AsyncSession]) -> None:
    settings = _settings(**OIDC)
    async with maker() as s:
        assert await sync_env_providers(s, settings) == ["env-oidc"]
        rec = await get_provider_by_slug(s, "env-oidc", secret_key=KEY)
        raw = await _raw(s, "env-oidc")
    assert rec is not None and rec.read_only and rec.source == "env"
    assert rec.enabled and rec.audience == "agent" and rec.kind == "oidc"
    assert rec.attribute_map.login == ["email"]
    assert rec.require_sso_opt_in is True and rec.jit_enabled is False
    assert rec.button_label is None and rec.sort == 0 and rec.match_by == "login"
    assert raw.config == "{}" and raw.secrets_enc is None and raw.source == "env"


async def test_runtime_config_comes_from_settings(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings(**OIDC)
    async with maker() as s:
        await sync_env_providers(s, settings)
        rec = await get_provider_by_slug(s, "env-oidc", secret_key=KEY)
    assert rec is not None
    cfg = runtime_config(rec, settings)
    assert isinstance(cfg, OidcProviderConfig)
    assert cfg.client_secret is not None and cfg.client_secret.get_secret_value() == "s"
    assert cfg.redirect_uri == OIDC["oidc_redirect_uri"]
    assert cfg.issuer == OIDC["oidc_issuer"] and cfg.client_id == "tiqora"
    # The row's JSON is ignored: a changed environment wins without any DB write.
    other = _settings(**{**OIDC, "oidc_client_id": "other"})
    new_cfg = env_runtime_config(rec, other)
    assert isinstance(new_cfg, OidcProviderConfig) and new_cfg.client_id == "other"


async def test_sync_is_idempotent_and_disable_keeps_row(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as s:
        await sync_env_providers(s, _settings(**OIDC))
        await sync_env_providers(s, _settings(**OIDC))
        rows = [r for r in await list_providers(s, secret_key=KEY) if r.slug == "env-oidc"]
        assert len(rows) == 1 and rows[0].enabled
        await sync_env_providers(s, _settings(**{**OIDC, "oidc_enabled": False}))
        rows = [r for r in await list_providers(s, secret_key=KEY) if r.slug == "env-oidc"]
        assert len(rows) == 1 and rows[0].enabled is False
        # Enabling again flips the same row back and follows a changed claim.
        await sync_env_providers(s, _settings(**{**OIDC, "oidc_claim": "upn"}))
        rec = await get_provider_by_slug(s, "env-oidc", secret_key=KEY)
    assert rec is not None and rec.enabled and rec.attribute_map.login == ["upn"]


async def test_disabled_env_creates_no_rows(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as s:
        assert await sync_env_providers(s, _settings()) == []
        assert [
            r for r in await list_providers(s, secret_key=KEY) if r.slug.startswith("env-")
        ] == []


async def test_ldap_rows_and_runtime_config(maker: async_sessionmaker[AsyncSession]) -> None:
    settings = _settings(
        ldap_enabled=True,
        ldap_host="ldap.example.org",
        ldap_bind_password="pw",  # noqa: S106
        customer_ldap_enabled=True,
        customer_ldap_host="cldap.example.org",
        customer_ldap_use_ssl=True,
        customer_ldap_port=636,
    )
    async with maker() as s:
        assert set(await sync_env_providers(s, settings)) == {"env-ldap", "env-customer-ldap"}
        agent = await get_provider_by_slug(s, "env-ldap", secret_key=KEY)
        cust = await get_provider_by_slug(s, "env-customer-ldap", secret_key=KEY)
    assert agent is not None and agent.kind == "ldap" and agent.audience == "agent"
    assert cust is not None and cust.kind == "ldap" and cust.audience == "customer"
    assert agent.sort == 1000 and cust.sort == 1000 and agent.read_only
    a_cfg = runtime_config(agent, settings)
    c_cfg = runtime_config(cust, settings)
    assert isinstance(a_cfg, LdapProviderConfig) and isinstance(c_cfg, LdapProviderConfig)
    assert a_cfg.host == "ldap.example.org"
    assert a_cfg.bind_password is not None and a_cfg.bind_password.get_secret_value() == "pw"
    assert c_cfg.host == "cldap.example.org" and c_cfg.use_ssl and c_cfg.port == 636


async def test_concurrent_sync_yields_one_row(maker: async_sessionmaker[AsyncSession]) -> None:
    settings = _settings(**OIDC)

    async def one() -> list[str]:
        async with maker() as s:
            return await sync_env_providers(s, settings)

    results = await asyncio.gather(*(one() for _ in range(4)))
    assert all(isinstance(r, list) for r in results)
    async with maker() as s:
        count = (
            await s.execute(
                text("SELECT COUNT(*) FROM tiqora_identity_provider WHERE slug = 'env-oidc'")
            )
        ).scalar_one()
    assert count == 1


async def test_ensure_syncs_once_per_fingerprint(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = env_providers.sync_env_providers

    async def counting(session: AsyncSession, settings: Settings) -> list[str]:
        nonlocal calls
        calls += 1
        return await real(session, settings)

    monkeypatch.setattr(env_providers, "sync_env_providers", counting)
    settings = _settings(**OIDC)
    async with maker() as s:
        await ensure_env_providers(s, settings)
        await ensure_env_providers(s, settings)
        assert calls == 1
        # A changed environment (new fingerprint) syncs again.
        await ensure_env_providers(s, _settings(**{**OIDC, "oidc_enabled": False}))
        assert calls == 2


async def test_failed_sync_is_not_cached(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    real = env_providers.sync_env_providers

    async def flaky(session: AsyncSession, settings: Settings) -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("db down")
        return await real(session, settings)

    monkeypatch.setattr(env_providers, "sync_env_providers", flaky)
    settings = _settings(**OIDC)
    async with maker() as s:
        with pytest.raises(RuntimeError):
            await ensure_env_providers(s, settings)
        await ensure_env_providers(s, settings)
        assert attempts == 2
        assert await get_provider_by_slug(s, "env-oidc", secret_key=KEY) is not None
