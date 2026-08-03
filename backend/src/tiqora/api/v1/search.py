"""Full-text search via Meilisearch with mandatory permission filter."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.domain.schemas import SearchResponse
from tiqora.domain.search import SearchIndexService
from tiqora.permissions.engine import PermissionEngine

# Keep in sync with ``tiqora.domain.search.SORT_OPTIONS``.
SortOrder = Literal["changed_desc", "created_desc", "created_asc"]

router = APIRouter(prefix="/search", tags=["search"])


def _day_start_ts(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=UTC).timestamp())


def _day_end_ts(value: date) -> int:
    return int(datetime.combine(value, time.max, tzinfo=UTC).timestamp())


@router.get("", response_model=SearchResponse)
async def search(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    q: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    queue_id: Annotated[list[int] | None, Query()] = None,
    state_type: Annotated[list[str] | None, Query()] = None,
    owner_id: int | None = Query(None),
    customer_id: str | None = Query(None),
    created_from: Annotated[date | None, Query(description="ISO date, e.g. 2026-07-01")] = None,
    created_to: Annotated[date | None, Query(description="ISO date, e.g. 2026-07-31")] = None,
    sort: Annotated[SortOrder, Query(description="Result ordering.")] = "changed_desc",
    include_archived: Annotated[
        bool, Query(description="Also return archived tickets (admins only; ignored otherwise).")
    ] = False,
) -> SearchResponse:
    if include_archived and not await PermissionEngine(session).is_admin(user.id):
        include_archived = False
    svc = SearchIndexService(session, settings)
    try:
        return await svc.search(
            user.id,
            q,
            limit=limit,
            offset=offset,
            queue_ids=queue_id,
            state_types=state_type,
            owner_id=owner_id,
            customer_id=customer_id,
            created_from=_day_start_ts(created_from) if created_from else None,
            created_to=_day_end_ts(created_to) if created_to else None,
            sort=sort,
            include_archived=include_archived,
        )
    finally:
        await svc.close()
