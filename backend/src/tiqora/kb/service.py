"""KB CRUD, versioning, publish (chunk + Meilisearch index), and search.

Write methods do not commit — callers wrap calls in ``async with
session.begin():`` (same convention as ``ticket_write_service``). Every
content-changing article update snapshots the *current* row into
``tiqora_kb_article_version`` before applying the change, then bumps
``version``.

Indexing: ``publish()`` calls Meilisearch directly (no outbox hop). The
existing ``tiqora_event_outbox`` table is ticket-shaped (a bare ``ticket_id``
column, drained by ``worker/outbox_drain.py`` which always re-indexes
tickets) — generalising it to carry KB events would mean widening its schema
for a second concern. Since ``publish()`` is a single low-frequency
admin/agent action (unlike high-volume ticket writes, which need the outbox
to batch/absorb bursts), indexing synchronously inside the request is
simpler and keeps the KB index consistent with the DB the moment the API
call returns. Recorded as a deviation from the ticket outbox pattern in
``docs/compatibility.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.models.settings import MeilisearchSettings
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.kb.chunker import chunk_article
from tiqora.kb.models import (
    STATE_PUBLISHED,
    TiqoraKbArticle,
    TiqoraKbArticleTag,
    TiqoraKbArticleVersion,
    TiqoraKbCategory,
    TiqoraKbChunk,
    TiqoraKbTag,
)
from tiqora.kb.schemas import (
    ArticleIn,
    ArticleUpdateIn,
    CategoryIn,
    CategoryUpdateIn,
    KbSearchHit,
    KbSearchResponse,
)
from tiqora.permissions.engine import PermissionEngine


class KbNotFound(Exception):
    """Category or article not found."""


class KbService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        client: AsyncClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            self._client = AsyncClient(
                url=self._settings.meili_url,
                api_key=self._settings.meili_master_key,
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def create_category(self, user_id: int, data: CategoryIn) -> TiqoraKbCategory:
        now = datetime.now(UTC).replace(tzinfo=None)
        row = TiqoraKbCategory(
            parent_id=data.parent_id,
            name=data.name,
            slug=data.slug,
            permission_group_id=data.permission_group_id,
            customer_visible=data.customer_visible,
            sort=data.sort,
            valid=data.valid,
            create_by=user_id,
            create_time=now,
            change_by=user_id,
            change_time=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_category(self, category_id: int) -> TiqoraKbCategory:
        row = (
            await self._session.execute(
                select(TiqoraKbCategory).where(TiqoraKbCategory.id == category_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise KbNotFound(f"category {category_id} not found")
        return row

    async def list_categories(self) -> list[TiqoraKbCategory]:
        rows = (
            (await self._session.execute(select(TiqoraKbCategory).order_by(TiqoraKbCategory.sort)))
            .scalars()
            .all()
        )
        return list(rows)

    async def update_category(
        self, user_id: int, category_id: int, data: CategoryUpdateIn
    ) -> TiqoraKbCategory:
        row = await self.get_category(category_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(row, field, value)
        row.change_by = user_id
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return row

    async def delete_category(self, user_id: int, category_id: int) -> None:
        """Soft-delete: mark invalid (categories may still be referenced by articles)."""
        row = await self.get_category(category_id)
        row.valid = False
        row.change_by = user_id
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------

    async def create_article(self, user_id: int, data: ArticleIn) -> TiqoraKbArticle:
        now = datetime.now(UTC).replace(tzinfo=None)
        row = TiqoraKbArticle(
            category_id=data.category_id,
            title=data.title,
            slug=data.slug,
            language=data.language,
            content_md=data.content_md,
            version=1,
            create_by=user_id,
            create_time=now,
            change_by=user_id,
            change_time=now,
        )
        self._session.add(row)
        await self._session.flush()
        if data.tags:
            await self.set_tags(row.id, data.tags)
        return row

    async def get_article(self, article_id: int) -> TiqoraKbArticle:
        row = (
            await self._session.execute(
                select(TiqoraKbArticle).where(TiqoraKbArticle.id == article_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise KbNotFound(f"article {article_id} not found")
        return row

    async def list_articles(
        self, *, category_id: int | None = None, state: str | None = None
    ) -> list[TiqoraKbArticle]:
        stmt = select(TiqoraKbArticle)
        if category_id is not None:
            stmt = stmt.where(TiqoraKbArticle.category_id == category_id)
        if state is not None:
            stmt = stmt.where(TiqoraKbArticle.state == state)
        stmt = stmt.order_by(TiqoraKbArticle.change_time.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_tags(self, article_id: int) -> list[str]:
        rows = await self._session.execute(
            select(TiqoraKbTag.name)
            .join(TiqoraKbArticleTag, TiqoraKbArticleTag.tag_id == TiqoraKbTag.id)
            .where(TiqoraKbArticleTag.article_id == article_id)
            .order_by(TiqoraKbTag.name)
        )
        return [r[0] for r in rows.all()]

    async def set_tags(self, article_id: int, tag_names: list[str]) -> None:
        await self._session.execute(
            delete(TiqoraKbArticleTag).where(TiqoraKbArticleTag.article_id == article_id)
        )
        for name in dict.fromkeys(t.strip() for t in tag_names if t.strip()):
            tag = (
                await self._session.execute(select(TiqoraKbTag).where(TiqoraKbTag.name == name))
            ).scalar_one_or_none()
            if tag is None:
                tag = TiqoraKbTag(name=name)
                self._session.add(tag)
                await self._session.flush()
            self._session.add(TiqoraKbArticleTag(article_id=article_id, tag_id=tag.id))
        await self._session.flush()

    async def _snapshot_version(self, row: TiqoraKbArticle) -> None:
        """Write the article's current state to tiqora_kb_article_version."""
        self._session.add(
            TiqoraKbArticleVersion(
                article_id=row.id,
                version=row.version,
                title=row.title,
                content_md=row.content_md,
                changed_by=row.change_by,
                changed_at=row.change_time,
            )
        )
        await self._session.flush()

    async def update_article(
        self, user_id: int, article_id: int, data: ArticleUpdateIn
    ) -> TiqoraKbArticle:
        row = await self.get_article(article_id)
        content_changed = (data.title is not None and data.title != row.title) or (
            data.content_md is not None and data.content_md != row.content_md
        )

        if content_changed:
            await self._snapshot_version(row)
            row.version += 1

        if data.category_id is not None:
            row.category_id = data.category_id
        if data.title is not None:
            row.title = data.title
        if data.content_md is not None:
            row.content_md = data.content_md
        if data.language is not None:
            row.language = data.language
        if data.state is not None:
            row.state = data.state
        if data.tags is not None:
            await self.set_tags(article_id, data.tags)

        row.change_by = user_id
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()
        return row

    async def delete_article(self, user_id: int, article_id: int) -> None:
        """Soft-delete: archive (history/chunks are kept for audit/citations)."""
        row = await self.get_article(article_id)
        row.state = "archived"
        row.change_by = user_id
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

    async def list_versions(self, article_id: int) -> list[TiqoraKbArticleVersion]:
        rows = await self._session.execute(
            select(TiqoraKbArticleVersion)
            .where(TiqoraKbArticleVersion.article_id == article_id)
            .order_by(TiqoraKbArticleVersion.version.desc())
        )
        return list(rows.scalars().all())

    # ------------------------------------------------------------------
    # Publish: chunk + Meilisearch index
    # ------------------------------------------------------------------

    async def ensure_index(self) -> None:
        client = await self._get_client()
        index_name = self._settings.meili_kb_index
        indexes = await client.get_indexes() or []
        names = {idx.uid for idx in indexes}
        if index_name not in names:
            await client.create_index(index_name, primary_key="id")
        index = client.index(index_name)
        settings = MeilisearchSettings(
            filterable_attributes=[
                "article_id",
                "language",
                "customer_visible",
                "permission_group_id",
            ],
            sortable_attributes=["article_id", "seq"],
            searchable_attributes=["title", "heading_path", "content"],
        )
        task = await index.update_settings(settings)
        await client.wait_for_task(task.task_uid, timeout_in_ms=60_000)

    async def publish(self, user_id: int, article_id: int) -> TiqoraKbArticle:
        """Publish an article: mark published, re-chunk, re-index in Meilisearch."""
        row = await self.get_article(article_id)
        row.state = STATE_PUBLISHED
        row.change_by = user_id
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
        await self._session.flush()

        chunks = chunk_article(row.content_md, article_title=row.title)

        await self._session.execute(
            delete(TiqoraKbChunk).where(TiqoraKbChunk.article_id == article_id)
        )
        chunk_rows = [
            TiqoraKbChunk(
                article_id=article_id,
                version=row.version,
                seq=c.seq,
                heading_path=c.heading_path,
                anchor=c.anchor,
                content_md=c.content_md,
                token_count=c.token_count,
            )
            for c in chunks
        ]
        self._session.add_all(chunk_rows)
        await self._session.flush()

        category = await self.get_category(row.category_id)
        docs = [
            {
                "id": f"{article_id}-{chunk_row.id}",
                "article_id": article_id,
                "chunk_id": chunk_row.id,
                "title": row.title,
                "heading_path": chunk_row.heading_path or "",
                "anchor": chunk_row.anchor or "",
                "content": chunk_row.content_md,
                "language": row.language,
                "customer_visible": category.customer_visible,
                "permission_group_id": category.permission_group_id,
            }
            for chunk_row in chunk_rows
        ]

        await self.ensure_index()
        client = await self._get_client()
        index = client.index(self._settings.meili_kb_index)
        if docs:
            task = await index.add_documents(docs)
            await client.wait_for_task(task.task_uid, timeout_in_ms=120_000)
        return row

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search_agent(
        self, user_id: int, query: str, *, limit: int = 20, offset: int = 0
    ) -> KbSearchResponse:
        """Agent-scoped search: filter by the caller's permission_group_id membership.

        A chunk with ``permission_group_id IS NULL`` is visible to every agent.
        """
        pe = PermissionEngine(self._session)
        allowed_groups = await pe.groups_for_permission(user_id, "ro")
        if allowed_groups:
            group_clause = (
                f"permission_group_id IN [{','.join(str(g) for g in sorted(allowed_groups))}]"
            )
            filter_str = f"(permission_group_id IS NULL OR {group_clause})"
        else:
            filter_str = "permission_group_id IS NULL"
        return await self._search(query, filter_str, limit=limit, offset=offset)

    async def search_customer(
        self, query: str, *, limit: int = 20, offset: int = 0
    ) -> KbSearchResponse:
        """Portal-scoped search: only chunks from customer_visible categories."""
        return await self._search(query, "customer_visible = true", limit=limit, offset=offset)

    async def _search(
        self, query: str, filter_str: str, *, limit: int, offset: int
    ) -> KbSearchResponse:
        await self.ensure_index()
        client = await self._get_client()
        index = client.index(self._settings.meili_kb_index)
        result = await index.search(
            query,
            filter=filter_str,
            limit=min(limit, 100),
            offset=offset,
        )
        hits: list[KbSearchHit] = []
        for raw in result.hits or []:
            h: dict[str, Any] = raw if isinstance(raw, dict) else dict(raw)
            hits.append(
                KbSearchHit(
                    article_id=int(h["article_id"]),
                    chunk_id=int(h["chunk_id"]),
                    title=h.get("title", ""),
                    heading_path=h.get("heading_path", ""),
                    anchor=h.get("anchor", ""),
                    content=h.get("content", ""),
                    language=h.get("language", "en"),
                    customer_visible=bool(h.get("customer_visible", False)),
                    permission_group_id=h.get("permission_group_id"),
                )
            )
        return KbSearchResponse(
            query=query,
            hits=hits,
            estimated_total=int(result.estimated_total_hits or len(hits)),
        )
