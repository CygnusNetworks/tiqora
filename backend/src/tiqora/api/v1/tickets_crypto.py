"""Compose options for PGP / S/MIME: ``GET /tickets[/{id}]/crypto-options``.

Feeds the compose "Sicherheit" control (reply, forward, new email ticket):
the sender's sign keys with the queue's ``default_sign_key`` preselected,
every recipient's encryption keys and their status, and warnings. Mounted
under ``/tickets`` **before** the main tickets router so the id-less route
is not captured by ``/tickets/{ticket_id}``. See
:mod:`tiqora.crypto.compose`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.channels.email.outbound_reply import queue_outbound_meta
from tiqora.crypto.compose import CryptoOptionsOut, crypto_options, split_addresses
from tiqora.crypto.config import load_crypto_config
from tiqora.domain.ticket_service import TicketAccessDenied, TicketNotFound, TicketService
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _options(
    session: AsyncSession, queue_id: int, to: str | None, cc: str | None, bcc: str | None
) -> CryptoOptionsOut:
    try:
        from_line, _name, _sig, _ct = await queue_outbound_meta(session, queue_id)
    except InvalidInput as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    default_sign_key = (
        await session.execute(
            text("SELECT default_sign_key FROM queue WHERE id = :qid"), {"qid": queue_id}
        )
    ).scalar_one_or_none()
    config = await load_crypto_config(session)
    if not (config.pgp.enabled or config.smime.enabled):
        return CryptoOptionsOut(enabled=False, from_address=None)
    return await crypto_options(
        config,
        from_address=from_line,
        recipients=split_addresses(to, cc, bcc),
        default_sign_key=str(default_sign_key) if default_sign_key else None,
    )


@router.get("/crypto-options", response_model=CryptoOptionsOut)
async def new_ticket_crypto_options(
    user: CurrentUser,
    session: DbSession,
    queue_id: int = Query(...),
    to: str | None = Query(None),
    cc: str | None = Query(None),
    bcc: str | None = Query(None),
) -> CryptoOptionsOut:
    """Crypto compose options for a new email ticket in *queue_id* (``create`` right)."""
    if not await PermissionEngine(session).check(user.id, queue_id, "create"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return await _options(session, queue_id, to, cc, bcc)


@router.get("/{ticket_id}/crypto-options", response_model=CryptoOptionsOut)
async def ticket_crypto_options(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    to: str | None = Query(None),
    cc: str | None = Query(None),
    bcc: str | None = Query(None),
) -> CryptoOptionsOut:
    """Crypto compose options for a reply/forward on *ticket_id* (sender = its queue)."""
    try:
        ticket = await TicketService(session)._assert_ticket_ro(user.id, ticket_id)  # noqa: SLF001
    except TicketNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except TicketAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc
    return await _options(session, int(ticket.queue_id), to, cc, bcc)
