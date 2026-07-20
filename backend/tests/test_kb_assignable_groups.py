"""Tests for ``KbService.assignable_groups`` (group picker for categories).

A user with ``rw`` on a group sees it; a non-admin without ``rw`` does not.
DB-only (no Meilisearch). Uses a fresh id block / unique logins so it can run
alongside test_kb_acl.py and test_kb_search_meili.py against the shared
session-scoped test DB without users.login / PK collisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.kb.service import KbService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Unique id block to avoid collisions in the shared session-scoped DB.
WRITER = 3260  # rw on GROUP_A
READER = 3261  # ro on GROUP_A only
GROUP_A = 3260
GROUP_B = 3261  # nobody in this test holds rw on it


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:w, 'kbgrp.writer', :pw, 'W', 'R', 1, :t, 1, :t, 1),"
                "        (:r, 'kbgrp.reader', :pw, 'R', 'E', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "r": READER, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:a, 'kb-grp-alpha', 1, :t, 1, :t, 1),"
                "        (:b, 'kb-grp-beta', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"a": GROUP_A, "b": GROUP_B, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user"
                " (user_id, group_id, permission_key,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:w, :a, 'rw', :t, 1, :t, 1), (:r, :a, 'ro', :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "r": READER, "a": GROUP_A, "t": NOW},
        )
    engine.dispose()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        meili_url="http://localhost:1",  # never touched
    )


@pytest.fixture
async def factory(postgres_znuny_url: str) -> Any:
    _seed(postgres_znuny_url)
    engine = create_async_engine(_to_async_url(postgres_znuny_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_writer_sees_only_rw_group(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        groups = await svc.assignable_groups(WRITER)
        # Writer holds rw on GROUP_A but not GROUP_B; a non-admin sees only rw groups.
        assert (GROUP_A, "kb-grp-alpha") in groups
        assert GROUP_B not in [gid for gid, _ in groups]


@pytest.mark.asyncio
async def test_reader_without_rw_sees_nothing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        # Reader has only ro on GROUP_A and is not an admin -> no assignable groups.
        assert await svc.assignable_groups(READER) == []
