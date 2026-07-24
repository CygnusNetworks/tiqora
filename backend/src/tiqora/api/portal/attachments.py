"""Customer portal attachment endpoints: upload (as a new reply) / download.

Upload has no standalone "attachment only" concept in Znuny's data model —
every attachment belongs to an article. A portal upload therefore creates a
new customer article (same reopen/reject semantics as a text reply) carrying
the uploaded file(s).
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from tiqora.api.attachment_response import safe_attachment_response
from tiqora.api.portal.deps import CurrentCustomer, PortalService
from tiqora.domain.portal_ticket_service import (
    PortalFollowUpRejected,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
)
from tiqora.domain.schemas import PortalAttachmentUploadResponse

router = APIRouter(prefix="/tickets", tags=["portal-attachments"])

# Cap a single portal upload so a customer can't exhaust memory with one request.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, PortalTicketNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if isinstance(exc, PortalTicketAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(exc, PortalFollowUpRejected):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ticket no longer accepts follow-ups",
        )
    return HTTPException(status_code=500, detail="Internal error")


@router.post(
    "/{ticket_id}/attachments",
    response_model=PortalAttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    ticket_id: int,
    customer: CurrentCustomer,
    svc: PortalService,
    file: UploadFile = File(...),  # noqa: B008
    note: str = Form(default=""),
) -> PortalAttachmentUploadResponse:
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Attachment exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
        )
    body = note or f"(attachment: {file.filename or 'file'})"
    try:
        article_id, _reopened = await svc.reply(
            customer,
            ticket_id,
            body=body,
            attachments=[
                (
                    file.filename or "attachment",
                    file.content_type or "application/octet-stream",
                    content,
                )
            ],
        )
    except (PortalTicketNotFound, PortalTicketAccessDenied, PortalFollowUpRejected) as exc:
        raise _map_exc(exc) from exc
    atts = await svc.list_attachments(customer, ticket_id, article_id)
    return PortalAttachmentUploadResponse(
        article_id=article_id, attachment_ids=[a.id for a in atts]
    )


@router.get("/{ticket_id}/articles/{article_id}/attachments/by-cid/{content_id:path}")
async def download_attachment_by_cid(
    ticket_id: int,
    article_id: int,
    content_id: str,
    customer: CurrentCustomer,
    svc: PortalService,
) -> Response:
    """Resolve a rendered article body's ``cid:`` image reference.

    Same ownership + ``is_visible_for_customer`` scoping as the numeric
    attachment endpoint below; this is what ``rewrite_cid_urls`` targets for
    portal-rendered article bodies (see ``domain.article_html``).
    """
    try:
        content = await svc.get_attachment_by_cid(customer, ticket_id, article_id, content_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    # Inline is honored only for raster images; active types are neutralized and
    # the response is sandboxed (see safe_attachment_response).
    return safe_attachment_response(
        content.content, content.meta.content_type, content.meta.filename, "inline"
    )


@router.get("/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(
    ticket_id: int,
    attachment_id: int,
    customer: CurrentCustomer,
    svc: PortalService,
) -> Response:
    try:
        content = await svc.get_attachment(customer, ticket_id, attachment_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return safe_attachment_response(
        content.content,
        content.meta.content_type,
        content.meta.filename,
        "attachment",
        force_download=True,
    )
