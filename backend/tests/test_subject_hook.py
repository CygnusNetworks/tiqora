"""Unit + DB tests for subject-hook config resolver and admin API."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import subject_config as admin_subject
from tiqora.api.v1.admin.schemas import SubjectConfigUpdate
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings  # noqa: F401 — register for create_all
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import set_setting
from tiqora.domain.subject_hook import (
    KEY_SUBJECT_FORMAT,
    KEY_SUBJECT_HOOK,
    KEY_SUBJECT_HOOK_DIVIDER,
    KEY_SUBJECT_HOOK_ENABLED,
    load_subject_config,
)
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Quote the reserved word ``key`` for MySQL; Postgres accepts "key".
        if "mysql" in sync_url or "mariadb" in sync_url:
            conn.execute(text("DELETE FROM tiqora_settings WHERE `key` LIKE 'ticket.subject_%'"))
        else:
            conn.execute(text("DELETE FROM tiqora_settings WHERE \"key\" LIKE 'ticket.subject_%'"))
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _sysconfig_with(values: dict[str, Any] | None = None) -> SysConfig:
    store = values or {}

    async def _fetch(name: str) -> Any:
        return store.get(name)

    return SysConfig(fetch=_fetch)


@pytest.mark.asyncio
async def test_load_subject_config_defaults_when_both_absent(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            cfg = await load_subject_config(session, _sysconfig_with())
            assert cfg.enabled is True
            assert cfg.hook == "Ticket#"
            assert cfg.divider == ""
            assert cfg.subject_format == "Left"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_load_subject_config_tiqora_override_beats_sysconfig(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_setting(session, KEY_SUBJECT_HOOK, "Cygnus#")
            await set_setting(session, KEY_SUBJECT_HOOK_DIVIDER, "-")
            await set_setting(session, KEY_SUBJECT_FORMAT, "Right")
            await set_setting(session, KEY_SUBJECT_HOOK_ENABLED, "1")
            sc = _sysconfig_with(
                {
                    "Ticket::Hook": "Ticket#",
                    "Ticket::HookDivider": "",
                    "Ticket::SubjectFormat": "Left",
                }
            )
            cfg = await load_subject_config(session, sc)
            assert cfg.hook == "Cygnus#"
            assert cfg.divider == "-"
            assert cfg.subject_format == "Right"
            assert cfg.enabled is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_load_subject_config_empty_override_reinherits(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # Empty string stored → fall through to Znuny.
            await set_setting(session, KEY_SUBJECT_HOOK, "")
            sc = _sysconfig_with({"Ticket::Hook": "Cygnus#"})
            cfg = await load_subject_config(session, sc)
            assert cfg.hook == "Cygnus#"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_load_subject_config_disabled(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await set_setting(session, KEY_SUBJECT_HOOK_ENABLED, "0")
            cfg = await load_subject_config(session, _sysconfig_with())
            assert cfg.enabled is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_subject_config_get_put(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _ensure_tiqora_tables(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            out = await admin_subject.get_subject_config(_root_user(), session)
            assert out.enabled is True
            assert out.hook == "Ticket#"
            assert out.znuny.hook == "Ticket#"
            assert out.overrides.hook is None

            out2 = await admin_subject.put_subject_config(
                SubjectConfigUpdate(
                    enabled=True,
                    hook="Cygnus#",
                    divider="",
                    subject_format="Left",
                ),
                _root_user(),
                session,
            )
            assert out2.hook == "Cygnus#"
            assert out2.overrides.hook == "Cygnus#"
            assert out2.znuny.hook == "Ticket#"

            # Clear override → re-inherit Znuny.
            out3 = await admin_subject.put_subject_config(
                SubjectConfigUpdate(hook=None),
                _root_user(),
                session,
            )
            assert out3.hook == "Ticket#"
            assert out3.overrides.hook is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_subject_config_bad_format_422(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_subject.put_subject_config(
                    SubjectConfigUpdate(subject_format="Middle"),
                    _root_user(),
                    session,
                )
            assert exc_info.value.status_code == 422
    finally:
        await engine.dispose()
