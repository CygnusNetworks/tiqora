"""DB integration tests for KbService: CRUD, versioning snapshot, tags.

Search/publish-to-Meilisearch scoping is covered separately in
``test_kb_search_meili.py`` (requires both the ``db`` and ``search`` markers).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.kb.schemas import ArticleIn, ArticleUpdateIn, CategoryIn, CategoryUpdateIn
from tiqora.kb.service import KbNotFound, KbService

pytestmark = pytest.mark.db


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _create_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


@pytest.fixture
async def factory(postgres_znuny_url: str) -> Any:
    _create_tables(postgres_znuny_url)
    async_url = _to_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        meili_url="http://localhost:1",  # never touched by these tests
    )


@pytest.mark.asyncio
async def test_category_crud(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(
                1, CategoryIn(name="Networking", slug="networking-1", permission_group_ids=[5])
            )
        cat_id = cat.id

        fetched = await svc.get_category(cat_id)
        assert fetched.name == "Networking"
        assert await svc.category_group_ids(cat_id) == [5]
        assert fetched.valid is True
        await session.commit()  # close autobegin transaction from the read above

        async with session.begin():
            updated = await svc.update_category(
                1, cat_id, CategoryUpdateIn(name="Networking & VPN", sort=3)
            )
        assert updated.name == "Networking & VPN"
        assert updated.sort == 3
        assert updated.slug == "networking-1"  # unset fields untouched

        cats = await svc.list_categories()
        assert any(c.id == cat_id for c in cats)
        await session.commit()

        async with session.begin():
            await svc.delete_category(1, cat_id)
        deleted = await svc.get_category(cat_id)
        assert deleted.valid is False


@pytest.mark.asyncio
async def test_get_missing_category_raises(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        with pytest.raises(KbNotFound):
            await svc.get_category(999999)


@pytest.mark.asyncio
async def test_article_create_and_versioning_snapshot(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(1, CategoryIn(name="Docs", slug="docs-1"))
        async with session.begin():
            article = await svc.create_article(
                1,
                ArticleIn(
                    category_id=cat.id,
                    title="How to reset password",
                    slug="reset-password-1",
                    content_md="## Steps\n\nDo the thing.",
                    tags=["auth", "faq"],
                ),
            )
        assert article.version == 1
        assert (await svc.get_tags(article.id)) == ["auth", "faq"]

        # No versions yet — nothing has changed since creation.
        assert await svc.list_versions(article.id) == []
        await session.commit()

        # Content-changing update: must snapshot v1 BEFORE applying, then bump to v2.
        async with session.begin():
            updated = await svc.update_article(
                2,
                article.id,
                ArticleUpdateIn(content_md="## Steps\n\nDo the new thing."),
            )
        assert updated.version == 2
        assert updated.change_by == 2

        versions = await svc.list_versions(article.id)
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].title == "How to reset password"
        assert versions[0].content_md == "## Steps\n\nDo the thing."
        assert versions[0].changed_by == 1
        await session.commit()

        # Non-content-changing update (category only): version must NOT bump,
        # no new snapshot.
        async with session.begin():
            cat2 = await svc.create_category(1, CategoryIn(name="Docs2", slug="docs-2"))
            recategorized = await svc.update_article(
                3, article.id, ArticleUpdateIn(category_id=cat2.id)
            )
        assert recategorized.version == 2
        assert len(await svc.list_versions(article.id)) == 1
        await session.commit()

        # Title change also triggers a snapshot + version bump.
        async with session.begin():
            renamed = await svc.update_article(
                4, article.id, ArticleUpdateIn(title="How to reset your password")
            )
        assert renamed.version == 3
        versions_after = await svc.list_versions(article.id)
        assert len(versions_after) == 2
        assert {v.version for v in versions_after} == {1, 2}


@pytest.mark.asyncio
async def test_article_tags_replace(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(1, CategoryIn(name="Tags", slug="tags-cat-1"))
        async with session.begin():
            article = await svc.create_article(
                1,
                ArticleIn(
                    category_id=cat.id,
                    title="Tagged",
                    slug="tagged-1",
                    content_md="body",
                    tags=["a", "b"],
                ),
            )
        async with session.begin():
            await svc.update_article(1, article.id, ArticleUpdateIn(tags=["b", "c"]))
        assert (await svc.get_tags(article.id)) == ["b", "c"]


@pytest.mark.asyncio
async def test_article_list_and_delete(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        svc = KbService(session, _settings())
        async with session.begin():
            cat = await svc.create_category(1, CategoryIn(name="ListMe", slug="listme-1"))
        async with session.begin():
            a1 = await svc.create_article(
                1, ArticleIn(category_id=cat.id, title="A1", slug="a1-1", content_md="x")
            )
            a2 = await svc.create_article(
                1, ArticleIn(category_id=cat.id, title="A2", slug="a2-1", content_md="y")
            )

        listed = await svc.list_articles(category_id=cat.id)
        assert {a.id for a in listed} == {a1.id, a2.id}
        await session.commit()

        async with session.begin():
            await svc.delete_article(1, a1.id)
        after_delete = await svc.get_article(a1.id)
        assert after_delete.state == "archived"

        drafts = await svc.list_articles(category_id=cat.id, state="draft")
        assert {a.id for a in drafts} == {a2.id}
