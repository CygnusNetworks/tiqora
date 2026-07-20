"""KB ACL, slug auto-generation, attachments, and knowledge-bundle tests.

Covers the group-based visibility model (category<->permission_group M2M),
the author restriction (agents may only assign groups they hold ``rw`` on),
ACL enforcement on direct fetch, slug auto-generation/collision handling,
state-on-create, attachment upload/download, and the ACL-filtered knowledge
bundle. DB-only (no Meilisearch).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.kb.schemas import ArticleIn, CategoryIn
from tiqora.kb.service import KbForbidden, KbService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Unique id block to avoid collisions in the shared session-scoped DB.
WRITER = 3210  # rw on GROUP
MEMBER = 3211  # ro on GROUP
OUTSIDER = 3212  # no membership
GROUP = 3210


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
        # Idempotent: the function-scoped `factory` fixture re-seeds the
        # session-scoped DB before every test, so tolerate existing rows.
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (:w, 'kbacl.writer', :pw, 'W', 'R', 1, :t, 1, :t, 1),"
                "        (:m, 'kbacl.member', :pw, 'M', 'E', 1, :t, 1, :t, 1),"
                "        (:o, 'kbacl.outsider', :pw, 'O', 'U', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "m": MEMBER, "o": OUTSIDER, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:g, 'kb-acl-group', 1, :t, 1, :t, 1)"
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
async def test_author_restriction_on_group_assignment(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        # Writer (rw on GROUP) may assign it.
        async with session.begin():
            cat = await svc.create_category(
                WRITER, CategoryIn(name="Runbooks", permission_group_ids=[GROUP])
            )
        assert await svc.category_group_ids(cat.id) == [GROUP]
        await session.commit()

        # Member (only ro) may NOT assign it.
        with pytest.raises(KbForbidden):
            async with session.begin():
                await svc.create_category(
                    MEMBER, CategoryIn(name="Nope1", permission_group_ids=[GROUP])
                )

        # Outsider (no membership) may NOT assign it.
        with pytest.raises(KbForbidden):
            async with session.begin():
                await svc.create_category(
                    OUTSIDER, CategoryIn(name="Nope2", permission_group_ids=[GROUP])
                )


@pytest.mark.asyncio
async def test_acl_enforced_on_direct_fetch(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            restricted = await svc.create_category(
                WRITER, CategoryIn(name="Secret", permission_group_ids=[GROUP])
            )
            art = await svc.create_article(
                WRITER,
                ArticleIn(category_id=restricted.id, title="Secret Steps", content_md="x"),
            )
            public = await svc.create_category(WRITER, CategoryIn(name="Open"))
            pub_art = await svc.create_article(
                WRITER, ArticleIn(category_id=public.id, title="Open Steps", content_md="y")
            )
        art_id, pub_id = art.id, pub_art.id
        await session.commit()

        # Member (ro on GROUP) and writer (rw implies ro) can read the restricted one.
        assert (await svc.get_article_scoped(MEMBER, art_id)).id == art_id
        assert (await svc.get_article_scoped(WRITER, art_id)).id == art_id
        # Outsider cannot.
        with pytest.raises(KbForbidden):
            await svc.get_article_scoped(OUTSIDER, art_id)
        # But the unrestricted article is visible to everyone.
        assert (await svc.get_article_scoped(OUTSIDER, pub_id)).id == pub_id


@pytest.mark.asyncio
async def test_slug_autogen_and_collision(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(WRITER, CategoryIn(name="Docs Area"))
            a1 = await svc.create_article(
                WRITER, ArticleIn(category_id=cat.id, title="Reset Password", content_md="a")
            )
            a2 = await svc.create_article(
                WRITER, ArticleIn(category_id=cat.id, title="Reset Password", content_md="b")
            )
        assert cat.slug == "docs-area"
        assert a1.slug == "reset-password"
        assert a2.slug == "reset-password-2"


@pytest.mark.asyncio
async def test_state_on_create(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(WRITER, CategoryIn(name="Stateful"))
            drafted = await svc.create_article(
                WRITER, ArticleIn(category_id=cat.id, title="D", content_md="x")
            )
            reviewed = await svc.create_article(
                WRITER,
                ArticleIn(category_id=cat.id, title="R", content_md="x", state="review"),
            )
        assert drafted.state == "draft"
        assert reviewed.state == "review"


@pytest.mark.asyncio
async def test_attachment_roundtrip(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(WRITER, CategoryIn(name="WithFiles"))
            art = await svc.create_article(
                WRITER, ArticleIn(category_id=cat.id, title="Has attachment", content_md="x")
            )
            att = await svc.add_attachment(art.id, "diagram.png", "image/png", b"\x89PNG data")
        art_id, att_id = art.id, att.id
        await session.commit()

        fetched = await svc.get_attachment(art_id, att_id)
        assert fetched.filename == "diagram.png"
        assert fetched.content == b"\x89PNG data"
        assert [a.id for a in await svc.list_attachments(art_id)] == [att_id]
        await session.commit()  # close the autobegin transaction from the reads

        async with session.begin():
            await svc.delete_attachment(art_id, att_id)
        assert await svc.list_attachments(art_id) == []


@pytest.mark.asyncio
async def test_knowledge_bundle_is_acl_filtered(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(
                WRITER, CategoryIn(name="Billing KB", permission_group_ids=[GROUP])
            )
            art = await svc.create_article(
                WRITER,
                ArticleIn(
                    category_id=cat.id,
                    title="Refund policy",
                    content_md="Refunds within 30 days.",
                    state="published",
                    tags=["billing"],
                ),
            )
        art_id = art.id
        await session.commit()

        member_bundle = await svc.get_knowledge(MEMBER, tags=["billing"])
        assert [row.id for row, _tags in member_bundle] == [art_id]
        assert member_bundle[0][1] == ["billing"]

        outsider_bundle = await svc.get_knowledge(OUTSIDER, tags=["billing"])
        assert outsider_bundle == []

        # Tag filter excludes non-matching tags.
        assert await svc.get_knowledge(MEMBER, tags=["nonexistent"]) == []
