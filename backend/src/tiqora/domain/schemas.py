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
    # Znuny user_preferences.UserEmail when set; used for Gravatar.
    email: str | None = None
    # Best-effort OIDC/Google ``picture`` claim captured at SSO login.
    avatar_url: str | None = None
    # True when the agent has ``rw`` on the group named ``admin``
    # (see ``PermissionEngine.is_admin``). Used by the agent UI to show
    # admin navigation without probing an admin endpoint.
    is_admin: bool = False


class LoginRequest(BaseModel):
    login: str
    password: str


class AuthMethodsOut(BaseModel):
    password: bool = True
    oidc: bool = False
    spnego: bool = False
    ldap: bool = False
    # True only when TIQORA_WEBAUTHN_RP_ID and TIQORA_WEBAUTHN_ORIGIN are set.
    webauthn: bool = False


class TOTPEnrollOut(BaseModel):
    secret: str
    otpauth_uri: str


class TOTPCodeIn(BaseModel):
    code: str


class TOTPStatusOut(BaseModel):
    enabled: bool


class PasskeyRegisterFinishIn(BaseModel):
    """Browser ``PublicKeyCredential`` JSON from ``navigator.credentials.create``."""

    credential: dict[str, Any]
    name: str | None = None


class PasskeyAuthenticateFinishIn(BaseModel):
    """Browser ``PublicKeyCredential`` JSON from ``navigator.credentials.get``."""

    credential: dict[str, Any]


class PasskeyOut(BaseModel):
    id: int
    name: str
    created: datetime
    last_used_at: datetime | None = None


class PasskeyStatusOut(BaseModel):
    """Returned after register/finish (and optionally after delete)."""

    id: int
    name: str
    enabled: bool = True


class LoginResponse(BaseModel):
    user: UserMe | None = None
    pending_2fa: bool = False
    # Password login only: agent must complete TOTP enrollment before a full
    # session is issued (per-agent enforce_2fa or global auth.totp.enforce_all).
    must_enroll_2fa: bool = False


class QueueCounts(BaseModel):
    open: int = 0
    """Viewable total: state type in {new, open, pending reminder, pending auto}."""
    new: int = 0
    """Subset of ``open`` — tickets currently in the ``new`` state type."""
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


class TicketPermissions(BaseModel):
    """Effective Znuny permission keys for the ticket's queue group.

    Each flag is true when the agent holds that key **or** ``rw`` (which
    implies every key). Used by the agent UI to disable per-action controls.
    """

    ro: bool = False
    move_into: bool = False
    create: bool = False
    note: bool = False
    owner: bool = False
    priority: bool = False
    rw: bool = False


class TicketDetail(TicketListItem):
    type_id: int | None = None
    service_id: int | None = None
    sla_id: int | None = None
    responsible_user_id: int | None = None
    archive_flag: int = 0
    create_by: int | None = None
    change_by: int | None = None
    dynamic_fields: list[DynamicFieldValueOut] = Field(default_factory=list)
    is_watched: bool = False
    can_write: bool = False
    permissions: TicketPermissions = Field(default_factory=TicketPermissions)


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
    rendered: str
    history_type_id: int
    history_type: str | None = None
    article_id: int | None = None
    owner_id: int
    create_time: datetime
    create_by: int
    create_by_login: str | None = None


class ReplyDraftOut(BaseModel):
    """Prefilled reply/reply-all draft for one article (see quoting.py)."""

    to_address: str | None = None
    cc: str | None = None
    subject: str
    body: str
    is_html: bool
    in_reply_to: str | None = None
    references: str | None = None
    # Queue signature expanded for read-only composer preview (not part of body;
    # the send pipeline appends it via prepare_outgoing_agent_email).
    signature: str = ""
    signature_is_html: bool = False


class TemplateOut(BaseModel):
    id: int
    name: str
    text: str
    content_type: str | None = None
    template_type: str | None = None


class TicketLinkTargetOut(BaseModel):
    source_key: str
    target_key: str
    link_type: str
    state: str
    other_ticket_id: int
    other_tn: str | None = None
    other_title: str | None = None


class TicketLinkCreateRequest(BaseModel):
    target_ticket_id: int
    link_type: str = "Normal"


class ForwardRequest(BaseModel):
    to_address: str
    cc: str | None = None
    subject: str | None = None
    body: str
    note: str | None = None


class BounceRequest(BaseModel):
    to_address: str
    note: str | None = None
    state_id: int | None = None


class SplitRequest(BaseModel):
    queue_id: int
    title: str | None = None


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
