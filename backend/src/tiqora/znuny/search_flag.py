"""Article search-index rebuild flag and Message-ID MD5 helpers.

Behavioural port of:

- ``Kernel/System/Ticket/Article.pm::ArticleSearchIndexRebuildFlagSet`` —
  setting ``article.search_index_needs_rebuild = 1`` makes the Znuny daemon
  rebuild ``article_search_index`` for that article.
- ``Kernel/System/Ticket/Article/Backend/MIMEBase.pm`` (ArticleCreate):
  ``$Param{MD5} = $MainObject->MD5sum( String => $Param{MessageID} )`` — the
  ``article_data_mime.a_message_id_md5`` column is the MD5 hex digest of the
  raw Message-ID string including angle brackets (used for follow-up
  detection via References/In-Reply-To).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def message_id_md5(message_id: str) -> str:
    """Return the MD5 hex digest of the raw Message-ID string (UTF-8 octets).

    Mirrors Znuny ``Main::MD5sum(String => ...)``, which encodes to UTF-8
    octets before digesting. Pass the full Message-ID including ``<`` / ``>``.
    """
    return hashlib.md5(message_id.encode("utf-8")).hexdigest()  # noqa: S324 — Znuny format


async def mark_search_rebuild(session: AsyncSession, article_id: int) -> None:
    """Set ``article.search_index_needs_rebuild = 1`` for *article_id*."""
    await session.execute(
        text("UPDATE article SET search_index_needs_rebuild = 1 WHERE id = :aid"),
        {"aid": article_id},
    )


__all__ = ["mark_search_rebuild", "message_id_md5"]
