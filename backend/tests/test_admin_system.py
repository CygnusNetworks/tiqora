"""DB test for the admin "System-Info" API (GET ``/api/v1/admin/system``).

Follows the direct-service-call pattern from ``test_admin_daemons.py`` (local
testcontainer only, never Prod). The endpoint is a best-effort aggregate: the
datastore probe runs against the real container, while Redis/Docker/psutil
degrade gracefully — so this asserts structure and the DB probe, and only that
the optional sections are well-formed (available flag + reason), never that a
particular optional backend is reachable in CI.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import system as admin_system
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.worker.services import DAEMON_SERVICES

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_settings"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


class _DeadRedis:
    """Stand-in whose calls fail, so the Redis probe reports connected=False."""

    async def ping(self) -> bool:
        raise ConnectionError("no redis in test")

    async def info(self) -> dict[str, str]:
        raise ConnectionError("no redis in test")


async def test_get_system_info_aggregate(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    async_url = _mysql_async(mariadb_znuny_url)
    cfg = Settings(database_url=async_url)  # so dialect detection sees MySQL/Maria
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_system.get_system_info(
                admin=_root_user(),
                session=session,
                cfg=cfg,
                redis_client=_DeadRedis(),  # type: ignore[arg-type]
            )

            # App identity is always populated.
            assert out.app.name == cfg.app_name
            assert out.app.version
            assert out.app.python_version
            assert out.app.uptime_seconds >= 0

            # Services reuse the daemon catalog (one row per service).
            assert len(out.services) == len(DAEMON_SERVICES)

            # Database probe ran against the real container.
            assert out.datastores.database.connected is True
            assert out.datastores.database.dialect == "mysql"
            assert out.datastores.database.latency_ms is not None
            # OTRS/Znuny schema profile (Znuny 6.5 fixture).
            legacy = out.datastores.database.legacy_schema
            assert legacy is not None
            assert legacy.known is True
            assert legacy.groups_table == "permission_groups"
            assert legacy.profile_id == "znuny-6.5"

            # Redis degrades gracefully (stub raises).
            assert out.datastores.redis.connected is False

            # Optional sections are well-formed regardless of availability.
            assert isinstance(out.datastores.search.available, bool)
            assert isinstance(out.containers.available, bool)
            assert isinstance(out.containers.items, list)
            assert isinstance(out.host.available, bool)
            # When a section is unavailable it must explain why.
            if not out.containers.available:
                assert out.containers.reason
            if not out.host.available:
                assert out.host.reason
    finally:
        await engine.dispose()
