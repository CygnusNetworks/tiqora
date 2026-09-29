"""``POST /tickets/{ticket_id}/phone-calls`` -- log a phone call on a ticket.

The agent-UI counterpart of Znuny's AgentTicketPhoneInbound/Outbound screens;
all logic lives in :func:`tiqora.channels.phone.service.log_phone_call_on_ticket`
(shared with the new phone ticket flow and the CTI webhook). Split out of
``api.v1.tickets`` like ``tickets_telegram``, same ``/tickets`` prefix.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.tickets import ArticleAttachmentIn, decode_attachments
from tiqora.channels.phone.service import (
    PhoneCallIn,
    PhoneCallLockedByOther,
    log_phone_call_on_ticket,
)
from tiqora.domain.ticket_write_service import InvalidInput, TicketAccessDenied, TicketNotFound
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/tickets", tags=["tickets"])


class PhoneCallRequest(BaseModel):
    direction: Literal["inbound", "outbound"]
    subject: str = Field(min_length=1, max_length=3800)
    body: str
    content_type: Literal["text/plain", "text/html"] = "text/plain"
    is_visible_for_customer: bool = True
    #: Next state; a pending state needs ``pending_time`` (422 otherwise).
    state_id: int | None = None
    pending_time: datetime | None = None
    #: Time units booked on the new article.
    time_unit: float | None = Field(default=None, ge=0)
    #: Ticket dynamic fields ``{name: [values]}``; unknown names are ignored.
    dynamic_fields: dict[str, list[str]] = Field(default_factory=dict)
    attachments: list[ArticleAttachmentIn] = Field(default_factory=list, max_length=20)
    #: The other party's number (From/To when the ticket has no customer).
    caller_number: str | None = Field(default=None, max_length=100)

    def to_call(self) -> PhoneCallIn:
        return PhoneCallIn(
            direction=self.direction,
            subject=self.subject,
            body=self.body,
            content_type=f"{self.content_type}; charset=utf-8",
            is_visible_for_customer=self.is_visible_for_customer,
            state_id=self.state_id,
            pending_time=self.pending_time,
            time_unit=self.time_unit,
            dynamic_fields=self.dynamic_fields,
            attachments=decode_attachments(self.attachments),
            caller_number=self.caller_number,
        )


class PhoneCallResponse(BaseModel):
    article_id: int
    ticket_id: int
    time_accounting_id: int | None
    #: True when this call locked the ticket (outbound RequiredLock).
    locked: bool


class PhoneCallLockedDetail(BaseModel):
    message: str
    locked_by_id: int | None
    locked_by_name: str | None


def phone_call_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PhoneCallLockedByOther):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=PhoneCallLockedDetail(
                message="ticket is locked by another agent",
                locked_by_id=exc.locked_by_id,
                locked_by_name=exc.locked_by_name,
            ).model_dump(),
        )
    if isinstance(exc, TicketNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if isinstance(exc, TicketAccessDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.post(
    "/{ticket_id}/phone-calls",
    response_model=PhoneCallResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Outbound call on a ticket locked by another agent"}},
)
async def log_phone_call_endpoint(
    ticket_id: int,
    body: PhoneCallRequest,
    user: CurrentUser,
    session: DbSession,
) -> PhoneCallResponse:
    """Log an inbound or outbound phone call (Znuny AgentTicketPhoneInbound/
    Outbound). Requires ``rw`` on the ticket's queue.

    One transaction: ``Phone`` article (sender customer/agent, history
    PhoneCallCustomer/PhoneCallAgent), time accounting bound to it, ticket
    dynamic fields, attachments and the next state. Outbound calls lock the
    ticket to the agent (``AgentTicketPhoneOutbound###RequiredLock``); a lock
    held by another agent is a 409 with ``locked_by_*`` in the detail.
    """
    call = body.to_call()
    try:
        async with session.begin():
            result = await log_phone_call_on_ticket(
                session, SysConfig(session), ticket_id=ticket_id, user_id=user.id, call=call
            )
    except (PhoneCallLockedByOther, TicketNotFound, TicketAccessDenied, InvalidInput) as exc:
        raise phone_call_http_error(exc) from exc
    return PhoneCallResponse(
        article_id=result.article_id,
        ticket_id=result.ticket_id,
        time_accounting_id=result.time_accounting_id,
        locked=result.locked,
    )
