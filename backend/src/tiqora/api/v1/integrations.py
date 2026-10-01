"""Read-only endpoints for external integrations (e.g. netadmin).

API-key scope area: ``tickets`` (``tiqora.domain.api_key_scopes``), so a key
with ``tickets:ro`` may call them. Queue permissions of the key's agent user
apply as everywhere else.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.integrations.customer_tickets import (
    LOGIN_MAX_LENGTH,
    InvalidLogin,
    list_customer_tickets,
)
from tiqora.domain.schemas import CustomerTicketsOut

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
