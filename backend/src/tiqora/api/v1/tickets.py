"""Ticket, article, attachment, and history read endpoints."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.schemas import (
    ArticleBody,
    ArticleListItem,
    AttachmentMetaOut,
    HistoryEntry,
    PaginatedTickets,
    TicketDetail,
)
from tiqora.domain.ticket_service import (
    TicketAccessDenied,
    TicketNotFound,
    TicketService,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, TicketNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if isinstance(exc, TicketAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return HTTPException(status_code=500, detail="Internal error")


@router.get("", response_model=PaginatedTickets)
async def list_tickets(
    user: CurrentUser,
    session: DbSession,
    queue_id: int | None = None,
    state_id: int | None = None,
    state_type: str | None = None,
    owner_id: int | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort: str = Query("age"),
    order: str = Query("desc"),
) -> PaginatedTickets:
    svc = TicketService(session)
    return await svc.list_tickets(
        user.id,
        queue_id=queue_id,
        state_id=state_id,
        state_type=state_type,
        owner_id=owner_id,
        offset=offset,
        limit=limit,
        sort=sort,
        order=order,
    )


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> TicketDetail:
    try:
        return await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get("/{ticket_id}/articles", response_model=list[ArticleListItem])
async def list_articles(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[ArticleListItem]:
    try:
        return await TicketService(session).list_articles(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get("/{ticket_id}/articles/{article_id}/body", response_model=ArticleBody)
async def get_article_body(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
) -> ArticleBody:
    try:
        rendered = await TicketService(session).get_article_body(user.id, ticket_id, article_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return ArticleBody(
        article_id=article_id,
        content_type=rendered.content_type,
        is_html=rendered.is_html,
        body=rendered.body,
    )


@router.get(
    "/{ticket_id}/articles/{article_id}/attachments",
    response_model=list[AttachmentMetaOut],
)
async def list_attachments(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[AttachmentMetaOut]:
    try:
        return await TicketService(session).list_attachments(user.id, ticket_id, article_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


def _attachment_response(
    content_bytes: bytes,
    content_type: str | None,
    filename: str | None,
    disposition: str | None,
    *,
    force_download: bool = False,
) -> Response:
    ct = (content_type or "application/octet-stream").split(";", 1)[0].strip()
    disp_kind = (
        "attachment"
        if force_download
        else ("inline" if (disposition or "").lower() == "inline" else "attachment")
    )
    headers: dict[str, str] = {}
    if filename:
        headers["Content-Disposition"] = (
            f"{disp_kind}; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    else:
        headers["Content-Disposition"] = disp_kind
    return Response(content=content_bytes, media_type=ct, headers=headers)


# Register by-cid before numeric attachment_id so "by-cid" is not captured as id.
@router.get("/{ticket_id}/articles/{article_id}/attachments/by-cid/{content_id:path}")
async def get_attachment_by_cid(
    ticket_id: int,
    article_id: int,
    content_id: str,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    try:
        att = await TicketService(session).get_attachment_by_cid(
            user.id, ticket_id, article_id, content_id
        )
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return _attachment_response(
        att.content,
        att.meta.content_type,
        att.meta.filename,
        att.meta.disposition or "inline",
        force_download=False,
    )


@router.get("/{ticket_id}/articles/{article_id}/attachments/{attachment_id}")
async def get_attachment(
    ticket_id: int,
    article_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
    download: bool = Query(False),
) -> Response:
    try:
        att = await TicketService(session).get_attachment(
            user.id, ticket_id, article_id, attachment_id
        )
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return _attachment_response(
        att.content,
        att.meta.content_type,
        att.meta.filename,
        att.meta.disposition,
        force_download=download,
    )


@router.get("/{ticket_id}/history", response_model=list[HistoryEntry])
async def ticket_history(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[HistoryEntry]:
    try:
        return await TicketService(session).list_history(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
