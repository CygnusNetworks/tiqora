"""StorageBackend protocol and DB MIME implementation for article attachments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.article import ArticleDataMimeAttachment


@dataclass(frozen=True, slots=True)
class AttachmentMeta:
    """Metadata for an article attachment (content may be streamed separately)."""

    id: int
    article_id: int
    filename: str | None
    content_type: str | None
    content_size: str | None
    content_id: str | None
    content_alternative: str | None
    disposition: str | None


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    """Full attachment including bytes."""

    meta: AttachmentMeta
    content: bytes


class StorageBackend(Protocol):
    """Pluggable attachment storage (DB MIME now; S3/FS later)."""

    async def list_attachments(self, article_id: int) -> list[AttachmentMeta]:
        """Return attachment metadata for an article (no content)."""
        ...

    async def get_attachment(self, attachment_id: int) -> AttachmentContent | None:
        """Load one attachment by primary key."""
        ...

    async def get_by_content_id(self, article_id: int, content_id: str) -> AttachmentContent | None:
        """Resolve a ``cid:`` reference for an article."""
        ...


def _as_bytes(value: bytes | memoryview | str | None) -> bytes:
    """Normalise BLOB/bytea values from both MySQL and Postgres drivers."""
    if value is None:
        return b""
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        # Mis-decoded PG hex escape (\\x68656c6c6f) from some insert paths
        if value.startswith("\\x"):
            try:
                return bytes.fromhex(value[2:])
            except ValueError:
                return value.encode("utf-8")
        return value.encode("utf-8")
    return bytes(value)


def _row_to_meta(row: ArticleDataMimeAttachment) -> AttachmentMeta:
    return AttachmentMeta(
        id=row.id,
        article_id=row.article_id,
        filename=row.filename,
        content_type=row.content_type,
        content_size=row.content_size,
        content_id=row.content_id,
        content_alternative=row.content_alternative,
        disposition=row.disposition,
    )


class DbMimeStorage:
    """Attachments stored as LONGBLOB/bytea in ``article_data_mime_attachment``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_attachments(self, article_id: int) -> list[AttachmentMeta]:
        result = await self._session.execute(
            select(ArticleDataMimeAttachment)
            .where(ArticleDataMimeAttachment.article_id == article_id)
            .order_by(ArticleDataMimeAttachment.id)
        )
        return [_row_to_meta(r) for r in result.scalars().all()]

    async def get_attachment(self, attachment_id: int) -> AttachmentContent | None:
        result = await self._session.execute(
            select(ArticleDataMimeAttachment).where(ArticleDataMimeAttachment.id == attachment_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return AttachmentContent(meta=_row_to_meta(row), content=_as_bytes(row.content))

    async def get_by_content_id(self, article_id: int, content_id: str) -> AttachmentContent | None:
        # Znuny stores content_id with or without angle brackets / cid: prefix.
        candidates = {
            content_id,
            content_id.strip("<>"),
            f"<{content_id.strip('<>')}>",
            content_id.removeprefix("cid:"),
            f"<{content_id.removeprefix('cid:')}>",
        }
        result = await self._session.execute(
            select(ArticleDataMimeAttachment).where(
                ArticleDataMimeAttachment.article_id == article_id,
                ArticleDataMimeAttachment.content_id.in_(candidates),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            # Fallback: case-insensitive / partial match on stripped cid
            all_atts = await self.list_attachments(article_id)
            needle = content_id.removeprefix("cid:").strip("<>").lower()
            for meta in all_atts:
                if meta.content_id and meta.content_id.strip("<>").lower() == needle:
                    return await self.get_attachment(meta.id)
            return None
        return AttachmentContent(meta=_row_to_meta(row), content=_as_bytes(row.content))
