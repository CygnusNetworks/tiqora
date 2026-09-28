"""Telegram chat-composer endpoints: chat info, typing, edit, retract, button
removal (Task 6).

Split out of ``api.v1.tickets`` (already large) into its own router, still
mounted under the same ``/tickets`` prefix. Reuses ``tickets.py``'s
``TelegramButtonIn`` schema and ``_write_service`` helper, and
``tiqora.channels.telegram.chat_actions`` for the actual DB/gateway work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from tiqora.api.deps import AppSettings, CurrentUser, DbSession
from tiqora.api.v1.tickets import TelegramButtonIn, _write_service
from tiqora.channels.telegram import chat_actions
from tiqora.channels.telegram.messages import buttons_from_json
from tiqora.channels.telegram.outbound import TelegramDeliveryError
from tiqora.db.tiqora.models import TiqoraTelegramMessage
from tiqora.domain.ticket_service import (
    TicketAccessDenied,
    TicketNotFound,
    TicketService,
)
from tiqora.domain.ticket_write_service import (
    TicketAccessDenied as WriteAccessDenied,
)
from tiqora.domain.ticket_write_service import (
    TicketNotFound as WriteNotFound,
)
from tiqora.domain.ticket_write_service import (
    _ticket_must_exist,  # noqa: PLC2701 -- deliberate reuse
)

router = APIRouter(prefix="/tickets", tags=["tickets"])


class TelegramMessageMeta(BaseModel):
    article_id: int
    direction: Literal["in", "out"]
    reply_to_article_id: int | None
    buttons: list[TelegramButtonIn]
    answered_button: int | None
    edited_at: datetime | None
    retracted_at: datetime | None
    # False for inbound, retracted, and attachment-only messages (Telegram
    # can only edit a text message's text).
    editable: bool


class TelegramChatOut(BaseModel):
    chat_id: int
    username: str | None
    display_name: str | None
    identity_verified: bool
    customer_user_login: str | None
    consent_time: datetime | None
    ai_escalated_at: datetime | None
    messages: list[TelegramMessageMeta]


class TelegramEditRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4096)


def _map_action_exc(exc: Exception) -> HTTPException:
    """Edit/retract: a message was sent, so "can't reach Telegram" (no
    bot_token, channel gone) is a conflict with a reason, not a 404."""
    if isinstance(exc, TelegramDeliveryError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _map_exc(exc)


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            WriteNotFound,
            TicketNotFound,
            chat_actions.TelegramMessageNotFound,
            TelegramDeliveryError,
        ),
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (WriteAccessDenied, TicketAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(exc, chat_actions.TelegramActionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


def _message_meta(row: TiqoraTelegramMessage, *, editable: bool) -> TelegramMessageMeta:
    buttons = buttons_from_json(row.buttons_json)
    return TelegramMessageMeta(
        article_id=row.article_id,
        direction=row.direction,  # type: ignore[arg-type]
        reply_to_article_id=row.reply_to_article_id,
        buttons=[TelegramButtonIn(label=b.label, action=b.action) for b in buttons],
        answered_button=row.answered_button,
        edited_at=row.edited_at,
        retracted_at=row.retracted_at,
        editable=editable,
    )


@router.get("/{ticket_id}/telegram", response_model=TelegramChatOut)
async def get_telegram_chat(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
) -> TelegramChatOut:
    """Telegram chat info for a ticket: contact identity + per-article message
    metadata (edit/retract state, buttons, answered button). Requires ``ro``.
    404 when the ticket has no Telegram chat to resolve.
    """
    try:
        await TicketService(session)._assert_ticket_ro(user.id, ticket_id)
        info = await chat_actions.get_chat_info(session, ticket_id)
    except (TicketNotFound, TicketAccessDenied, TelegramDeliveryError) as exc:
        raise _map_exc(exc) from exc
    return TelegramChatOut(
        chat_id=info.contact.chat_id,
        username=info.contact.username,
        display_name=info.contact.display_name,
        identity_verified=bool(info.contact.customer_user_login),
        customer_user_login=info.contact.customer_user_login,
        consent_time=info.contact.consent_time,
        ai_escalated_at=info.ai_escalated_at,
        messages=[
            _message_meta(m, editable=m.article_id in info.editable_article_ids)
            for m in info.messages
        ],
    )


@router.post("/{ticket_id}/telegram/typing", status_code=status.HTTP_204_NO_CONTENT)
async def send_telegram_typing(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Best-effort typing indicator for the ticket's Telegram chat. Requires
    ``note``. Meant to be called at most every few seconds while an agent is
    composing -- no server-side throttle, the frontend paces its own calls.
    """
    svc = _write_service(session, settings)
    try:
        t = await _ticket_must_exist(session, ticket_id)
        await svc._assert(user.id, int(t["queue_id"]), "note")
        await chat_actions.send_typing(session, ticket_id)
    except (WriteNotFound, WriteAccessDenied, TelegramDeliveryError) as exc:
        raise _map_exc(exc) from exc


@router.patch(
    "/{ticket_id}/articles/{article_id}/telegram",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def edit_telegram_article(
    ticket_id: int,
    article_id: int,
    body: TelegramEditRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Edit a previously sent agent Telegram message's text (keeps its
    keyboard unless already answered). Requires ``note``.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            t = await _ticket_must_exist(session, ticket_id)
            await svc._assert(user.id, int(t["queue_id"]), "note")
            await chat_actions.edit_message(
                session,
                ticket_id=ticket_id,
                article_id=article_id,
                user_id=user.id,
                body=body.body,
            )
    except (
        WriteNotFound,
        WriteAccessDenied,
        chat_actions.TelegramMessageNotFound,
        chat_actions.TelegramActionConflict,
        TelegramDeliveryError,
    ) as exc:
        raise _map_action_exc(exc) from exc


@router.post(
    "/{ticket_id}/articles/{article_id}/telegram/retract",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def retract_telegram_article(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Delete a previously sent agent Telegram message (and its attachment
    parts). Requires ``note``.
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            t = await _ticket_must_exist(session, ticket_id)
            await svc._assert(user.id, int(t["queue_id"]), "note")
            await chat_actions.retract_message(
                session, ticket_id=ticket_id, article_id=article_id, user_id=user.id
            )
    except (
        WriteNotFound,
        WriteAccessDenied,
        chat_actions.TelegramMessageNotFound,
        chat_actions.TelegramActionConflict,
        TelegramDeliveryError,
    ) as exc:
        raise _map_action_exc(exc) from exc


@router.delete(
    "/{ticket_id}/articles/{article_id}/telegram/buttons",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_telegram_article_buttons(
    ticket_id: int,
    article_id: int,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Remove the inline keyboard from a previously sent agent Telegram
    message that no button has been answered on yet. Requires ``note``
    (same as edit/retract).
    """
    svc = _write_service(session, settings)
    try:
        async with session.begin():
            t = await _ticket_must_exist(session, ticket_id)
            await svc._assert(user.id, int(t["queue_id"]), "note")
            await chat_actions.remove_buttons(
                session, ticket_id=ticket_id, article_id=article_id, user_id=user.id
            )
    except (
        WriteNotFound,
        WriteAccessDenied,
        chat_actions.TelegramMessageNotFound,
        chat_actions.TelegramActionConflict,
        TelegramDeliveryError,
    ) as exc:
        raise _map_action_exc(exc) from exc


__all__ = ["router"]
