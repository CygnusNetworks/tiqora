"""Endpoints for external integrations (e.g. netadmin).

API-key scope area: ``tickets`` (``tiqora.domain.api_key_scopes``). The GET
endpoint is read-only (``tickets:ro`` suffices); ``POST /outbound-ticket``
writes and needs ``tickets:rw``. Queue permissions of the key's agent user
apply as everywhere else.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.api.v1.tickets import _write_service
from tiqora.channels.email.outbound_reply import OutboundMailError
from tiqora.domain.integrations.customer_tickets import (
    LOGIN_MAX_LENGTH,
    InvalidLogin,
    list_customer_tickets,
)
from tiqora.domain.integrations.outbound_ticket import (
    OutboundMailDisabled,
    QueueNotFound,
    create_outbound_ticket,
)
from tiqora.domain.schemas import CustomerTicketsOut, OutboundTicketIn, OutboundTicketOut
from tiqora.domain.ticket_write_service import InvalidInput, TicketAccessDenied, TicketNotFound
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/integrations", tags=["integrations"])

_LOGIN_DESC = (
    "Customer login WITHOUT the contract suffix (e.g. z50test). Matches tickets whose"
    " customer_user_id equals it or starts with it plus a configured customer-link"
    " login_suffix_separator (z50test#1, z50test#3). Must not contain a separator,"
    " whitespace or control characters."
)


@router.get("/customer-tickets", response_model=CustomerTicketsOut)
async def customer_tickets(
    user: CurrentUser,
    session: DbSession,
    login: str = Query(..., min_length=1, max_length=LOGIN_MAX_LENGTH, description=_LOGIN_DESC),
    limit: int = Query(100, ge=1, le=200),
) -> CustomerTicketsOut:
    """Tickets of one customer account, newest first (``create_time`` desc,
    then id desc), archived tickets included. Tickets in queues the caller
    cannot read (``ro``) are silently omitted. ``email_count`` and the
    first/last article times cover customer-visible ``Email`` articles only;
    ``summary`` is the stored AI summary (never generated here)."""
    try:
        return await list_customer_tickets(session, user.id, login, limit=limit)
    except InvalidLogin as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.post("/outbound-ticket", response_model=OutboundTicketOut, status_code=201)
async def outbound_ticket(
    body: OutboundTicketIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> OutboundTicketOut:
    """Create a ticket in ``queue`` with one customer-visible outgoing e-mail
    to ``to_address`` and (``close`` true, the default) close it.

    Needs ``tickets:rw`` and ``create`` + ``note`` + ``rw`` on the queue's
    group. AI automation is paused on the ticket before the article is
    created and no agent notification is raised; the text stays stored in the
    ticket. 404 unknown queue, 403 missing permission, 503
    ``outbound_mail_disabled`` (nothing is created), 502 mail delivery failed
    (nothing is stored).
    """
    try:
        return await create_outbound_ticket(
            session, SysConfig(session), _write_service(session, settings), user.id, body
        )
    except QueueNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found"
        ) from exc
    except OutboundMailDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="outbound_mail_disabled"
        ) from exc
    except TicketAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc
    except (TicketNotFound, InvalidInput) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except OutboundMailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Outbound email delivery failed: {exc}",
        ) from exc
