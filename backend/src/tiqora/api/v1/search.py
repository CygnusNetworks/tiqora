"""Full-text search via Meilisearch with mandatory permission filter."""

from __future__ import annotations

from fastapi import APIRouter, Query

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.domain.schemas import SearchResponse
from tiqora.domain.search import SearchIndexService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    q: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> SearchResponse:
    svc = SearchIndexService(session, settings)
    try:
        return await svc.search(user.id, q, limit=limit, offset=offset)
    finally:
        await svc.close()
