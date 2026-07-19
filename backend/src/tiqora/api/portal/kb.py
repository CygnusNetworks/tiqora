"""Customer portal knowledge base endpoints: search + read published articles.

Published + customer-visible content only. Requires an authenticated portal
customer session, same as every other portal endpoint (``CurrentCustomer``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from tiqora.api.portal.deps import AppSettings, CurrentCustomer, DbSession
from tiqora.kb.models import STATE_PUBLISHED, TiqoraKbArticle
from tiqora.kb.schemas import KbSearchResponse
from tiqora.kb.service import KbNotFound, KbService

router = APIRouter(prefix="/kb", tags=["portal-kb"])


class PortalArticleOut(BaseModel):
    id: int
    title: str
    slug: str
    language: str
    content_md: str
    tags: list[str] = []


@router.get("/search", response_model=KbSearchResponse)
async def search(
    customer: CurrentCustomer,
    session: DbSession,
    settings: AppSettings,
    q: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> KbSearchResponse:
    _ = customer
    svc = KbService(session, settings)
    try:
        return await svc.search_customer(q, limit=limit, offset=offset)
    finally:
        await svc.close()


@router.get("/articles/{slug_or_id}", response_model=PortalArticleOut)
async def get_article(
    slug_or_id: str,
    customer: CurrentCustomer,
    session: DbSession,
    settings: AppSettings,
) -> PortalArticleOut:
    _ = customer
    svc = KbService(session, settings)
    row: TiqoraKbArticle | None

    if slug_or_id.isdigit():
        try:
            row = await svc.get_article(int(slug_or_id))
        except KbNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    else:
        rows = await svc.list_articles()
        row = next((r for r in rows if r.slug == slug_or_id), None)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if row.state != STATE_PUBLISHED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    category = await svc.get_category(row.category_id)
    if not category.customer_visible:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    tags = await svc.get_tags(row.id)
    return PortalArticleOut(
        id=row.id,
        title=row.title,
        slug=row.slug,
        language=row.language,
        content_md=row.content_md,
        tags=tags,
    )
