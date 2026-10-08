"""Bridge from today's ticket-list query parameters to a filter.

``GET /api/v1/tickets`` is not rewritten onto the compiler in this wave; the
parity test (``tests/test_filters_ticket_params.py``) pins that this bridge
selects exactly what ``TicketService._filtered_ticket_stmt`` selects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tiqora.domain.ticket_service import VIEW_STATE_TYPES
from tiqora.filters.model import AllOf, Condition, Filter, FilterNode


class TicketListParams(BaseModel):
    queue_id: int | None = None
    state_id: int | None = None
    state_type: str | None = None  # incl. view names from ticket_service.VIEW_STATE_TYPES
    owner_id: int | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    responsible_id: int | None = None
    service_id: int | None = None
    locked: bool | None = None
    watcher_user_id: int | None = None
    escalated: bool | None = None  # only True filters (route semantics)
    ai_escalated: bool | None = None  # only True filters
    unassigned: bool | None = None
    escalating_within: int | None = Field(None, ge=0)
    channel: list[Literal["email", "telegram", "webchat", "phone"]] | None = None
    include_archived: bool = False  # goes into the scope, not into the filter


def filter_from_list_params(params: TicketListParams) -> Filter:
    """One condition per set parameter, ANDed. No parameter -> no ``where``."""
    p = params
    nodes: list[FilterNode] = []

    def add(field: str, op: str, value: object = None) -> None:
        nodes.append(Condition(field=field, op=op, value=value))  # type: ignore[arg-type]

    for field, value in (
        ("queue_id", p.queue_id),
        ("state_id", p.state_id),
        ("owner_id", p.owner_id),
        ("customer_id", p.customer_id),
        ("customer_user_id", p.customer_user_id),
        ("responsible_id", p.responsible_id),
        ("service_id", p.service_id),
    ):
        if value is not None:
            add(field, "eq", value)
    if p.state_type is not None:
        view = VIEW_STATE_TYPES.get(p.state_type)
        if view is not None:
            add("state_type", "in", sorted(view))
        else:
            add("state_type", "eq", p.state_type)
    if p.locked is not None:
        add("locked", "is_true" if p.locked else "is_false")
    if p.watcher_user_id is not None:
        add("watched_by", "eq", p.watcher_user_id)
    if p.unassigned is not None:
        add("unassigned", "is_true" if p.unassigned else "is_false")
    if p.escalated:
        add("escalated", "is_true")
    if p.ai_escalated:
        add("ai_escalated", "is_true")
    if p.escalating_within is not None:
        add("escalation_due_within", "lte", p.escalating_within)
    if p.channel:
        add("channel", "in", list(p.channel))
    return Filter(where=AllOf(all=nodes) if nodes else None)
