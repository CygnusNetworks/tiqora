"""Tests for GET /kb/tags (ACL-scoped tag counts) and the multi-tag OR filter
on ``KbService.list_articles``.

Uses its own seeded users/group in the 891xx id block (dedicated block per
the assigned KB-tags task, to avoid collisions with other tests/agents
running against the same shared Testcontainer DB).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.kb.schemas import ArticleIn, ArticleUpdateIn, CategoryIn
from tiqora.kb.service import KbService
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# Dedicated id block to avoid collisions with other parallel test files.
WRITER = 89100  # rw on GROUP
MEMBER = 89101  # ro on GROUP
OUTSIDER = 89102  # no membership
GROUP = 89100


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
                " VALUES (:w, 'kbtags.writer', :pw, 'W', 'R', 1, :t, 1, :t, 1),"
                "        (:m, 'kbtags.member', :pw, 'M', 'E', 1, :t, 1, :t, 1),"
                "        (:o, 'kbtags.outsider', :pw, 'O', 'U', 1, :t, 1, :t, 1)"
                " ON CONFLICT DO NOTHING"
            ),
            {"w": WRITER, "m": MEMBER, "o": OUTSIDER, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:g, 'kb-tags-group', 1, :t, 1, :t, 1)"
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
async def test_list_tags_acl_scoped_counts(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            restricted = await svc.create_category(
                WRITER, CategoryIn(name="Restricted KB", permission_group_ids=[GROUP])
            )
            public = await svc.create_category(WRITER, CategoryIn(name="Public KB"))
            await svc.create_article(
                WRITER,
                ArticleIn(
                    category_id=restricted.id,
                    title="Restricted only",
                    content_md="x",
                    tags=["restricted-tag", "shared-tag"],
                ),
            )
            await svc.create_article(
                WRITER,
                ArticleIn(
                    category_id=public.id,
                    title="Public only",
                    content_md="y",
                    tags=["public-tag", "shared-tag"],
                ),
            )
            # A tag with zero visible articles: create it via an article, then
            # detach it again — the tag row survives, the association doesn't.
            orphan = await svc.create_article(
                WRITER,
                ArticleIn(
                    category_id=public.id,
                    title="Will lose its tag",
                    content_md="z",
                    tags=["orphan-tag"],
                ),
            )
            await svc.update_article(WRITER, orphan.id, ArticleUpdateIn(tags=[]))
        await session.commit()

        # Other test files create KB tags in the same shared database (this
        # runs inside the full suite on CI) — assert only over OUR tag names
        # instead of the full result.
        ours = {"orphan-tag", "public-tag", "restricted-tag", "shared-tag"}

        member_all = await svc.list_tags(MEMBER)
        member_tags = {name: count for name, count in member_all if name in ours}
        assert member_tags == {
            "orphan-tag": 0,
            "public-tag": 1,
            "restricted-tag": 1,
            "shared-tag": 2,
        }
        # Sorted by name (over the full result, our subset included).
        assert [name for name, _ in member_all] == sorted(name for name, _ in member_all)

        outsider_all = await svc.list_tags(OUTSIDER)
        outsider_tags = {name: count for name, count in outsider_all if name in ours}
        assert outsider_tags == {
            "orphan-tag": 0,
            "public-tag": 1,
            "restricted-tag": 0,  # can't see the restricted category's article
            "shared-tag": 1,  # only the public one is visible
        }


@pytest.mark.asyncio
async def test_list_articles_multi_tag_or_filter(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(WRITER, CategoryIn(name="Multi-tag"))
            a = await svc.create_article(
                WRITER,
                ArticleIn(category_id=cat.id, title="Alpha", content_md="a", tags=["alpha"]),
            )
            b = await svc.create_article(
                WRITER,
                ArticleIn(category_id=cat.id, title="Beta", content_md="b", tags=["beta"]),
            )
            c = await svc.create_article(
                WRITER,
                ArticleIn(category_id=cat.id, title="Gamma", content_md="c", tags=["gamma"]),
            )
        await session.commit()

        # Single tag behaves exactly as before (regression).
        single = await svc.list_articles(tag="alpha", user_id=WRITER)
        assert [r.id for r in single] == [a.id]

        # Comma-separated list is a union (OR).
        union = await svc.list_articles(tag="alpha,beta", user_id=WRITER)
        assert {r.id for r in union} == {a.id, b.id}
        assert c.id not in {r.id for r in union}

        # Whitespace/empty entries are tolerated and don't filter anything out.
        whitespace_only = await svc.list_articles(tag=" , ", user_id=WRITER)
        assert {a.id, b.id, c.id} <= {r.id for r in whitespace_only}

        # Whitespace around real tag names is trimmed.
        spaced = await svc.list_articles(tag=" alpha , beta ", user_id=WRITER)
        assert {r.id for r in spaced} == {a.id, b.id}


async def test_tags_for_articles_bulk(factory: async_sessionmaker[AsyncSession]) -> None:
    """Batch tag lookup used by the list endpoint (tag pills per row)."""
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(WRITER, CategoryIn(name="Bulk-tags"))
            tagged = await svc.create_article(
                WRITER,
                ArticleIn(
                    category_id=cat.id, title="Tagged", content_md="x", tags=["zeta", "alpha2"]
                ),
            )
            untagged = await svc.create_article(
                WRITER,
                ArticleIn(category_id=cat.id, title="Untagged", content_md="y"),
            )
        await session.commit()

        by_article = await svc.tags_for_articles([tagged.id, untagged.id])
        assert by_article[tagged.id] == ["alpha2", "zeta"]
        assert untagged.id not in by_article
        assert await svc.tags_for_articles([]) == {}
