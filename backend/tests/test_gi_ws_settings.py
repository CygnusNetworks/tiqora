"""Tiqora-side webservice egress settings on MariaDB and PostgreSQL.

Seed band 51060-51079: webservice ids 51061/51062 (settings keys only, no Znuny rows).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import cleanup_webservices
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import set_setting
from tiqora.gi.egress import load_policy
from tiqora.gi.ws_settings import (
    WebserviceEgressSettings,
    get_egress_settings,
    set_egress_settings,
)

pytestmark = pytest.mark.db

WS, OTHER_WS = 51061, 51062


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _clean(sync_url: str, *, create: bool = False) -> None:
    engine = create_engine(sync_url)
    try:
        if create:
            TiqoraBase.metadata.create_all(engine, tables=[TiqoraSettings.__table__])
        with engine.begin() as conn:
            cleanup_webservices(conn, [WS, OTHER_WS])
    finally:
        engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_egress_settings_default_roundtrip_and_isolation(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _clean(sync_url, create=True)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await get_egress_settings(session, WS) == WebserviceEgressSettings((), False)
            await set_egress_settings(
                session, WS, WebserviceEgressSettings(("10.0.0.0/8", "CRM.example.org"), True)
            )
            got = await get_egress_settings(session, WS)
            assert got.allowed_targets == ("10.0.0.0/8", "crm.example.org")
            assert got.allow_insecure_tls is True
            assert await get_egress_settings(session, OTHER_WS) == WebserviceEgressSettings()
            pol = await load_policy(session, WS, Settings(gi_cert_dirs=["/etc/ssl/gi"]))
            assert [str(n) for n in pol.allow_networks] == ["10.0.0.0/8"]
            assert pol.allow_hosts == frozenset({"crm.example.org"})
            assert pol.allow_insecure_tls is True and pol.max_timeout == 300.0
            assert [p.name for p in pol.cert_dirs] == ["gi"]
            with pytest.raises(ValueError):
                await set_egress_settings(
                    session, WS, WebserviceEgressSettings(("127.0.0.0/8",), False)
                )
            assert (await get_egress_settings(session, WS)).allowed_targets == (
                "10.0.0.0/8",
                "crm.example.org",
            )
            # a damaged stored value falls back to the closed default
            await set_setting(session, f"gi.webservice.{WS}.egress", "{not json")
            assert await get_egress_settings(session, WS) == WebserviceEgressSettings()
            await set_setting(
                session,
                f"gi.webservice.{WS}.egress",
                '{"allowed_targets": ["127.0.0.1/32"], "allow_insecure_tls": true}',
            )
            assert await get_egress_settings(session, WS) == WebserviceEgressSettings()
    finally:
        await engine.dispose()
        _clean(sync_url)
