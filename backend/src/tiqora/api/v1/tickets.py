"""Ticket, article, attachment, and history read + write endpoints."""

from __future__ import annotations

import csv
from collections.abc import AsyncGenerator
from datetime import datetime
from html import escape as html_escape
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.attachment_response import safe_attachment_response
from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.channels.email.outbound_reply import OutboundMailError
from tiqora.channels.telegram.outbound import TelegramDeliveryError
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
    SimilarTicketsOut,
    SplitRequest,
    TemplateOut,
    TicketDetail,
    TicketLinkCreateRequest,
    TicketLinkTargetOut,
    TicketListItem,
)
from tiqora.domain.search import SearchIndexService
from tiqora.domain.ticket_service import (
    TicketAccessDenied,
    TicketNotFound,
    TicketService,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    ArticleNotDeletable,
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
from tiqora.permissions.engine import PermissionEngine
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
    type_id: int | None = None
    service_id: int | None = None
    # True when the client explicitly wants to clear service.
    clear_service: bool | None = None
    sla_id: int | None = None
    clear_sla: bool | None = None
    title: str | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    # True when the client explicitly wants to unassign the customer. Needed
    # because `customer_id = None` is indistinguishable from "not supplied".
    clear_customer: bool | None = None
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


class AcquireLockRequest(BaseModel):
    """Composer-open lock acquisition (Znuny RequiredLock semantics)."""

    action: Literal["compose", "forward", "bounce", "close"]
    # Take over a ticket locked by another agent (owner moves to the caller,
    # lock stays). Requires the Znuny ``owner`` permission key.
    takeover: bool = False


class AcquireLockResponse(BaseModel):
    result: Literal[
        "not_required", "acquired", "already_mine", "taken_over", "locked_by_other"
    ]
    locked_by_id: int | None = None
    locked_by_name: str | None = None


class DraftIn(BaseModel):
    action: str
    # Article the draft replies to. Omitted/null = ticket-wide draft.
    article_id: int | None = None
    title: str | None = None
    content: str = "{}"


class DraftOut(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    action: str
    article_id: int | None = None
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
    if isinstance(exc, ArticleNotDeletable):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OutboundMailError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Outbound email delivery failed: {exc}",
        )
    if isinstance(exc, TelegramDeliveryError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
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
    customer_id: str | None = None,
    responsible_id: int | None = None,
    service_id: int | None = None,
    locked: bool | None = Query(
        None, description="True = lock/tmp_lock only; False = unlock only."
    ),
    watcher_user_id: int | None = Query(None, description="Tickets watched by this agent user id."),
    escalated: bool | None = Query(
        None, description="True = any escalation_* epoch already in the past."
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort: str = Query("age"),
    order: str = Query("desc"),
    include_archived: bool = Query(
        False, description="Also list archived tickets (admins only; ignored otherwise)."
    ),
) -> PaginatedTickets:
    if include_archived and not await PermissionEngine(session).is_admin(user.id):
        include_archived = False
    svc = TicketService(session)
    return await svc.list_tickets(
        user.id,
        queue_id=queue_id,
        state_id=state_id,
        state_type=state_type,
        owner_id=owner_id,
        customer_id=customer_id,
        responsible_id=responsible_id,
        service_id=service_id,
        locked=locked,
        watcher_user_id=watcher_user_id,
        escalated=True if escalated else None,
        offset=offset,
        limit=limit,
        sort=sort,
        order=order,
        include_archived=include_archived,
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
    customer_id: str | None,
    responsible_id: int | None = None,
    service_id: int | None = None,
    locked: bool | None = None,
    watcher_user_id: int | None = None,
    escalated: bool | None = None,
    sort: str,
    order: str,
    include_archived: bool = False,
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
        customer_id=customer_id,
        responsible_id=responsible_id,
        service_id=service_id,
        locked=locked,
        watcher_user_id=watcher_user_id,
        escalated=escalated,
        sort=sort,
        order=order,
        include_archived=include_archived,
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
    customer_id: str | None = None,
    responsible_id: int | None = None,
    service_id: int | None = None,
    locked: bool | None = None,
    watcher_user_id: int | None = None,
    escalated: bool | None = None,
    sort: str = Query("age"),
    order: str = Query("desc"),
    include_archived: bool = Query(
        False, description="Also export archived tickets (admins only; ignored otherwise)."
    ),
) -> StreamingResponse:
    """Stream every ticket matching the same filters as ``GET /tickets`` as CSV.

    Unlike the paginated list endpoint, this has no 200-row cap — rows are
    streamed server-side (``TicketService.iter_tickets_for_export``) so
    exporting a large queue stays memory-safe. Route registered *before*
    ``/{ticket_id}`` so FastAPI does not try to parse "export.csv" as a
    ticket id.
    """
    if include_archived and not await PermissionEngine(session).is_admin(user.id):
        include_archived = False
    svc = TicketService(session)
    return StreamingResponse(
        _export_tickets_csv_stream(
            svc,
            user.id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
            customer_id=customer_id,
            responsible_id=responsible_id,
            service_id=service_id,
            locked=locked,
            watcher_user_id=watcher_user_id,
            escalated=True if escalated else None,
            sort=sort,
            order=order,
            include_archived=include_archived,
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="tickets.csv"'},
    )


class TimeAccountingReportEntry(BaseModel):
    """One booked time row for the cross-ticket report."""

    id: int
    ticket_id: int
    ticket_tn: str | None = None
    ticket_title: str | None = None
    article_id: int | None = None
    time_unit: float
    create_time: datetime | None = None
    create_by: int
    create_by_login: str | None = None


class TimeAccountingReportOut(BaseModel):
    items: list[TimeAccountingReportEntry]
    total_units: float
    offset: int
    limit: int


@router.get("/time-accounting", response_model=TimeAccountingReportOut)
async def time_accounting_report(
    user: CurrentUser,
    session: DbSession,
    create_by: int | None = None,
    ticket_id: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> TimeAccountingReportOut:
    """Cross-ticket time-accounting report (permission-scoped via queue ``ro``).

    Registered before ``/{ticket_id}`` so "time-accounting" is not parsed as a
    ticket id. Only rows on tickets the agent can read are returned.
    """
    from sqlalchemy import func, select

    from tiqora.db.legacy.misc import TimeAccounting
    from tiqora.db.legacy.queue import Queue
    from tiqora.db.legacy.ticket import Ticket
    from tiqora.db.legacy.user import Users
    from tiqora.permissions.engine import PermissionEngine as _PE

    allowed_groups = await _PE(session).groups_for_permission(user.id, "ro")
    if not allowed_groups:
        return TimeAccountingReportOut(items=[], total_units=0.0, offset=offset, limit=limit)

    filters = [
        Queue.group_id.in_(list(allowed_groups)),
        Queue.valid_id == 1,
    ]
    if create_by is not None:
        filters.append(TimeAccounting.create_by == create_by)
    if ticket_id is not None:
        filters.append(TimeAccounting.ticket_id == ticket_id)
    if created_from is not None:
        filters.append(TimeAccounting.create_time >= created_from)
    if created_to is not None:
        filters.append(TimeAccounting.create_time <= created_to)

    base = (
        select(
            TimeAccounting.id,
            TimeAccounting.ticket_id,
            Ticket.tn,
            Ticket.title,
            TimeAccounting.article_id,
            TimeAccounting.time_unit,
            TimeAccounting.create_time,
            TimeAccounting.create_by,
            Users.login,
        )
        .join(Ticket, Ticket.id == TimeAccounting.ticket_id)
        .join(Queue, Queue.id == Ticket.queue_id)
        .outerjoin(Users, Users.id == TimeAccounting.create_by)
        .where(*filters)
    )
    sum_stmt = (
        select(func.coalesce(func.sum(TimeAccounting.time_unit), 0))
        .select_from(TimeAccounting)
        .join(Ticket, Ticket.id == TimeAccounting.ticket_id)
        .join(Queue, Queue.id == Ticket.queue_id)
        .where(*filters)
    )
    rows = (
        await session.execute(
            base.order_by(TimeAccounting.create_time.desc(), TimeAccounting.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    total_units = float((await session.execute(sum_stmt)).scalar_one())

    items = [
        TimeAccountingReportEntry(
            id=int(r[0]),
            ticket_id=int(r[1]),
            ticket_tn=r[2],
            ticket_title=r[3],
            article_id=int(r[4]) if r[4] is not None else None,
            time_unit=float(r[5]),
            create_time=r[6],
            create_by=int(r[7]),
            create_by_login=r[8],
        )
        for r in rows
    ]
    return TimeAccountingReportOut(items=items, total_units=total_units, offset=offset, limit=limit)


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


@router.get("/{ticket_id}/field-options")
async def ticket_field_options(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    fields: str = Query(
        "state,queue,priority,type,service,sla",
        description="Comma-separated field names to return",
    ),
    action: str | None = Query(
        "AgentTicketZoom",
        description="Frontend Action for ACL Properties (e.g. AgentTicketZoom)",
    ),
) -> dict[str, dict[str, str]]:
    """ACL-filtered field options for an existing ticket (zoom/update forms).

    Requires ticket read access. Returns id→name maps under each field key.
    """
    from tiqora.api.v1.reference import collect_ticket_field_options

    try:
        detail = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc

    requested = {f.strip().lower() for f in fields.split(",") if f.strip()}
    result = await collect_ticket_field_options(
        session,
        user,
        fields=requested,
        ticket_id=ticket_id,
        action=action,
        queue_id=getattr(detail, "queue_id", None),
        service_id=getattr(detail, "service_id", None),
        type_id=getattr(detail, "type_id", None),
        state_id=getattr(detail, "state_id", None),
        priority_id=getattr(detail, "priority_id", None),
        sla_id=getattr(detail, "sla_id", None),
    )
    return result.model_dump()


@router.get("/{ticket_id}/print", response_class=HTMLResponse)
async def print_ticket(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    include_history: bool = Query(False, description="Also render ticket history."),
) -> HTMLResponse:
    """Printable HTML for browser print / Save as PDF (Znuny AgentTicketPrint).

    Returns a self-contained HTML document with ticket header metadata and
    article bodies. Prefer this over embedding a PDF library.
    """
    svc = TicketService(session)
    try:
        ticket = await svc.get_ticket(user.id, ticket_id)
        articles = await svc.list_articles(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc

    article_blocks: list[str] = []
    for art in articles:
        try:
            body = await svc.get_article_body(user.id, ticket_id, art.id)
            if body.is_html:
                body_html = body.body or ""
            else:
                body_html = f"<pre>{html_escape(body.body or '')}</pre>"
        except Exception:
            body_html = "<p><em>(body unavailable)</em></p>"
        meta = (
            f"<div class='meta'>"
            f"<strong>#{art.id}</strong> · {html_escape(art.sender_type or '')} · "
            f"{html_escape(str(art.create_time or ''))}<br/>"
            f"<span>{html_escape(art.from_address or '')}</span>"
            f"{' → ' + html_escape(art.to_address) if art.to_address else ''}"
            f"</div>"
        )
        subject = html_escape(art.subject or "(no subject)")
        article_blocks.append(
            f"<section class='article'>{meta}<h3>{subject}</h3>"
            f"<div class='body'>{body_html}</div></section>"
        )

    history_html = ""
    if include_history:
        try:
            history = await svc.list_history(user.id, ticket_id)
            rows = "".join(
                f"<tr><td>{html_escape(str(h.create_time or ''))}</td>"
                f"<td>{html_escape(h.history_type or '')}</td>"
                f"<td>{html_escape(h.rendered or h.name or '')}</td>"
                f"<td>{html_escape(h.create_by_login or str(h.create_by))}</td></tr>"
                for h in history
            )
            history_html = (
                "<h2>History</h2>"
                "<table><thead><tr><th>When</th><th>Type</th>"
                "<th>Detail</th><th>By</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        except Exception:
            history_html = ""

    title = html_escape(ticket.title or "")
    tn = html_escape(ticket.tn or str(ticket.id))
    owner = html_escape(ticket.owner_login or ticket.owner_name or "")
    customer = (
        f"{html_escape(ticket.customer_id or '')} {html_escape(ticket.customer_user_id or '')}"
    ).strip()
    css = (
        "body{font-family:system-ui,sans-serif;margin:1.5rem;color:#111}"
        "h1{font-size:1.25rem;margin:0 0 .25rem}"
        "h2{font-size:1.05rem;margin:1.5rem 0 .5rem;border-bottom:1px solid #ddd;"
        "padding-bottom:.25rem}"
        "h3{font-size:.95rem;margin:.4rem 0}"
        ".header{margin-bottom:1.25rem}"
        ".grid{display:grid;grid-template-columns:8rem 1fr;gap:.25rem .75rem;"
        "font-size:.875rem}"
        ".grid dt{color:#555}"
        ".article{border-top:1px solid #e5e5e5;padding:.75rem 0;"
        "page-break-inside:avoid}"
        ".meta{font-size:.8rem;color:#555;margin-bottom:.25rem}"
        ".body{font-size:.9rem;line-height:1.45}"
        ".body pre{white-space:pre-wrap;font-family:inherit;margin:0}"
        "table{width:100%;border-collapse:collapse;font-size:.8rem}"
        "th,td{text-align:left;padding:.3rem .4rem;border-bottom:1px solid #eee;"
        "vertical-align:top}"
        "@media print{body{margin:.5rem}.no-print{display:none}}"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Ticket {tn} — {title}</title>
<style>{css}</style>
</head>
<body>
  <div class="header">
    <p class="no-print"><button onclick="window.print()">Print</button></p>
    <h1>Ticket {tn}</h1>
    <p><strong>{title}</strong></p>
    <dl class="grid">
      <dt>Queue</dt><dd>{html_escape(ticket.queue_name or "")}</dd>
      <dt>State</dt><dd>{html_escape(ticket.state or "")}</dd>
      <dt>Priority</dt><dd>{html_escape(ticket.priority or "")}</dd>
      <dt>Owner</dt><dd>{owner}</dd>
      <dt>Customer</dt><dd>{customer}</dd>
      <dt>Service</dt><dd>{html_escape(ticket.service_name or "")}</dd>
      <dt>Created</dt><dd>{html_escape(str(ticket.create_time or ""))}</dd>
      <dt>Changed</dt><dd>{html_escape(str(ticket.change_time or ""))}</dd>
    </dl>
  </div>
  <h2>Articles ({len(article_blocks)})</h2>
  {"".join(article_blocks) or "<p>No articles.</p>"}
  {history_html}
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/{ticket_id}/similar", response_model=SimilarTicketsOut)
async def get_similar_tickets(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> SimilarTicketsOut:
    """Keyword-similar closed tickets (Meili rank; embedding blend later)."""
    try:
        ticket = await TicketService(session).get_ticket(user.id, ticket_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc

    svc = SearchIndexService(session, settings)
    try:
        # Read excerpt from the existing Meili doc (cheap); title-only fallback
        # if the ticket is not indexed yet. Avoids build_document SQL fan-out.
        excerpt = None
        doc = await svc.get_indexed_document(ticket_id)
        if doc is not None:
            raw_excerpt = doc.get("latest_article_excerpt")
            excerpt = str(raw_excerpt) if raw_excerpt else None
        return await svc.find_similar(
            user.id,
            ticket_id,
            title=ticket.title,
            excerpt=excerpt,
        )
    finally:
        await svc.close()


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


@router.get("/{ticket_id}/articles/{article_id}/plain", response_model=ArticleBody)
async def get_article_plain_body(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
) -> ArticleBody:
    """Return the plaintext body from ``article_data_mime_plain`` when present.

    Falls back to the rendered MIME body (same as ``/body``) when no plain
    row exists — Znuny stores the plain part separately for search/index use.
    Requires ``ro``.
    """
    try:
        plain = await TicketService(session).get_article_plain_body(user.id, ticket_id, article_id)
    except (TicketNotFound, TicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return ArticleBody(
        article_id=article_id,
        content_type=plain.content_type,
        is_html=plain.is_html,
        body=plain.body,
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
    # Central safe delivery: neutralizes active types (html/svg/xml), restricts
    # inline to raster images, sandboxes the response, sanitizes the filename.
    return safe_attachment_response(
        content_bytes,
        content_type,
        filename,
        disposition,
        force_download=force_download,
    )


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
    except (
        WriteAccessDenied,
        WriteNotFound,
        InvalidInput,
        OutboundMailError,
        TelegramDeliveryError,
    ) as exc:
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


@router.post("/{ticket_id}/acquire-lock", response_model=AcquireLockResponse)
async def acquire_ticket_lock(
    ticket_id: int,
    body: AcquireLockRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> AcquireLockResponse:
    """Znuny composer-open lock semantics (RequiredLock).

    Called when a composer dialog opens (and again with ``takeover`` from the
    "Übernehmen" banner). Locking an unlocked ticket makes the caller its
    owner; a foreign lock is reported as ``locked_by_other`` without writing.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            res = await svc.acquire_lock(
                user.id, ticket_id, action=body.action, takeover=body.takeover
            )
    except (WriteAccessDenied, WriteNotFound) as exc:
        raise _map_exc(exc) from exc
    return AcquireLockResponse(
        result=res.result,  # type: ignore[arg-type]
        locked_by_id=res.locked_by_id,
        locked_by_name=res.locked_by_name,
    )


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
            if body.type_id is not None:
                await svc.change_type(user.id, ticket_id, body.type_id)
            if body.service_id is not None or body.clear_service:
                await svc.change_service(
                    user.id,
                    ticket_id,
                    None if body.clear_service else body.service_id,
                )
            if body.sla_id is not None or body.clear_sla:
                await svc.change_sla(
                    user.id,
                    ticket_id,
                    None if body.clear_sla else body.sla_id,
                )
            if body.title is not None:
                await svc.change_title(user.id, ticket_id, body.title)
            if (
                body.customer_id is not None
                or body.customer_user_id is not None
                or body.clear_customer
            ):
                await svc.set_customer(
                    user.id,
                    ticket_id,
                    customer_id=None if body.clear_customer else body.customer_id,
                    customer_user_id=None if body.clear_customer else body.customer_user_id,
                )
            if body.owner_id is not None:
                await svc.assign_owner(user.id, ticket_id, body.owner_id)
            if body.responsible_id is not None:
                await svc.assign_responsible(user.id, ticket_id, body.responsible_id)
            if body.lock is not None:
                if body.lock == "lock":
                    # Znuny AgentTicketLock: locking via the menu also makes
                    # the agent the owner; unlock leaves the owner untouched.
                    await svc.lock_ticket(user.id, ticket_id, take_ownership=True)
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
    "/{ticket_id}/articles/{article_id}/resend",
    response_model=ArticleCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def resend_article_endpoint(
    ticket_id: int,
    article_id: int,
    body: BounceRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ArticleCreateResponse:
    """Alias of bounce — naming parity with Znuny AgentTicketEmailResend.

    Resends the article body verbatim to ``to_address`` (history type Bounce).
    Requires ``rw``.
    """
    return await bounce_article_endpoint(ticket_id, article_id, body, user, session, settings)


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
                user.id,
                ticket_id,
                article_id,
                queue_id=body.queue_id,
                title=body.title,
                priority_id=body.priority_id,
                state_id=body.state_id,
                customer_id=body.customer_id,
                customer_user_id=body.customer_user_id,
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    return TicketCreateResponse(ticket_id=new_id)


@router.delete("/{ticket_id}/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article_endpoint(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Hard-delete an internal note. Requires ``rw``.

    Customer-visible or non-Internal-channel articles are never deletable and
    return HTTP 409 instead.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            await svc.delete_article(user.id, ticket_id, article_id)
    except (WriteAccessDenied, WriteNotFound, ArticleNotDeletable) as exc:
        raise _map_exc(exc) from exc


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


_DRAFT_COLUMNS = "id, ticket_id, user_id, action, article_id, title, content, created, changed"


def _article_predicate(article_id: int | None) -> str:
    """NULL-safe match on ``article_id``.

    ``= :aid`` never matches NULL, and the portable spellings differ per
    dialect (``<=>`` vs ``IS NOT DISTINCT FROM``), so branch in Python
    instead. The parameter is only bound in the non-NULL case.
    """
    return "article_id IS NULL" if article_id is None else "article_id = :aid"


async def _assert_draft_ticket_readable(
    session: AsyncSession, user_id: int, ticket_id: int
) -> None:
    """Require ``ro`` on the ticket a draft is being stored against (L-1).

    Drafts are per-agent, but without this any authenticated agent could park
    content against — and confirm the existence of — a ticket they cannot see.

    Rolls the read-only lookup back before returning: it autobegins a
    transaction on the shared request session, and the draft writers open their
    own ``async with session.begin()`` right after (same dance as
    ``api.deps.get_current_user``).
    """
    try:
        await TicketService(session)._assert_ticket_ro(user_id, ticket_id)  # noqa: SLF001
    except (TicketAccessDenied, TicketNotFound) as exc:
        raise _map_exc(exc) from exc
    finally:
        await session.rollback()


@router.get("/{ticket_id}/drafts", response_model=list[DraftOut])
async def list_drafts(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[DraftOut]:
    """List form drafts for a ticket (current user only)."""
    from sqlalchemy import text

    await _assert_draft_ticket_readable(session, user.id, ticket_id)

    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_DRAFT_COLUMNS}"
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
    """Create or update a draft for (ticket, user, action, article)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    await _assert_draft_ticket_readable(session, user.id, ticket_id)

    article_id = body.article_id
    where = (
        f"ticket_id = :tid AND user_id = :uid AND action = :act"
        f" AND {_article_predicate(article_id)}"
    )
    params: dict[str, Any] = {"tid": ticket_id, "uid": user.id, "act": action}
    if article_id is not None:
        params["aid"] = article_id

    update = text(
        "UPDATE tiqora_form_draft SET title = :title, content = :content,"
        f" changed = current_timestamp WHERE {where}"
    )
    update_params = {**params, "title": body.title, "content": body.content}

    # UPDATE first, INSERT only if it matched nothing. A concurrent tab of
    # the same agent can still slip its INSERT in between the two (the
    # composer autosaves on a short debounce), which the unique constraint
    # from 20260807_0030 turns into an IntegrityError instead of a duplicate
    # row — retry as an UPDATE so the later write wins, as it would have
    # without the race.
    try:
        async with session.begin():
            result = await session.execute(update, update_params)
            # CursorResult.rowcount is not on the generic Result type stub.
            if int(getattr(result, "rowcount", 0) or 0) == 0:
                await session.execute(
                    text(
                        "INSERT INTO tiqora_form_draft"
                        " (ticket_id, user_id, action, article_id, title, content,"
                        " created, changed)"
                        " VALUES (:tid, :uid, :act, :aid_ins, :title, :content,"
                        " current_timestamp, current_timestamp)"
                    ),
                    {**update_params, "aid_ins": article_id},
                )
    except IntegrityError:
        async with session.begin():
            await session.execute(update, update_params)

    row = (
        (
            await session.execute(
                text(f"SELECT {_DRAFT_COLUMNS} FROM tiqora_form_draft WHERE {where} LIMIT 1"),
                params,
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
    article_id: int | None = Query(
        default=None,
        description="Article the draft belongs to; omit for the ticket-wide draft.",
    ),
) -> None:
    """Delete one draft for (ticket, user, action, article).

    Scoped to a single article so discarding one reply draft leaves the
    agent's drafts on the ticket's other articles alone.
    """
    from sqlalchemy import text

    await _assert_draft_ticket_readable(session, user.id, ticket_id)

    params: dict[str, Any] = {"tid": ticket_id, "uid": user.id, "act": action}
    if article_id is not None:
        params["aid"] = article_id

    async with session.begin():
        await session.execute(
            text(
                "DELETE FROM tiqora_form_draft"
                " WHERE ticket_id = :tid AND user_id = :uid AND action = :act"
                f" AND {_article_predicate(article_id)}"
            ),
            params,
        )


# ---------------------------------------------------------------------------
# Mentions (Znuny ``mention`` table, schema ≥ 6.4)
# ---------------------------------------------------------------------------


class MentionOut(BaseModel):
    id: int
    user_id: int
    ticket_id: int
    article_id: int | None = None
    create_time: datetime | None = None
    user_login: str | None = None
    user_name: str | None = None


class MentionCreate(BaseModel):
    user_id: int
    article_id: int | None = None


@router.get("/{ticket_id}/mentions", response_model=list[MentionOut])
async def list_mentions(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[MentionOut]:
    """List agent mentions on a ticket (requires ticket ``ro``)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_service import TicketService

    try:
        await TicketService(session)._assert_ticket_ro(user.id, ticket_id)
    except (TicketAccessDenied, TicketNotFound) as exc:
        raise _map_exc(exc) from exc

    # Table may be absent on pre-6.4 peers — return empty rather than 500.
    try:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT m.id, m.user_id, m.ticket_id, m.article_id, m.create_time,"
                        " u.login AS user_login, u.first_name, u.last_name"
                        " FROM mention m"
                        " LEFT JOIN users u ON u.id = m.user_id"
                        " WHERE m.ticket_id = :tid ORDER BY m.id"
                    ),
                    {"tid": ticket_id},
                )
            )
            .mappings()
            .all()
        )
    except Exception:
        return []
    out: list[MentionOut] = []
    for r in rows:
        parts = [str(r.get("first_name") or "").strip(), str(r.get("last_name") or "").strip()]
        name = " ".join(p for p in parts if p) or None
        out.append(
            MentionOut(
                id=int(r["id"]),
                user_id=int(r["user_id"]),
                ticket_id=int(r["ticket_id"]),
                article_id=int(r["article_id"]) if r.get("article_id") is not None else None,
                create_time=r.get("create_time"),
                user_login=r.get("user_login"),
                user_name=name,
            )
        )
    return out


@router.post(
    "/{ticket_id}/mentions",
    response_model=MentionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_mention(
    ticket_id: int,
    body: MentionCreate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MentionOut:
    """Mention an agent on a ticket (requires ``note``)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_write_service import _ticket_must_exist

    svc = _write_service(session, settings)
    try:
        async with session.begin():
            ticket = await _ticket_must_exist(session, ticket_id)
            await svc._assert(user.id, int(ticket["queue_id"]), "note")
            await session.execute(
                text(
                    "INSERT INTO mention (user_id, ticket_id, article_id, create_time)"
                    " VALUES (:uid, :tid, :aid, current_timestamp)"
                ),
                {
                    "uid": body.user_id,
                    "tid": ticket_id,
                    "aid": body.article_id,
                },
            )
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT m.id, m.user_id, m.ticket_id, m.article_id, m.create_time,"
                            " u.login AS user_login"
                            " FROM mention m LEFT JOIN users u ON u.id = m.user_id"
                            " WHERE m.ticket_id = :tid AND m.user_id = :uid"
                            " ORDER BY m.id DESC LIMIT 1"
                        ),
                        {"tid": ticket_id, "uid": body.user_id},
                    )
                )
                .mappings()
                .first()
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Mentions not available on this peer schema",
        ) from exc
    if row is None:
        raise HTTPException(status_code=500, detail="Mention insert failed")
    return MentionOut(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        ticket_id=int(row["ticket_id"]),
        article_id=int(row["article_id"]) if row.get("article_id") is not None else None,
        create_time=row.get("create_time"),
        user_login=row.get("user_login"),
    )


@router.delete(
    "/{ticket_id}/mentions/{mention_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_mention(
    ticket_id: int,
    mention_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Remove a mention (requires ``note`` on the ticket)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_write_service import _ticket_must_exist

    svc = _write_service(session, settings)
    try:
        async with session.begin():
            ticket = await _ticket_must_exist(session, ticket_id)
            await svc._assert(user.id, int(ticket["queue_id"]), "note")
            result = await session.execute(
                text("DELETE FROM mention WHERE id = :mid AND ticket_id = :tid"),
                {"mid": mention_id, "tid": ticket_id},
            )
            if int(getattr(result, "rowcount", 0) or 0) == 0:
                raise WriteNotFound(f"mention {mention_id}")
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc


# ---------------------------------------------------------------------------
# Time accounting (Znuny ``time_accounting``)
# ---------------------------------------------------------------------------


class TimeAccountingOut(BaseModel):
    id: int
    ticket_id: int
    article_id: int | None = None
    time_unit: float
    create_time: datetime | None = None
    create_by: int
    create_by_login: str | None = None


class TimeAccountingCreate(BaseModel):
    time_unit: float
    article_id: int | None = None


@router.get("/{ticket_id}/time-accounting", response_model=list[TimeAccountingOut])
async def list_time_accounting(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> list[TimeAccountingOut]:
    """List time-accounting rows for a ticket (requires ``ro``)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_service import TicketService

    try:
        await TicketService(session)._assert_ticket_ro(user.id, ticket_id)
    except (TicketAccessDenied, TicketNotFound) as exc:
        raise _map_exc(exc) from exc

    rows = (
        (
            await session.execute(
                text(
                    "SELECT ta.id, ta.ticket_id, ta.article_id, ta.time_unit,"
                    " ta.create_time, ta.create_by, u.login AS create_by_login"
                    " FROM time_accounting ta"
                    " LEFT JOIN users u ON u.id = ta.create_by"
                    " WHERE ta.ticket_id = :tid ORDER BY ta.id"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        TimeAccountingOut(
            id=int(r["id"]),
            ticket_id=int(r["ticket_id"]),
            article_id=int(r["article_id"]) if r.get("article_id") is not None else None,
            time_unit=float(r["time_unit"]),
            create_time=r.get("create_time"),
            create_by=int(r["create_by"]),
            create_by_login=r.get("create_by_login"),
        )
        for r in rows
    ]


@router.post(
    "/{ticket_id}/time-accounting",
    response_model=TimeAccountingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_time_accounting(
    ticket_id: int,
    body: TimeAccountingCreate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> TimeAccountingOut:
    """Book time units on a ticket (requires ``rw``)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_write_service import _ticket_must_exist

    if body.time_unit <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="time_unit must be positive",
        )
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            ticket = await _ticket_must_exist(session, ticket_id)
            await svc._assert_rw(user.id, int(ticket["queue_id"]))
            await session.execute(
                text(
                    "INSERT INTO time_accounting"
                    " (ticket_id, article_id, time_unit, create_time, create_by,"
                    "  change_time, change_by)"
                    " VALUES (:tid, :aid, :units, current_timestamp, :uid,"
                    "         current_timestamp, :uid)"
                ),
                {
                    "tid": ticket_id,
                    "aid": body.article_id,
                    "units": body.time_unit,
                    "uid": user.id,
                },
            )
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT ta.id, ta.ticket_id, ta.article_id, ta.time_unit,"
                            " ta.create_time, ta.create_by, u.login AS create_by_login"
                            " FROM time_accounting ta"
                            " LEFT JOIN users u ON u.id = ta.create_by"
                            " WHERE ta.ticket_id = :tid AND ta.create_by = :uid"
                            " ORDER BY ta.id DESC LIMIT 1"
                        ),
                        {"tid": ticket_id, "uid": user.id},
                    )
                )
                .mappings()
                .first()
            )
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
    if row is None:
        raise HTTPException(status_code=500, detail="Time accounting insert failed")
    return TimeAccountingOut(
        id=int(row["id"]),
        ticket_id=int(row["ticket_id"]),
        article_id=int(row["article_id"]) if row.get("article_id") is not None else None,
        time_unit=float(row["time_unit"]),
        create_time=row.get("create_time"),
        create_by=int(row["create_by"]),
        create_by_login=row.get("create_by_login"),
    )


@router.delete(
    "/{ticket_id}/time-accounting/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_time_accounting(
    ticket_id: int,
    entry_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Delete a time-accounting row (requires ``rw``)."""
    from sqlalchemy import text

    from tiqora.domain.ticket_write_service import _ticket_must_exist

    svc = _write_service(session, settings)
    try:
        async with session.begin():
            ticket = await _ticket_must_exist(session, ticket_id)
            await svc._assert_rw(user.id, int(ticket["queue_id"]))
            result = await session.execute(
                text("DELETE FROM time_accounting WHERE id = :eid AND ticket_id = :tid"),
                {"eid": entry_id, "tid": ticket_id},
            )
            if int(getattr(result, "rowcount", 0) or 0) == 0:
                raise WriteNotFound(f"time_accounting {entry_id}")
    except (WriteAccessDenied, WriteNotFound, InvalidInput) as exc:
        raise _map_exc(exc) from exc
