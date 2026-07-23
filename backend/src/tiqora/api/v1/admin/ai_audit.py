"""Admin API for the LLM-Request-Audit — ``/api/v1/admin/ai/audit/*``.

Separate module from ``tiqora.api.v1.admin.ai`` (which already owns
``/admin/ai/*`` for providers/policies/MCP/settings) purely to keep this
Phase's diff self-contained; both routers share the ``/ai`` prefix space and
are mounted independently in ``tiqora.api.v1.admin``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from tiqora.ai import audit as ai_audit
from tiqora.ai.models import TiqoraAiAuditLog
from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import Page
from tiqora.config import get_settings
from tiqora.db.legacy.ticket import Ticket

router = APIRouter(prefix="/ai/audit", tags=["admin:ai-audit"])

AuditStatus = Literal["ok", "error"]


class AuditLogListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    run_id: str | None
    provider_id: int | None
    provider_name: str
    model: str
    feature: str
    ticket_id: int | None
    queue_id: int | None
    acting_user_id: int | None
    trigger: str | None
    status_code: int | None
    error: str | None
    duration_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    pii_counts: dict[str, int] | None = None


def _pii_counts_out(row: TiqoraAiAuditLog) -> dict[str, int] | None:
    return json.loads(row.pii_counts_json) if row.pii_counts_json else None


class AuditLogDetailOut(AuditLogListItemOut):
    request_json: str
    response_json: str | None


class AuditLogPageOut(Page[AuditLogListItemOut]):
    pass


class AuditLogStatsOut(BaseModel):
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    error_rate: float
    per_day: list[dict[str, object]]
    top_model: str | None


class PiiRevealOut(BaseModel):
    mapping: dict[str, str]


async def _resolve_ticket_filter(session: DbSession, ticket_query: str | None) -> int | None:
    """``ticket`` filter accepts either the human-facing ticket number
    (``Ticket.tn``) or the internal numeric id — the ticket number is tried
    first since that is what agents actually search by."""
    if not ticket_query:
        return None
    query = ticket_query.strip()
    if not query:
        return None
    tn_id = (
        await session.execute(select(Ticket.id).where(Ticket.tn == query))
    ).scalar_one_or_none()
    if tn_id is not None:
        return int(tn_id)
    if query.isdigit():
        return int(query)
    return None


@router.get("", response_model=AuditLogPageOut)
async def list_ai_audit_log(
    admin: AdminUser,
    session: DbSession,
    ts_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    ts_to: datetime | None = Query(None, alias="to"),  # noqa: B008
    provider_id: int | None = None,
    feature: str | None = None,
    ticket: str | None = None,
    status_filter: AuditStatus | None = Query(None, alias="status"),  # noqa: B008
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> AuditLogPageOut:
    _ = admin
    ticket_id = await _resolve_ticket_filter(session, ticket)
    if ticket and ticket_id is None:
        return AuditLogPageOut(items=[], total=0, page=page, page_size=page_size)
    result = await ai_audit.list_audit_log(
        session,
        ts_from=ts_from,
        ts_to=ts_to,
        provider_id=provider_id,
        feature=feature,
        ticket_id=ticket_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    items = []
    for r in result.items:
        item = AuditLogListItemOut.model_validate(r)
        item.pii_counts = _pii_counts_out(r)
        items.append(item)
    return AuditLogPageOut(items=items, total=result.total, page=page, page_size=page_size)


@router.get("/stats", response_model=AuditLogStatsOut)
async def ai_audit_log_stats(
    admin: AdminUser,
    session: DbSession,
    ts_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    ts_to: datetime | None = Query(None, alias="to"),  # noqa: B008
    provider_id: int | None = None,
    feature: str | None = None,
    ticket: str | None = None,
) -> AuditLogStatsOut:
    _ = admin
    ticket_id = await _resolve_ticket_filter(session, ticket)
    if ticket and ticket_id is None:
        return AuditLogStatsOut(
            total_requests=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            error_rate=0.0,
            per_day=[],
            top_model=None,
        )
    stats = await ai_audit.audit_log_stats(
        session,
        ts_from=ts_from,
        ts_to=ts_to,
        provider_id=provider_id,
        feature=feature,
        ticket_id=ticket_id,
    )
    return AuditLogStatsOut(
        total_requests=stats.total_requests,
        total_prompt_tokens=stats.total_prompt_tokens,
        total_completion_tokens=stats.total_completion_tokens,
        error_rate=stats.error_rate,
        per_day=stats.per_day,
        top_model=stats.top_model,
    )


@router.get("/{entry_id}", response_model=AuditLogDetailOut)
async def get_ai_audit_log_entry(
    entry_id: int, admin: AdminUser, session: DbSession
) -> AuditLogDetailOut:
    _ = admin
    row = await ai_audit.get_audit_log_entry(session, entry_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit entry not found")
    data = AuditLogDetailOut.model_validate(row)
    data.pii_counts = _pii_counts_out(row)
    return data


@router.post("/{entry_id}/reveal-pii", response_model=PiiRevealOut)
async def reveal_ai_audit_pii(entry_id: int, admin: AdminUser, session: DbSession) -> PiiRevealOut:
    row = await ai_audit.get_audit_log_entry(session, entry_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit entry not found")
    try:
        mapping = await ai_audit.reveal_pii(
            session, row, settings=get_settings(), admin_user_id=admin.id
        )
    except ai_audit.PiiRevealError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PiiRevealOut(mapping=mapping)


__all__ = ["router"]
