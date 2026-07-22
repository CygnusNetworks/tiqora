"""Ticket, article, attachment, and history read + write endpoints."""

from __future__ import annotations

import csv
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.channels.email.outbound_reply import OutboundMailError
from tiqora.db.engine import get_session_factory
from tiqora.domain.schemas import (
    ArticleBody,
    ArticleListItem,
    AttachmentMetaOut,
    BounceRequest,
    ForwardRequest,
    HistoryEntry,
    PaginatedTickets,
    ReplyDraftOut,
    SplitRequest,
    TemplateOut,
    TicketDetail,
    TicketLinkCreateRequest,
    TicketLinkTargetOut,
    TicketListItem,
)
from tiqora.domain.ticket_service import (
    TicketAccessDenied,
    TicketNotFound,
    TicketService,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketIn,
    TicketWriteService,
)
from tiqora.domain.ticket_write_service import (
    TicketAccessDenied as WriteAccessDenied,
)
from tiqora.domain.ticket_write_service import (
    TicketNotFound as WriteNotFound,
)
from tiqora.znuny.sysconfig import SysConfig

# ---------------------------------------------------------------------------
# Write schemas
# ---------------------------------------------------------------------------


class TicketCreateRequest(BaseModel):
    title: str
    queue_id: int
    state_id: int
    priority_id: int
    owner_id: int
    lock_id: int = 1
    type_id: int | None = None
    service_id: int | None = None
    sla_id: int | None = None
    responsible_id: int | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    archive_flag: int = 0
    dynamic_fields: dict[str, list[str]] = Field(default_factory=dict)


class TicketCreateResponse(BaseModel):
    ticket_id: int


class ArticleCreateRequest(BaseModel):
    sender_type: str = "agent"
    is_visible_for_customer: bool = True
    subject: str
    body: str
    content_type: str = "text/plain; charset=utf-8"
    from_address: str | None = None
    to_address: str | None = None
    cc: str | None = None
    bcc: str | None = None
    reply_to: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    channel: str = "note"
    # Optional accept-hook (Tiqora AI, plan §3.1/§3.4): when set and the draft
    # is still `open` and belongs to this ticket, it is marked `accepted` with
    # `accepted_article_id` = the article just created. Never set at prefill
    # time — only once the article actually exists. Absent/unknown values are
    # a no-op so this stays fully backward compatible.
    ai_draft_id: int | None = None


class ArticleCreateResponse(BaseModel):
    article_id: int


class MutationRequest(BaseModel):
    """Generic mutation payload (flexible)."""

    queue_id: int | None = None
    state_id: int | None = None
    priority_id: int | None = None
    title: str | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    owner_id: int | None = None
    responsible_id: int | None = None
    lock: str | None = None  # "lock" | "unlock"
    archive: bool | None = None
    pending_time: datetime | None = None
    field_name: str | None = None
    field_values: list[str] | None = None
    watcher_user_id: int | None = None
    unwatch_user_id: int | None = None


class MergeRequest(BaseModel):
    main_ticket_id: int


class DraftIn(BaseModel):
    action: str
    title: str | None = None
    content: str = "{}"


class DraftOut(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    action: str
    title: str | None = None
    content: str
    created: datetime
    changed: datetime


router = APIRouter(prefix="/tickets", tags=["tickets"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, (TicketNotFound, WriteNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if isinstance(exc, (TicketAccessDenied, WriteAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(exc, InvalidInput):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, OutboundMailError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Outbound email delivery failed: {exc}",
        )
    return HTTPException(status_code=500, detail="Internal error")


def _write_service(session: Any, settings: Any) -> TicketWriteService:
    _ = settings
    factory = get_session_factory()
    sysconfig = SysConfig(session)
    # mail_sender=None: deliver_agent_email_reply resolves DB outbound settings
    # first, then env TIQORA_SMTP_*. Tests inject CapturingMailSender via
    # TicketWriteService(..., mail_sender=...).
    return TicketWriteService(session, factory, sysconfig, mail_sender=None)


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


class MyTicketCounts(BaseModel):
    """Owned-ticket counts for the "My tickets" sidebar badges."""

    open: int
    new: int


@router.get("/my-counts", response_model=MyTicketCounts)
async def my_ticket_counts(user: CurrentUser, session: DbSession) -> MyTicketCounts:
    """Open/new counts for tickets owned by the current agent.

    Registered before ``/{ticket_id}`` so "my-counts" is not parsed as a
    ticket id.
    """
    counts = await TicketService(session).count_owned(user.id)
    return MyTicketCounts(open=counts["open"], new=counts["new"])


class DashboardSummary(BaseModel):
    """KPI-tile counts for the agent dashboard."""

    my_open: int
    my_new: int
    unowned_new: int
    escalated: int


@router.get("/dashboard-summary", response_model=DashboardSummary)
async def dashboard_summary(user: CurrentUser, session: DbSession) -> DashboardSummary:
    """Counts for the dashboard KPI tiles: owned open/new, unclaimed new, escalated.

    Registered before ``/{ticket_id}`` so "dashboard-summary" is not parsed as
    a ticket id.
    """
    counts = await TicketService(session).count_dashboard_summary(user.id)
    return DashboardSummary(**counts)


class TicketSearchHitOut(BaseModel):
    """Compact ticket hit for agent link/merge pickers."""

    ticket_id: int
    tn: str
    title: str
    queue: str | None = None
    state: str | None = None
    state_type: str | None = None


@router.get("/search", response_model=list[TicketSearchHitOut])
async def search_tickets(
    user: CurrentUser,
    session: DbSession,
    q: str = Query("", description="Substring matched against ticket number or title"),
    limit: int = Query(20, ge=1, le=50),
) -> list[TicketSearchHitOut]:
    """Search tickets the current agent may access (``ro`` on the queue).

    Matches case-insensitively against ``tn`` and ``title``. Merged/removed
    tickets are excluded. Registered before ``/{ticket_id}`` so "search" is
    not parsed as a ticket id. Powers "Ticket verknüpfen" / "Ticket
    zusammenfassen" pickers.
    """
    hits = await TicketService(session).search_tickets(user.id, q=q, limit=limit)
    return [TicketSearchHitOut(**h) for h in hits]


class _EchoWriter:
    """File-like shim so ``csv.writer`` yields each row as a string.

    ``csv.writer(target).writerow(...)`` calls ``target.write(row_string)``
    and returns whatever ``write`` returns — echoing the string back turns
    the writer into a per-row string generator instead of one requiring a
    real (buffering) file object, which is what lets the CSV export stream
    row-by-row instead of materializing the whole file in memory.
    """

    def write(self, value: str) -> str:
        return value


_CSV_HEADER = [
    "Number",
    "Title",
    "Queue",
    "State",
    "Priority",
    "Owner",
    "Customer",
    "Created",
    "Changed",
]


def _ticket_csv_row(item: TicketListItem) -> list[str]:
    return [
        item.tn,
        item.title or "",
        item.queue_name or "",
        item.state or "",
        item.priority or "",
        item.owner_login or item.owner_name or "",
        item.customer_id or "",
        item.create_time.isoformat(),
        item.change_time.isoformat(),
    ]


async def _export_tickets_csv_stream(
    svc: TicketService,
    user_id: int,
    *,
    queue_id: int | None,
    state_id: int | None,
    state_type: str | None,
    owner_id: int | None,
    sort: str,
    order: str,
) -> AsyncGenerator[bytes, None]:
    writer = csv.writer(_EchoWriter(), delimiter=";")
    # UTF-8 BOM first, so Excel opens the file as UTF-8 instead of guessing
    # the system codepage.
    yield b"\xef\xbb\xbf"
    yield writer.writerow(_CSV_HEADER).encode("utf-8")
    async for item in svc.iter_tickets_for_export(
        user_id,
        queue_id=queue_id,
        state_id=state_id,
        state_type=state_type,
        owner_id=owner_id,
        sort=sort,
        order=order,
    ):
        yield writer.writerow(_ticket_csv_row(item)).encode("utf-8")


@router.get("/export.csv")
async def export_tickets_csv(
    user: CurrentUser,
    session: DbSession,
    queue_id: int | None = None,
    state_id: int | None = None,
    state_type: str | None = None,
    owner_id: int | None = None,
    sort: str = Query("age"),
    order: str = Query("desc"),
) -> StreamingResponse:
    """Stream every ticket matching the same filters as ``GET /tickets`` as CSV.

    Unlike the paginated list endpoint, this has no 200-row cap — rows are
    streamed server-side (``TicketService.iter_tickets_for_export``) so
    exporting a large queue stays memory-safe. Route registered *before*
    ``/{ticket_id}`` so FastAPI does not try to parse "export.csv" as a
    ticket id.
    """
    svc = TicketService(session)
    return StreamingResponse(
        _export_tickets_csv_stream(
            svc,
            user.id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
            sort=sort,
            order=order,
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tickets.csv"'},
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
    order: str = Query("desc"),
) -> list[HistoryEntry]:
    try:
        return await TicketService(session).list_history(user.id, ticket_id, order=order)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get(
    "/{ticket_id}/articles/{article_id}/reply-draft",
    response_model=ReplyDraftOut,
)
async def article_reply_draft(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    reply_all: bool = Query(False),
) -> ReplyDraftOut:
    """Prefilled reply draft (Re: subject, To/Cc, quoted body) for one article."""
    try:
        return await TicketService(session).get_reply_draft(
            user.id, ticket_id, article_id, reply_all=reply_all
        )
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


@router.get("/{ticket_id}/templates", response_model=list[TemplateOut])
async def ticket_templates(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[TemplateOut]:
    """Response templates (template_type='Answer') for the ticket's queue."""
    try:
        return await TicketService(session).list_templates(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=TicketCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: TicketCreateRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> TicketCreateResponse:
    """Create a new ticket. Requires ``create`` permission on the queue's group."""
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            tid = await svc.create_ticket(
                user.id,
                TicketIn(
                    title=body.title,
                    queue_id=body.queue_id,
                    state_id=body.state_id,
                    priority_id=body.priority_id,
                    owner_id=body.owner_id,
                    lock_id=body.lock_id,
                    type_id=body.type_id,
                    service_id=body.service_id,
                    sla_id=body.sla_id,
                    responsible_id=body.responsible_id,
                    customer_id=body.customer_id,
                    customer_user_id=body.customer_user_id,
                    archive_flag=body.archive_flag,
                    dynamic_fields=body.dynamic_fields,
                ),
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return TicketCreateResponse(ticket_id=tid)


@router.post(
    "/{ticket_id}/articles",
    response_model=ArticleCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_article(
    ticket_id: int,
    body: ArticleCreateRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ArticleCreateResponse:
    """Add an article to a ticket. Requires ``rw`` permission.

    Agent email replies (``channel=email``, ``sender_type=agent``) are SMTP-
    delivered then stored (send-then-store). Delivery failure returns HTTP 502
    and does not leave a silent no-op 201.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            aid = await svc.add_article(
                user.id,
                ticket_id,
                ArticleIn(
                    sender_type=body.sender_type,
                    is_visible_for_customer=body.is_visible_for_customer,
                    subject=body.subject,
                    body=body.body,
                    content_type=body.content_type,
                    from_address=body.from_address,
                    to_address=body.to_address,
                    cc=body.cc,
                    bcc=body.bcc,
                    reply_to=body.reply_to,
                    message_id=body.message_id,
                    in_reply_to=body.in_reply_to,
                    references=body.references,
                    channel=body.channel,
                ),
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput, OutboundMailError) as exc:
        raise _map_exc(exc) from exc

    if body.ai_draft_id is not None:
        # Outside the write transaction above (already committed): the AI
        # service modules commit internally (see tiqora.ai.drafts), so this
        # runs as its own follow-up transaction rather than nesting commits.
        from tiqora.ai import drafts as ai_drafts

        draft = await ai_drafts.get_draft(session, body.ai_draft_id)
        if draft is not None and draft.ticket_id == ticket_id:
            await ai_drafts.mark_accepted(
                session, body.ai_draft_id, article_id=aid, actor_user_id=user.id
            )
    return ArticleCreateResponse(article_id=aid)


@router.patch("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_ticket(
    ticket_id: int,
    body: MutationRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Apply one or more field mutations to a ticket.

    Every mutating field is routed through :class:`TicketWriteService` methods
    so per-action Znuny permission keys are enforced (``priority``, ``owner``,
    ``move_into``, ``rw``, …). Personal watch/unwatch stays ungated.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            if body.queue_id is not None:
                await svc.move_queue(user.id, ticket_id, body.queue_id)
            if body.state_id is not None:
                await svc.change_state(
                    user.id, ticket_id, body.state_id, pending_time=body.pending_time
                )
            if body.priority_id is not None:
                await svc.change_priority(user.id, ticket_id, body.priority_id)
            if body.title is not None:
                await svc.change_title(user.id, ticket_id, body.title)
            if body.customer_id is not None or body.customer_user_id is not None:
                await svc.set_customer(
                    user.id,
                    ticket_id,
                    customer_id=body.customer_id,
                    customer_user_id=body.customer_user_id,
                )
            if body.owner_id is not None:
                await svc.assign_owner(user.id, ticket_id, body.owner_id)
            if body.responsible_id is not None:
                await svc.assign_responsible(user.id, ticket_id, body.responsible_id)
            if body.lock is not None:
                if body.lock == "lock":
                    await svc.lock_ticket(user.id, ticket_id)
                elif body.lock == "unlock":
                    await svc.unlock_ticket(user.id, ticket_id)
            if body.archive is not None:
                await svc.archive_ticket(user.id, ticket_id, body.archive)
            if body.field_name is not None and body.field_values is not None:
                await svc.update_dynamic_field(
                    user.id,
                    ticket_id,
                    field_name=body.field_name,
                    values=body.field_values,
                )
            # Watch/unwatch are personal preferences — no permission gate.
            if body.watcher_user_id is not None:
                from tiqora.domain.ticket_write_service import watch_ticket

                await watch_ticket(
                    session,
                    ticket_id=ticket_id,
                    watcher_user_id=body.watcher_user_id,
                    user_id=user.id,
                )
            if body.unwatch_user_id is not None:
                from tiqora.domain.ticket_write_service import unwatch_ticket

                await unwatch_ticket(
                    session,
                    ticket_id=ticket_id,
                    watcher_user_id=body.unwatch_user_id,
                    user_id=user.id,
                )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc


@router.post("/{ticket_id}/merge", status_code=status.HTTP_204_NO_CONTENT)
async def merge_ticket(
    ticket_id: int,
    body: MergeRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Merge ticket_id into main_ticket_id. Requires ``rw`` on both queues."""
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            await svc.merge_tickets(user.id, body.main_ticket_id, ticket_id)
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc


@router.post(
    "/{ticket_id}/articles/{article_id}/forward",
    response_model=ArticleCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def forward_article_endpoint(
    ticket_id: int,
    article_id: int,
    body: ForwardRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ArticleCreateResponse:
    """Forward an article by email (history type 'Forward'). Requires ``rw``."""
    svc = _write_service(session, settings)
    fwd_body = f"{body.note}\n\n{body.body}" if body.note else body.body
    subject = body.subject or "Fwd:"
    try:
        async with session.begin():
            aid = await svc.forward_article(
                user.id,
                ticket_id,
                subject=subject,
                body=fwd_body,
                to_address=body.to_address,
                cc=body.cc,
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return ArticleCreateResponse(article_id=aid)


@router.post(
    "/{ticket_id}/articles/{article_id}/bounce",
    response_model=ArticleCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bounce_article_endpoint(
    ticket_id: int,
    article_id: int,
    body: BounceRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ArticleCreateResponse:
    """Bounce (resend) an article verbatim to a new recipient. Requires ``rw``."""
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            aid = await svc.bounce_article(
                user.id,
                ticket_id,
                article_id,
                to_address=body.to_address,
                state_id=body.state_id,
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return ArticleCreateResponse(article_id=aid)


@router.post(
    "/{ticket_id}/articles/{article_id}/split",
    response_model=TicketCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def split_article_endpoint(
    ticket_id: int,
    article_id: int,
    body: SplitRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> TicketCreateResponse:
    """Split an article into a new linked ticket. Requires ``rw`` + ``create``."""
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            new_id = await svc.split_article(
                user.id, ticket_id, article_id, queue_id=body.queue_id, title=body.title
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return TicketCreateResponse(ticket_id=new_id)


@router.get("/{ticket_id}/links", response_model=list[TicketLinkTargetOut])
async def list_ticket_links(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[TicketLinkTargetOut]:
    """List tickets linked to this one."""
    svc = _write_service(session, settings)
    try:
        rows = await svc.list_links(user.id, ticket_id)
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return [TicketLinkTargetOut(**r) for r in rows]


@router.post(
    "/{ticket_id}/links",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
)
async def create_ticket_link(
    ticket_id: int,
    body: TicketLinkCreateRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    """Link this ticket to another. Requires ``rw`` on both."""
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            await svc.link_tickets(
                user.id, ticket_id, body.target_ticket_id, link_type=body.link_type
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return Response(status_code=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Draft endpoints (tiqora_form_draft)
# ---------------------------------------------------------------------------


@router.get("/{ticket_id}/drafts", response_model=list[DraftOut])
async def list_drafts(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[DraftOut]:
    """List form drafts for a ticket (current user only)."""
    from sqlalchemy import text

    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, ticket_id, user_id, action, title, content, created, changed"
                    " FROM tiqora_form_draft"
                    " WHERE ticket_id = :tid AND user_id = :uid ORDER BY changed DESC"
                ),
                {"tid": ticket_id, "uid": user.id},
            )
        )
        .mappings()
        .fetchall()
    )
    return [DraftOut(**dict(r)) for r in rows]


@router.put(
    "/{ticket_id}/drafts/{action}",
    response_model=DraftOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_draft(
    ticket_id: int,
    action: str,
    body: DraftIn,
    user: CurrentUser,
    session: DbSession,
) -> DraftOut:
    """Create or update a draft for (ticket, user, action)."""
    from sqlalchemy import text

    async with session.begin():
        existing = (
            await session.execute(
                text(
                    "SELECT id FROM tiqora_form_draft"
                    " WHERE ticket_id = :tid AND user_id = :uid AND action = :act LIMIT 1"
                ),
                {"tid": ticket_id, "uid": user.id, "act": action},
            )
        ).first()
        if existing is not None:
            await session.execute(
                text(
                    "UPDATE tiqora_form_draft SET title = :title, content = :content,"
                    " changed = current_timestamp"
                    " WHERE ticket_id = :tid AND user_id = :uid AND action = :act"
                ),
                {
                    "title": body.title,
                    "content": body.content,
                    "tid": ticket_id,
                    "uid": user.id,
                    "act": action,
                },
            )
        else:
            await session.execute(
                text(
                    "INSERT INTO tiqora_form_draft"
                    " (ticket_id, user_id, action, title, content, created, changed)"
                    " VALUES (:tid, :uid, :act, :title, :content,"
                    " current_timestamp, current_timestamp)"
                ),
                {
                    "tid": ticket_id,
                    "uid": user.id,
                    "act": action,
                    "title": body.title,
                    "content": body.content,
                },
            )

    row = (
        (
            await session.execute(
                text(
                    "SELECT id, ticket_id, user_id, action, title, content, created, changed"
                    " FROM tiqora_form_draft"
                    " WHERE ticket_id = :tid AND user_id = :uid AND action = :act LIMIT 1"
                ),
                {"tid": ticket_id, "uid": user.id, "act": action},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=500, detail="Draft upsert failed")
    return DraftOut(**dict(row))


@router.delete("/{ticket_id}/drafts/{action}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    ticket_id: int,
    action: str,
    user: CurrentUser,
    session: DbSession,
) -> None:
    """Delete a draft for (ticket, user, action)."""
    from sqlalchemy import text

    async with session.begin():
        await session.execute(
            text(
                "DELETE FROM tiqora_form_draft"
                " WHERE ticket_id = :tid AND user_id = :uid AND action = :act"
            ),
            {"tid": ticket_id, "uid": user.id, "act": action},
        )
