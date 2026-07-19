"""Customer portal attachment endpoints: upload (as a new reply) / download.

Upload has no standalone "attachment only" concept in Znuny's data model —
every attachment belongs to an article. A portal upload therefore creates a
new customer article (same reopen/reject semantics as a text reply) carrying
the uploaded file(s).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from tiqora.api.portal.deps import CurrentCustomer, PortalService
from tiqora.domain.portal_ticket_service import (
    PortalFollowUpRejected,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
)
from tiqora.domain.schemas import PortalAttachmentUploadResponse

router = APIRouter(prefix="/tickets", tags=["portal-attachments"])


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
    ct = (content.meta.content_type or "application/octet-stream").split(";", 1)[0].strip()
    filename = content.meta.filename
    headers: dict[str, str] = {}
    if filename:
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    else:
        headers["Content-Disposition"] = "attachment"
    return Response(content=content.content, media_type=ct, headers=headers)
