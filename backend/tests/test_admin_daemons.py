"""DB tests for the admin "Dienste" API (GET/PUT ``/api/v1/admin/daemons``).

Follows the direct-service-call pattern from ``test_mail_outbound_admin.py``
(local testcontainer only, never Prod): seed via raw SQL / ORM against a
fresh MariaDB DB, then exercise ``tiqora.api.v1.admin.daemons`` router
functions directly against a real async session.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import daemons as admin_daemons
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.api.v1.admin.schemas import DaemonUpdate
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Clear between tests sharing the session-scoped container.
        conn.execute(text("DELETE FROM tiqora_settings"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _seed_plain_user(sync_url: str) -> int:
    ns = uuid.uuid4().int % 1_000_000
    plain_id = 400_000 + ns
    login = f"plain.daemons.{ns}"
    pw = hash_password("secret")
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM group_user WHERE user_id = :id"), {"id": plain_id})
        conn.execute(text("DELETE FROM role_user WHERE user_id = :id"), {"id": plain_id})
        conn.execute(
            text("DELETE FROM users WHERE id = :id OR login = :login"),
            {"id": plain_id, "login": login},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, :pw, 'Plain', 'Daemon', 1, :t, 1, :t, 1)
                """
            ),
            {"id": plain_id, "login": login, "pw": pw, "t": NOW},
        )
    engine.dispose()
    return plain_id


async def test_get_daemons_defaults(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_daemons.list_daemons(_root_user(), session)
            assert len(out.services) == 10
            by_slug = {s.slug: s for s in out.services}

            poller = by_slug["poller"]
            assert poller.enabled is True
            assert poller.toggleable is False
            assert poller.interval_overridden is False

            assert by_slug["outbox"].enabled is True
            assert by_slug["gdpr_erasure_purge"].enabled is True
            assert by_slug["ai_audit_cleanup"].enabled is True
            assert by_slug["ai_audit_cleanup"].daily_at == "04:00"
            for slug in (
                "postmaster",
                "escalation",
                "notifications",
                "generic_agent",
                "gdpr_retention",
                "ai_worker",
            ):
                assert by_slug[slug].enabled is False, slug

            assert by_slug["gdpr_retention"].schedule == "daily"
            assert by_slug["gdpr_retention"].daily_at == "03:00"
            assert by_slug["gdpr_erasure_purge"].daily_at == "03:30"
            for svc_out in by_slug.values():
                if svc_out.schedule == "daily":
                    assert svc_out.interval_seconds is None
                else:
                    assert svc_out.interval_seconds is not None
    finally:
        await engine.dispose()


async def test_get_daemons_seeded_flags_and_status(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add_all(
                [
                    TiqoraSettings(key="daemon.postmaster.enabled", value="1"),
                    TiqoraSettings(key="daemon.postmaster.interval_seconds", value="90"),
                    TiqoraSettings(
                        key="daemon.postmaster.status.last_run",
                        value="2026-07-19T10:00:00+00:00",
                    ),
                    TiqoraSettings(
                        key="daemon.postmaster.status.last_ok",
                        value="2026-07-19T10:00:00+00:00",
                    ),
                    TiqoraSettings(
                        key="daemon.postmaster.status.last_result",
                        value=json.dumps({"fetched": 3}),
                    ),
                ]
            )
            await session.commit()

            out = await admin_daemons.list_daemons(_root_user(), session)
            postmaster = {s.slug: s for s in out.services}["postmaster"]
            assert postmaster.enabled is True
            assert postmaster.interval_seconds == 90
            assert postmaster.interval_overridden is True
            assert postmaster.last_run_at is not None
            assert postmaster.last_ok_at is not None
            assert postmaster.last_result == {"fetched": 3}
    finally:
        await engine.dispose()


async def test_put_daemon_toggle_and_interval_roundtrip(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_daemons.update_daemon(
                "escalation",
                DaemonUpdate(enabled=True, interval_seconds=120),
                _root_user(),
                session,
            )
            assert out.enabled is True
            assert out.interval_seconds == 120
            assert out.interval_overridden is True

            # interval_seconds=0 clears the override, reverting to the config default.
            out2 = await admin_daemons.update_daemon(
                "escalation", DaemonUpdate(interval_seconds=0), _root_user(), session
            )
            assert out2.interval_overridden is False
            assert out2.enabled is True  # untouched by the second PUT

            out3 = await admin_daemons.update_daemon(
                "escalation", DaemonUpdate(enabled=False), _root_user(), session
            )
            assert out3.enabled is False
    finally:
        await engine.dispose()


async def test_put_daemon_unknown_slug_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_daemons.update_daemon(
                    "does-not-exist", DaemonUpdate(enabled=True), _root_user(), session
                )
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_put_daemon_validation_422(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # poller is always on, cannot be toggled.
            with pytest.raises(HTTPException) as exc_toggle:
                await admin_daemons.update_daemon(
                    "poller", DaemonUpdate(enabled=False), _root_user(), session
                )
            assert exc_toggle.value.status_code == 422

            # below the 5s floor.
            with pytest.raises(HTTPException) as exc_floor:
                await admin_daemons.update_daemon(
                    "escalation", DaemonUpdate(interval_seconds=2), _root_user(), session
                )
            assert exc_floor.value.status_code == 422

            # daily services have no editable interval.
            with pytest.raises(HTTPException) as exc_daily:
                await admin_daemons.update_daemon(
                    "gdpr_retention", DaemonUpdate(interval_seconds=60), _root_user(), session
                )
            assert exc_daily.value.status_code == 422
    finally:
        await engine.dispose()


async def test_admin_gate_403_for_non_admin(mariadb_znuny_url: str) -> None:
    plain_id = _seed_plain_user(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            plain_user = AuthenticatedUser(
                id=plain_id,
                login="plain",
                first_name="Plain",
                last_name="Daemon",
                auth_method="session",
            )
            with pytest.raises(HTTPException) as exc_info:
                await get_admin_user(plain_user, session)
            assert exc_info.value.status_code == 403
    finally:
        await engine.dispose()
