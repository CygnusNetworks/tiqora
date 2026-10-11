"""Agent web-chat endpoints (C8 "Agent"): availability, topics, inbox, accept, end, and the
ticket's chat info and agent typing (``/tickets/{id}/chat…``, ``ticket_router``).

Agents keep full access while the webchat channel or a chat config is switched off
(R-DISABLED-SESSIONS): nothing here checks ``channel_enabled`` — "off" stops new visitor
input, not the agents' work on chats that exist.

Transactions (R-TX): handlers never open ``db.begin()``. Reads and the service's writes run
in the request session's autobegun transaction; the handler commits, then publishes the
queued chat notifications (``flush_chat_notifications``), or rolls back on a refusal.
``ChatError`` → ``{"detail": "<code>"}`` with its HTTP status.

Agent replies into the chat are normal articles (``POST /tickets/{id}/articles`` with
``channel=webchat``, see ``webchat.agent.deliver_agent_chat_reply``).
"""

from __future__ import annotations

from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession, RedisDep
from tiqora.domain.ticket_write_service import TicketAccessDenied, TicketNotFound
from tiqora.jobs.service import utc_now
from tiqora.webchat.agent import (
    ChatNotLive,
    RwQueues,
    accept_session,
    agent_typing,
    end_session_by_agent,
    list_agent_topics,
    list_inbox,
    ticket_chat_info,
)
from tiqora.webchat.availability import (
    active_count,
    ensure_agent_state_row,
    get_agent_state,
    set_agent_state,
)
from tiqora.webchat.notify import flush_chat_notifications
from tiqora.webchat.schemas import (
    ChatAcceptOut,
    ChatAgentStateIn,
    ChatAgentStateOut,
    ChatEndIn,
    ChatInboxOut,
    ChatTopicRefOut,
    TicketChatOut,
)
from tiqora.webchat.sessions import ChatError
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/chat", tags=["chat"])
#: ``/tickets/{id}/chat…`` — the chat of one ticket (registered next to ``router``).
ticket_router = APIRouter(prefix="/tickets", tags=["chat"])

SessionId = Annotated[str, Path(min_length=1, max_length=64)]

_ACCEPT_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "No rw permission on the chat's queue (`forbidden`)"},
    404: {"description": "Unknown chat (`not_found`)"},
    409: {"description": "`already_taken`, `at_capacity` or `not_waiting`"},
}
_TICKET_CHAT_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "No ro permission on the ticket's queue (`forbidden`)"},
    404: {"description": "Unknown ticket or no chat on it (`not_found`)"},
}
_TYPING_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "No note permission on the ticket's queue (`forbidden`)"},
    404: {"description": "Unknown ticket (`not_found`)"},
    409: {"description": "The ticket has no live chat (`chat_not_live`)"},
}
_END_ERRORS: Final[dict[int | str, dict[str, Any]]] = {
    403: {"description": "No rw permission on the chat's queue (`forbidden`)"},
    404: {"description": "Unknown chat (`not_found`)"},
}


async def _refuse(db: AsyncSession, exc: ChatError) -> NoReturn:
    await db.rollback()
    raise HTTPException(status_code=exc.http_status, detail=exc.code) from exc


async def _state_out(db: AsyncSession, user_id: int) -> ChatAgentStateOut:
    state = await get_agent_state(db, user_id)
    return ChatAgentStateOut(
        available=state.available,
        topics=list(state.topics),
        max_concurrent=state.max_concurrent,
        active_count=await active_count(db, user_id),
    )


@router.get("/agent-state", response_model=ChatAgentStateOut)
async def get_chat_agent_state(user: CurrentUser, db: DbSession) -> ChatAgentStateOut:
    """The agent's "Chats annehmen" state and how many chats they hold now."""
    return await _state_out(db, user.id)


@router.put(
    "/agent-state",
    response_model=ChatAgentStateOut,
    responses={422: {"description": "A topic ref the agent may not take (`unknown_topic`)"}},
)
async def put_chat_agent_state(
    body: ChatAgentStateIn, user: CurrentUser, db: DbSession
) -> ChatAgentStateOut:
    """Set availability, topic filter (refs from ``GET /chat/topics``; empty = all) and the
    concurrent-chat limit."""
    allowed = {t.ref for t in await list_agent_topics(db, user_id=user.id)}
    if any(ref not in allowed for ref in body.topics):
        await db.rollback()
        raise HTTPException(status_code=422, detail="unknown_topic")
    await set_agent_state(
        db,
        user.id,
        available=body.available,
        topics=body.topics,
        max_concurrent=body.max_concurrent,
        now=utc_now(),
    )
    await db.commit()
    return await _state_out(db, user.id)


@router.get("/topics", response_model=list[ChatTopicRefOut])
async def get_chat_topics(user: CurrentUser, db: DbSession) -> list[ChatTopicRefOut]:
    """Topics of valid chat configs whose queue the agent holds ``rw`` on."""
    return await list_agent_topics(db, user_id=user.id, rw=RwQueues(db, user.id))


@router.get("/inbox", response_model=ChatInboxOut)
async def get_chat_inbox(user: CurrentUser, db: DbSession) -> ChatInboxOut:
    """Waiting chats the agent may accept and the agent's own active chats."""
    return await list_inbox(db, user_id=user.id)


@router.post(
    "/sessions/{session_id}/accept", response_model=ChatAcceptOut, responses=_ACCEPT_ERRORS
)
async def post_chat_accept(
    session_id: SessionId, user: CurrentUser, db: DbSession, redis_client: RedisDep
) -> ChatAcceptOut:
    """Take a waiting chat (first accept wins; owner + lock like Znuny)."""
    now = utc_now()
    # The agent's state row exists before the accept transaction starts: accept_session's
    # first statement is its locking read (MariaDB snapshot timing, see webchat.agent), and
    # a locking read of a missing row would take a gap lock.
    await ensure_agent_state_row(db, user.id, now=now)
    await db.commit()
    try:
        chat = await accept_session(
            db, SysConfig(db), session_id=session_id, user_id=user.id, now=now
        )
    except ChatError as exc:
        await _refuse(db, exc)
    ticket_id = int(chat.ticket_id)
    await db.commit()
    await flush_chat_notifications(db, redis_client)
    return ChatAcceptOut(ticket_id=ticket_id)


@router.post(
    "/sessions/{session_id}/end",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_END_ERRORS,
)
async def post_chat_end(
    session_id: SessionId,
    user: CurrentUser,
    db: DbSession,
    redis_client: RedisDep,
    body: ChatEndIn | None = None,
) -> Response:
    """End a live chat; optionally close the ticket. Idempotent (204 again when ended)."""
    try:
        await end_session_by_agent(
            db,
            SysConfig(db),
            session_id=session_id,
            user_id=user.id,
            close_ticket=bool(body and body.close_ticket),
            now=utc_now(),
        )
    except ChatError as exc:
        await _refuse(db, exc)
    await db.commit()
    await flush_chat_notifications(db, redis_client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- the chat of one ticket ----------------------------------------------------------------


def _ticket_refusal(
    exc: TicketNotFound | TicketAccessDenied | ChatNotLive | ChatError,
) -> HTTPException:
    if isinstance(exc, TicketNotFound):
        return HTTPException(status_code=404, detail="not_found")
    if isinstance(exc, TicketAccessDenied):
        return HTTPException(status_code=403, detail="forbidden")
    if isinstance(exc, ChatNotLive):
        return HTTPException(status_code=409, detail="chat_not_live")
    return HTTPException(status_code=exc.http_status, detail=exc.code)


@ticket_router.get("/{ticket_id}/chat", response_model=TicketChatOut, responses=_TICKET_CHAT_ERRORS)
async def get_ticket_chat(
    ticket_id: int, user: CurrentUser, db: DbSession, redis_client: RedisDep
) -> TicketChatOut:
    """The ticket's newest chat for the conversation header (visitor, topic, status,
    online/typing); 404 when the ticket has none."""
    try:
        return await ticket_chat_info(db, redis_client, ticket_id=ticket_id, user_id=user.id)
    except (TicketNotFound, TicketAccessDenied, ChatError) as exc:
        raise _ticket_refusal(exc) from exc


@ticket_router.post(
    "/{ticket_id}/chat/typing",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_TYPING_ERRORS,
)
async def post_ticket_chat_typing(
    ticket_id: int, user: CurrentUser, db: DbSession, redis_client: RedisDep
) -> Response:
    """The agent is typing in the ticket's live chat (the visitor sees it; at most one
    signal per 2 s, the rest is dropped). Needs ``note`` on the ticket's queue."""
    try:
        await agent_typing(db, redis_client, ticket_id=ticket_id, user_id=user.id)
    except (TicketNotFound, TicketAccessDenied, ChatNotLive) as exc:
        raise _ticket_refusal(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
