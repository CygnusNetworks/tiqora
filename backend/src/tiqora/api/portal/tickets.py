"""Customer portal ticket endpoints: list/get/create/reply."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.api.portal.deps import CurrentCustomer, PortalService
from tiqora.domain.portal_ticket_service import (
    PortalFollowUpRejected,
    PortalInvalidInput,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
)
from tiqora.domain.schemas import (
    ArticleBody,
    ArticleListItem,
    PaginatedTickets,
    PortalReplyRequest,
    PortalReplyResponse,
    PortalTicketCreateRequest,
    PortalTicketCreateResponse,
    TicketDetail,
)

router = APIRouter(prefix="/tickets", tags=["portal-tickets"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, PortalTicketNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if isinstance(exc, PortalTicketAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(exc, PortalFollowUpRejected):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This ticket no longer accepts follow-ups "
                "(queue rejects follow-ups on closed tickets)"
            ),
        )
    if isinstance(exc, PortalInvalidInput):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


@router.get("", response_model=PaginatedTickets)
async def list_tickets(
    customer: CurrentCustomer,
    svc: PortalService,
    state: int | None = Query(default=None, alias="state"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> PaginatedTickets:
    return await svc.list_tickets(customer, state_id=state, offset=offset, limit=limit)


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(
    ticket_id: int,
    customer: CurrentCustomer,
    svc: PortalService,
) -> TicketDetail:
    try:
        return await svc.get_ticket(customer, ticket_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get("/{ticket_id}/articles", response_model=list[ArticleListItem])
async def list_articles(
    ticket_id: int,
    customer: CurrentCustomer,
    svc: PortalService,
) -> list[ArticleListItem]:
    """Only articles with ``is_visible_for_customer == 1`` — internal notes never returned."""
    try:
        return await svc.list_visible_articles(customer, ticket_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get("/{ticket_id}/articles/{article_id}/body", response_model=ArticleBody)
async def get_article_body(
    ticket_id: int,
    article_id: int,
    customer: CurrentCustomer,
    svc: PortalService,
) -> ArticleBody:
    """Sanitised article body — 404 unless owned by the customer and customer-visible."""
    try:
        rendered = await svc.get_article_body(customer, ticket_id, article_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return ArticleBody(
        article_id=article_id,
        content_type=rendered.content_type,
        is_html=rendered.is_html,
        body=rendered.body,
    )


@router.post("", response_model=PortalTicketCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: PortalTicketCreateRequest,
    customer: CurrentCustomer,
    svc: PortalService,
) -> PortalTicketCreateResponse:
    try:
        ticket_id = await svc.create_ticket(
            customer, title=body.title, body=body.body, queue_id=body.queue_id
        )
    except PortalInvalidInput as exc:
        raise _map_exc(exc) from exc
    return PortalTicketCreateResponse(ticket_id=ticket_id)


@router.post("/{ticket_id}/reply", response_model=PortalReplyResponse)
async def reply(
    ticket_id: int,
    body: PortalReplyRequest,
    customer: CurrentCustomer,
    svc: PortalService,
) -> PortalReplyResponse:
    try:
        article_id, reopened = await svc.reply(
            customer, ticket_id, body=body.body, subject=body.subject
        )
    except (PortalTicketNotFound, PortalTicketAccessDenied, PortalFollowUpRejected) as exc:
        raise _map_exc(exc) from exc
    return PortalReplyResponse(article_id=article_id, reopened=reopened)
