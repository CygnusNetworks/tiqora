"""Agent-side knowledge base REST API: categories, articles, publish, search."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.kb.schemas import (
    ArticleIn,
    ArticleOut,
    ArticleSummary,
    ArticleUpdateIn,
    ArticleVersionOut,
    CategoryIn,
    CategoryOut,
    CategoryUpdateIn,
    KbSearchResponse,
)
from tiqora.kb.service import KbNotFound, KbService

router = APIRouter(prefix="/kb", tags=["kb"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, KbNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


async def _article_out(svc: KbService, article_id: int) -> ArticleOut:
    row = await svc.get_article(article_id)
    tags = await svc.get_tags(article_id)
    return ArticleOut.model_validate(row, from_attributes=True).model_copy(update={"tags": tags})


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[CategoryOut]:
    svc = KbService(session, settings)
    rows = await svc.list_categories()
    return [CategoryOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CategoryOut:
    svc = KbService(session, settings)
    async with session.begin():
        row = await svc.create_category(user.id, body)
    return CategoryOut.model_validate(row, from_attributes=True)


@router.get("/categories/{category_id}", response_model=CategoryOut)
async def get_category(
    category_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CategoryOut:
    svc = KbService(session, settings)
    try:
        row = await svc.get_category(category_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc
    return CategoryOut.model_validate(row, from_attributes=True)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int,
    body: CategoryUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> CategoryOut:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            row = await svc.update_category(user.id, category_id, body)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc
    return CategoryOut.model_validate(row, from_attributes=True)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> None:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.delete_category(user.id, category_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


@router.get("/articles", response_model=list[ArticleSummary])
async def list_articles(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    category_id: int | None = Query(default=None),
    state: str | None = Query(default=None),
) -> list[ArticleSummary]:
    svc = KbService(session, settings)
    rows = await svc.list_articles(category_id=category_id, state=state)
    return [ArticleSummary.model_validate(r, from_attributes=True) for r in rows]


@router.post("/articles", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    body: ArticleIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ArticleOut:
    svc = KbService(session, settings)
    async with session.begin():
        row = await svc.create_article(user.id, body)
        article_id = row.id
    return await _article_out(svc, article_id)


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ArticleOut:
    svc = KbService(session, settings)
    try:
        return await _article_out(svc, article_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc


@router.patch("/articles/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: int,
    body: ArticleUpdateIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ArticleOut:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.update_article(user.id, article_id, body)
        return await _article_out(svc, article_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> None:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.delete_article(user.id, article_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc


@router.post("/articles/{article_id}/publish", response_model=ArticleOut)
async def publish_article(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ArticleOut:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.publish(user.id, article_id)
        return await _article_out(svc, article_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc
    finally:
        await svc.close()


@router.get("/articles/{article_id}/versions", response_model=list[ArticleVersionOut])
async def list_versions(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[ArticleVersionOut]:
    svc = KbService(session, settings)
    try:
        await svc.get_article(article_id)  # 404 if missing
    except KbNotFound as exc:
        raise _map_exc(exc) from exc
    rows = await svc.list_versions(article_id)
    return [ArticleVersionOut.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=KbSearchResponse)
async def search(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    q: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> KbSearchResponse:
    svc = KbService(session, settings)
    try:
        return await svc.search_agent(user.id, q, limit=limit, offset=offset)
    finally:
        await svc.close()
