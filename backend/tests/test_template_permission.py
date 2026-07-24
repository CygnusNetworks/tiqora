"""Per-template edit-ACL tests for :class:`TemplatePermissionService`.

Covers the group-based edit grant (member needs ``rw``, ``ro`` is not enough),
the individual-user grant, the admin-only default (no ACL rows), and the
`can_edit_any` / `editable_template_ids` helpers that drive the /me flag and the
agent list. DB-only (Znuny schema + tiqora_* tables).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.template_permission import TemplatePermissionService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Unique id block to avoid collisions in the shared session-scoped DB.
WRITER = 5400  # rw on GROUP
MEMBER = 5401  # ro on GROUP only
OUTSIDER = 5402  # no membership, no grant
GROUP = 5400
TPL = 5400  # standard_template id


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
                " VALUES (:w, 'tpl.writer', :pw, 'W', 'R', 1, :t, 1, :t, 1),"
                "        (:m, 'tpl.member', :pw, 'M', 'E', 1, :t, 1, :t, 1),"
                "        (:o, 'tpl.outsider', :pw, 'O', 'U', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "m": MEMBER, "o": OUTSIDER, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:g, 'tpl-edit-group', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user"
                " (user_id, group_id, permission_key,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:w, :g, 'rw', :t, 1, :t, 1), (:m, :g, 'ro', :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "m": MEMBER, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO standard_template"
                " (id, name, text, content_type, template_type, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:tpl, 'ACL Answer', 'hi', 'text/plain', 'Answer', 1,"
                "         :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"tpl": TPL, "t": NOW},
        )
    engine.dispose()


@pytest.fixture
async def factory(postgres_znuny_url: str) -> Any:
    _seed(postgres_znuny_url)
    engine = create_async_engine(_to_async_url(postgres_znuny_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_no_acl_is_admin_only(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = TemplatePermissionService(session)
        # Clean slate for this template.
        await svc.set_editors(TPL, [], [])
        await session.commit()
        # Non-admins may not edit a template with no ACL rows.
        assert await svc.may_edit(WRITER, TPL) is False
        assert await svc.may_edit(OUTSIDER, TPL) is False
        assert await svc.can_edit_any(WRITER) is False
        assert await svc.editable_template_ids(WRITER) == set()


@pytest.mark.asyncio
async def test_group_grant_requires_rw(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = TemplatePermissionService(session)
        await svc.set_editors(TPL, [GROUP], [])
        await session.commit()
        # rw member may edit; ro-only member may not; outsider may not.
        assert await svc.may_edit(WRITER, TPL) is True
        assert await svc.may_edit(MEMBER, TPL) is False
        assert await svc.may_edit(OUTSIDER, TPL) is False
        assert await svc.can_edit_any(WRITER) is True
        assert await svc.can_edit_any(MEMBER) is False
        assert await svc.editable_template_ids(WRITER) == {TPL}
        assert await svc.editable_template_ids(MEMBER) == set()


@pytest.mark.asyncio
async def test_individual_user_grant(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = TemplatePermissionService(session)
        await svc.set_editors(TPL, [], [OUTSIDER])
        await session.commit()
        assert await svc.may_edit(OUTSIDER, TPL) is True
        assert await svc.may_edit(WRITER, TPL) is False  # no group grant now
        assert await svc.can_edit_any(OUTSIDER) is True
        assert await svc.editable_template_ids(OUTSIDER) == {TPL}


@pytest.mark.asyncio
async def test_get_set_editors_roundtrip(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = TemplatePermissionService(session)
        await svc.set_editors(TPL, [GROUP, GROUP], [OUTSIDER, OUTSIDER])  # dedupe
        await session.commit()
        group_ids, user_ids = await svc.get_editors(TPL)
        assert group_ids == [GROUP]
        assert user_ids == [OUTSIDER]
        # Clearing removes all rows → admin-only again.
        await svc.set_editors(TPL, [], [])
        await session.commit()
        assert await svc.get_editors(TPL) == ([], [])
