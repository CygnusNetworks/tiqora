"""Agent-side knowledge base REST API: categories, articles, attachments,
publish, search, and an ACL-filtered knowledge bundle for agent/LLM use.

Every read that returns article content is ACL-scoped to the caller's
permission groups (a category with no groups is readable by every agent).
Writes go through ``CurrentUser`` and therefore work with a session cookie
*or* a Bearer API key, so an automation/agent can upload and update content.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.kb.schemas import (
    ArticleIn,
    ArticleOut,
    ArticleSummary,
    ArticleUpdateIn,
    ArticleVersionOut,
    AssignableGroup,
    AttachmentOut,
    CategoryIn,
    CategoryOut,
    CategoryUpdateIn,
    KbSearchResponse,
    KnowledgeArticle,
    KnowledgeBundle,
)
from tiqora.kb.service import KbForbidden, KbNotFound, KbService

router = APIRouter(prefix="/kb", tags=["kb"])

#: Max KB attachment size (bytes). Stored inline in the DB, so kept modest.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, KbNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, KbForbidden):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


async def _category_out(svc: KbService, category_id: int) -> CategoryOut:
    row = await svc.get_category(category_id)
    gids = await svc.category_group_ids(category_id)
    return CategoryOut.model_validate(row, from_attributes=True).model_copy(
        update={"permission_group_ids": gids}
    )


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
    return [
        CategoryOut.model_validate(r, from_attributes=True).model_copy(
            update={"permission_group_ids": await svc.category_group_ids(r.id)}
        )
        for r in rows
    ]


@router.get("/assignable-groups", response_model=list[AssignableGroup])
async def list_assignable_groups(
    user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[AssignableGroup]:
    """Permission groups the current user may assign to a KB category."""
    svc = KbService(session, settings)
    pairs = await svc.assignable_groups(user.id)
    return [AssignableGroup(id=gid, name=name) for gid, name in pairs]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CategoryOut:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            row = await svc.create_category(user.id, body)
            category_id = row.id
    except KbForbidden as exc:
        raise _map_exc(exc) from exc
    return await _category_out(svc, category_id)


@router.get("/categories/{category_id}", response_model=CategoryOut)
async def get_category(
    category_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> CategoryOut:
    svc = KbService(session, settings)
    try:
        return await _category_out(svc, category_id)
    except KbNotFound as exc:
        raise _map_exc(exc) from exc


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
            await svc.update_category(user.id, category_id, body)
        return await _category_out(svc, category_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc


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
    tag: str | None = Query(default=None),
) -> list[ArticleSummary]:
    svc = KbService(session, settings)
    rows = await svc.list_articles(category_id=category_id, state=state, tag=tag, user_id=user.id)
    return [ArticleSummary.model_validate(r, from_attributes=True) for r in rows]


@router.get("/knowledge", response_model=KnowledgeBundle)
async def get_knowledge(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    tags: str | None = Query(default=None, description="Comma-separated tag names"),
    category_id: int | None = Query(default=None),
    state: str | None = Query(default="published"),
    include_content: bool = Query(default=True),
) -> KnowledgeBundle:
    """ACL-filtered article bundle selected by tag(s) and/or category.

    Intended as the context surface for an agent/LLM: pass a set of tags
    and/or a category, get back the readable articles (Markdown included by
    default). Content is scoped to the caller's permission groups.
    """
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    svc = KbService(session, settings)
    pairs = await svc.get_knowledge(user.id, tags=tag_list, category_id=category_id, state=state)
    articles = [
        KnowledgeArticle(
            id=row.id,
            category_id=row.category_id,
            title=row.title,
            slug=row.slug,
            language=row.language,
            state=row.state,
            tags=tag_names,
            content_md=row.content_md if include_content else None,
        )
        for row, tag_names in pairs
    ]
    return KnowledgeBundle(
        tags=tag_list, category_id=category_id, total=len(articles), articles=articles
    )


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
        await svc.get_article_scoped(user.id, article_id)  # 404 / 403
        return await _article_out(svc, article_id)
    except (KbNotFound, KbForbidden) as exc:
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
            await svc.get_article_scoped(user.id, article_id)
            await svc.update_article(user.id, article_id, body)
        return await _article_out(svc, article_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> None:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.get_article_scoped(user.id, article_id)
            await svc.delete_article(user.id, article_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc


@router.post("/articles/{article_id}/publish", response_model=ArticleOut)
async def publish_article(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ArticleOut:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.get_article_scoped(user.id, article_id)
            await svc.publish(user.id, article_id)
        return await _article_out(svc, article_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc
    finally:
        await svc.close()


@router.get("/articles/{article_id}/versions", response_model=list[ArticleVersionOut])
async def list_versions(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[ArticleVersionOut]:
    svc = KbService(session, settings)
    try:
        await svc.get_article_scoped(user.id, article_id)  # 404 / 403
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc
    rows = await svc.list_versions(article_id)
    return [ArticleVersionOut.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@router.post(
    "/articles/{article_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    file: UploadFile = File(...),  # noqa: B008
) -> AttachmentOut:
    content = await file.read()
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"attachment exceeds {MAX_ATTACHMENT_BYTES} bytes",
        )
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.get_article_scoped(user.id, article_id)
            row = await svc.add_attachment(
                article_id,
                file.filename or "attachment",
                file.content_type,
                content,
            )
            out = AttachmentOut(
                id=row.id,
                article_id=article_id,
                filename=row.filename,
                content_type=row.content_type,
                size=len(content),
            )
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc
    return out


@router.get("/articles/{article_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    article_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[AttachmentOut]:
    svc = KbService(session, settings)
    try:
        await svc.get_article_scoped(user.id, article_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc
    rows = await svc.list_attachments(article_id)
    return [
        AttachmentOut(
            id=r.id,
            article_id=article_id,
            filename=r.filename,
            content_type=r.content_type,
            size=len(r.content),
        )
        for r in rows
    ]


@router.get("/articles/{article_id}/attachments/{attachment_id}")
async def download_attachment(
    article_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    svc = KbService(session, settings)
    try:
        await svc.get_article_scoped(user.id, article_id)
        row = await svc.get_attachment(article_id, attachment_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc
    ct = (row.content_type or "application/octet-stream").split(";", 1)[0].strip()
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{row.filename}\"; filename*=UTF-8''{quote(row.filename)}"
        )
    }
    return Response(content=row.content, media_type=ct, headers=headers)


@router.delete(
    "/articles/{article_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    article_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    svc = KbService(session, settings)
    try:
        async with session.begin():
            await svc.get_article_scoped(user.id, article_id)
            await svc.delete_attachment(article_id, attachment_id)
    except (KbNotFound, KbForbidden) as exc:
        raise _map_exc(exc) from exc


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
