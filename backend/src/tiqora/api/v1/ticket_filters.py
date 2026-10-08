"""Ticket filter API: field catalog and permission-scoped counts."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.filters import Filter, FilterError
from tiqora.filters.tickets import count_tickets, ticket_filter_fields, ticket_query_scope
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/ticket-filters", tags=["tickets"])


class FilterFieldOut(BaseModel):
    key: str
    type: Literal["id", "string", "int", "datetime", "bool", "enum"]
    ops: list[str]
    label_key: str | None
    label: str | None
    enum_values: list[str]
    options_source: str | None


class TicketFilterCountIn(BaseModel):
    filter: Filter
    include_archived: bool = False  # honoured for admins only (like GET /tickets)


class TicketFilterCountOut(BaseModel):
    count: int


@router.get("/fields", response_model=list[FilterFieldOut])
async def list_filter_fields(user: CurrentUser, session: DbSession) -> list[FilterFieldOut]:
    return [
        FilterFieldOut(
            key=f.key,
            type=f.type,
            ops=sorted(f.allowed_ops()),
            label_key=f.label_key,
            label=f.label,
            enum_values=list(f.enum_values),
            options_source=f.options_source,
        )
        for f in await ticket_filter_fields(session)
    ]


@router.post("/count", response_model=TicketFilterCountOut)
async def count_filtered_tickets(
    body: TicketFilterCountIn, user: CurrentUser, session: DbSession
) -> TicketFilterCountOut:
    include_archived = body.include_archived
    if include_archived and not await PermissionEngine(session).is_admin(user.id):
        include_archived = False
    scope = await ticket_query_scope(session, user.id, include_archived=include_archived)
    try:
        count = await count_tickets(session, body.filter, scope)
    except FilterError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": exc.message, "path": exc.path},
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "Der Filter ist ungültig.", "path": "where"},
        ) from exc
    return TicketFilterCountOut(count=count)
