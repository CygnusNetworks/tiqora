"""Pydantic v2 response models for REST v1 (read path)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    first_name: str
    last_name: str
    auth_method: str


class LoginRequest(BaseModel):
    login: str
    password: str


class AuthMethodsOut(BaseModel):
    password: bool = True
    oidc: bool = False
    spnego: bool = False
    ldap: bool = False


class TOTPEnrollOut(BaseModel):
    secret: str
    otpauth_uri: str


class TOTPCodeIn(BaseModel):
    code: str


class TOTPStatusOut(BaseModel):
    enabled: bool


class LoginResponse(BaseModel):
    user: UserMe | None = None
    pending_2fa: bool = False


class QueueCounts(BaseModel):
    open: int = 0
    locked: int = 0
    unlocked: int = 0
    total: int = 0


class QueueNode(BaseModel):
    id: int
    name: str
    group_id: int
    parent_name: str | None = None
    valid: bool = True
    counts: QueueCounts = Field(default_factory=QueueCounts)
    children: list[QueueNode] = Field(default_factory=list)


class TicketListItem(BaseModel):
    id: int
    tn: str
    title: str | None
    queue_id: int
    queue_name: str | None = None
    state_id: int
    state: str | None = None
    state_type: str | None = None
    priority_id: int
    priority: str | None = None
    lock_id: int
    lock: str | None = None
    owner_id: int
    owner_login: str | None = None
    owner_name: str | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    create_time: datetime
    change_time: datetime
    age_seconds: int | None = None
    escalation_time: int = 0
    escalation_response_time: int = 0
    escalation_update_time: int = 0
    escalation_solution_time: int = 0
    until_time: int = 0


class PaginatedTickets(BaseModel):
    items: list[TicketListItem]
    total: int
    offset: int
    limit: int


class DynamicFieldValueOut(BaseModel):
    name: str
    label: str | None = None
    field_type: str | None = None
    values: list[Any] = Field(default_factory=list)


class TicketDetail(TicketListItem):
    type_id: int | None = None
    service_id: int | None = None
    sla_id: int | None = None
    responsible_user_id: int | None = None
    archive_flag: int = 0
    create_by: int | None = None
    change_by: int | None = None
    dynamic_fields: list[DynamicFieldValueOut] = Field(default_factory=list)


class ArticleListItem(BaseModel):
    id: int
    ticket_id: int
    sender_type: str | None = None
    sender_type_id: int
    communication_channel_id: int
    is_visible_for_customer: bool
    create_time: datetime
    create_by: int
    subject: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    content_type: str | None = None
    incoming_time: int | None = None


class ArticleBody(BaseModel):
    article_id: int
    content_type: str
    is_html: bool
    body: str


class AttachmentMetaOut(BaseModel):
    id: int
    article_id: int
    filename: str | None = None
    content_type: str | None = None
    content_size: str | None = None
    content_id: str | None = None
    disposition: str | None = None


class HistoryEntry(BaseModel):
    id: int
    ticket_id: int
    name: str
    history_type_id: int
    history_type: str | None = None
    article_id: int | None = None
    owner_id: int
    create_time: datetime
    create_by: int


class CustomerUserOut(BaseModel):
    login: str
    email: str
    customer_id: str
    first_name: str
    last_name: str
    title: str | None = None
    phone: str | None = None
    company_name: str | None = None


class SearchHit(BaseModel):
    id: int
    tn: str | None = None
    title: str | None = None
    queue_id: int | None = None
    queue_name: str | None = None
    state: str | None = None
    state_type: str | None = None
    priority: str | None = None
    owner_login: str | None = None
    customer_id: str | None = None
    create_time: str | None = None
    change_time: str | None = None
    excerpt: str | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    estimated_total: int


# ---------------------------------------------------------------------------
# Customer portal (Phase 3a)
# ---------------------------------------------------------------------------


class CustomerMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    email: str
    customer_id: str
    first_name: str
    last_name: str


class CustomerLoginResponse(BaseModel):
    customer: CustomerMe


class PortalTicketCreateRequest(BaseModel):
    title: str
    body: str
    queue_id: int | None = None


class PortalTicketCreateResponse(BaseModel):
    ticket_id: int


class PortalReplyRequest(BaseModel):
    body: str
    subject: str | None = None


class PortalReplyResponse(BaseModel):
    article_id: int
    reopened: bool = False


class PortalAttachmentUploadResponse(BaseModel):
    article_id: int
    attachment_ids: list[int]
