"""Pydantic v2 schemas for the admin CRUD API (`api/v1/admin/`).

Kept separate from :mod:`tiqora.domain.schemas` (the existing read-path
convention) because these are write-capable request/response models scoped
to the admin surface only.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    title: str | None
    first_name: str
    last_name: str
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class UserCreate(BaseModel):
    login: str
    password: str
    title: str | None = None
    first_name: str
    last_name: str
    valid_id: int = 1


class UserUpdate(BaseModel):
    login: str | None = None
    password: str | None = None
    title: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    valid_id: int | None = None


class GroupAssignment(BaseModel):
    group_id: int
    permission_key: Literal["ro", "move_into", "create", "note", "owner", "priority", "rw"]


class RoleAssignment(BaseModel):
    role_id: int


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class GroupCreate(BaseModel):
    name: str
    comments: str | None = None
    valid_id: int = 1


class GroupUpdate(BaseModel):
    name: str | None = None
    comments: str | None = None
    valid_id: int | None = None


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class RoleCreate(BaseModel):
    name: str
    comments: str | None = None
    valid_id: int = 1


class RoleUpdate(BaseModel):
    name: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class GroupRoleAssignment(BaseModel):
    group_id: int
    permission_key: Literal["ro", "move_into", "create", "note", "owner", "priority", "rw"]
    permission_value: int = 1


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


class QueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    group_id: int
    unlock_timeout: int | None
    first_response_time: int | None
    first_response_notify: int | None
    update_time: int | None
    update_notify: int | None
    solution_time: int | None
    solution_notify: int | None
    system_address_id: int
    calendar_name: str | None
    default_sign_key: str | None
    salutation_id: int
    signature_id: int
    follow_up_id: int
    follow_up_lock: int
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class QueueCreate(BaseModel):
    name: str
    group_id: int
    system_address_id: int
    salutation_id: int
    signature_id: int
    follow_up_id: int
    follow_up_lock: int = 0
    unlock_timeout: int | None = None
    first_response_time: int | None = None
    first_response_notify: int | None = None
    update_time: int | None = None
    update_notify: int | None = None
    solution_time: int | None = None
    solution_notify: int | None = None
    calendar_name: str | None = None
    default_sign_key: str | None = None
    comments: str | None = None
    valid_id: int = 1


class QueueUpdate(BaseModel):
    name: str | None = None
    group_id: int | None = None
    system_address_id: int | None = None
    salutation_id: int | None = None
    signature_id: int | None = None
    follow_up_id: int | None = None
    follow_up_lock: int | None = None
    unlock_timeout: int | None = None
    first_response_time: int | None = None
    first_response_notify: int | None = None
    update_time: int | None = None
    update_notify: int | None = None
    solution_time: int | None = None
    solution_notify: int | None = None
    calendar_name: str | None = None
    default_sign_key: str | None = None
    comments: str | None = None
    valid_id: int | None = None


# ---------------------------------------------------------------------------
# States / priorities
# ---------------------------------------------------------------------------


class StateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    comments: str | None
    type_id: int
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class StateTypeOut(BaseModel):
    """Reference row for resolving ``ticket_state.type_id`` to a name."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class SystemAddressOut(BaseModel):
    """Reference row for resolving ``queue.system_address_id`` to a label.

    Znuny ``system_address``: ``value0`` is the email, ``value1`` the real name.
    UI display is typically ``"{value1} <{value0}>"``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    value0: str
    value1: str
    comments: str | None = None
    valid_id: int


class FollowUpPossibleOut(BaseModel):
    """Reference row for resolving ``queue.follow_up_id`` to a name.

    Stock Znuny names: ``possible``, ``reject``, ``new ticket``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    comments: str | None = None
    valid_id: int


class StateCreate(BaseModel):
    name: str
    type_id: int
    comments: str | None = None
    valid_id: int = 1


class StateUpdate(BaseModel):
    name: str | None = None
    type_id: int | None = None
    comments: str | None = None
    valid_id: int | None = None


class PriorityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class PriorityCreate(BaseModel):
    name: str
    valid_id: int = 1


class PriorityUpdate(BaseModel):
    name: str | None = None
    valid_id: int | None = None


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class CustomerUserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    login: str
    email: str
    customer_id: str
    title: str | None
    first_name: str
    last_name: str
    phone: str | None
    fax: str | None
    mobile: str | None
    street: str | None
    zip: str | None
    city: str | None
    country: str | None
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class CustomerUserAdminCreate(BaseModel):
    login: str
    email: str
    customer_id: str
    password: str | None = None
    title: str | None = None
    first_name: str
    last_name: str
    phone: str | None = None
    fax: str | None = None
    mobile: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    comments: str | None = None
    valid_id: int = 1


class CustomerUserAdminUpdate(BaseModel):
    login: str | None = None
    email: str | None = None
    customer_id: str | None = None
    password: str | None = None
    title: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    fax: str | None = None
    mobile: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class CustomerUserBulkUpdate(BaseModel):
    """Bulk-patch a set of customer_user rows (validity and/or company)."""

    ids: list[int] = Field(..., min_length=1, max_length=1000)
    valid_id: int | None = None
    customer_id: str | None = None


class CustomerUserBulkUpdateResult(BaseModel):
    updated: int


class CustomerCompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: str
    name: str
    street: str | None
    zip: str | None
    city: str | None
    country: str | None
    url: str | None
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class CustomerCompanyCreate(BaseModel):
    customer_id: str
    name: str
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    url: str | None = None
    comments: str | None = None
    valid_id: int = 1


class CustomerCompanyUpdate(BaseModel):
    name: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    url: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class CustomerUserCustomerAssignment(BaseModel):
    customer_id: str


# ---------------------------------------------------------------------------
# Salutations / signatures / templates
# ---------------------------------------------------------------------------


class SalutationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    text: str
    content_type: str | None
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class SalutationWrite(BaseModel):
    name: str
    text: str
    content_type: str | None = None
    comments: str | None = None
    valid_id: int = 1


class SalutationUpdate(BaseModel):
    name: str | None = None
    text: str | None = None
    content_type: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class SignatureOut(SalutationOut):
    pass


class SignatureWrite(SalutationWrite):
    pass


class SignatureUpdate(SalutationUpdate):
    pass


class StandardTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    text: str | None
    content_type: str | None
    template_type: str
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None
    #: How many queues this template is assigned to (list responses only).
    assigned_queue_count: int = 0


class StandardTemplateCreate(BaseModel):
    name: str
    text: str | None = None
    content_type: str | None = None
    template_type: str = "Answer"
    comments: str | None = None
    valid_id: int = 1


class StandardTemplateUpdate(BaseModel):
    name: str | None = None
    text: str | None = None
    content_type: str | None = None
    template_type: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class QueueTemplateAssignment(BaseModel):
    standard_template_id: int


# ---------------------------------------------------------------------------
# Standard attachments (+ template assignment)
# ---------------------------------------------------------------------------


class StandardAttachmentOut(BaseModel):
    """Attachment master row; ``content`` is base64-encoded binary."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    content_type: str
    content: str
    filename: str
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None
    #: How many templates link this attachment (list responses only).
    assigned_template_count: int = 0

    @field_validator("content", mode="before")
    @classmethod
    def _encode_content_b64(cls, value: object) -> str:
        """Normalise ORM blob → base64 for the wire format.

        Drivers differ: MariaDB/aiomysql returns ``bytes``; asyncpg sometimes
        surfaces BYTEA as a PostgreSQL hex literal (``\\xdeadbeef``) string
        after refresh. Accept both, plus already-encoded base64 for
        round-trips through the same schema.
        """
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, bytearray):
            value = bytes(value)
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        if isinstance(value, str):
            if value.startswith("\\x"):
                return base64.b64encode(bytes.fromhex(value[2:])).decode("ascii")
            return value
        raise TypeError("content must be bytes or a base64 string")


class StandardAttachmentCreate(BaseModel):
    name: str
    content_type: str
    content: str = Field(description="Base64-encoded attachment body")
    filename: str
    comments: str | None = None
    valid_id: int = 1

    def content_bytes(self) -> bytes:
        return base64.b64decode(self.content, validate=True)


class StandardAttachmentUpdate(BaseModel):
    name: str | None = None
    content_type: str | None = None
    content: str | None = Field(default=None, description="Base64-encoded attachment body")
    filename: str | None = None
    comments: str | None = None
    valid_id: int | None = None

    def content_bytes(self) -> bytes | None:
        if self.content is None:
            return None
        return base64.b64decode(self.content, validate=True)


class AttachmentRefOut(BaseModel):
    """Slim attachment row for assignment editors (no blob)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    filename: str
    content_type: str


class TemplateAttachmentsReplace(BaseModel):
    """Full replacement set of attachments for a standard template."""

    attachment_ids: list[int]


class CustomerUserGroupAssignment(BaseModel):
    """Assign a customer-user (by login) to a group with ro/rw permission.

    Znuny ``group_customer_user`` stores one row per (login, group, key) with
    ``permission_value`` (unlike agent ``group_user``, which has no value
    column). Customer-user group grants are only ``ro`` / ``rw``.
    """

    group_id: int
    permission_key: Literal["ro", "rw"]
    permission_value: int = 1


# ---------------------------------------------------------------------------
# Auto responses
# ---------------------------------------------------------------------------


class AutoResponseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    text0: str | None
    text1: str | None
    type_id: int
    system_address_id: int
    content_type: str | None
    comments: str | None
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None
    #: How many queues this auto-response is assigned to (list responses only).
    assigned_queue_count: int = 0


class AutoResponseCreate(BaseModel):
    name: str
    text0: str | None = None
    text1: str | None = None
    type_id: int
    system_address_id: int
    content_type: str | None = None
    comments: str | None = None
    valid_id: int = 1


class AutoResponseUpdate(BaseModel):
    name: str | None = None
    text0: str | None = None
    text1: str | None = None
    type_id: int | None = None
    system_address_id: int | None = None
    content_type: str | None = None
    comments: str | None = None
    valid_id: int | None = None


class QueueAutoResponseAssignment(BaseModel):
    auto_response_id: int


# ---------------------------------------------------------------------------
# Dynamic fields
# ---------------------------------------------------------------------------

DYNAMIC_FIELD_TYPES = {
    "Text",
    "TextArea",
    "Checkbox",
    "Dropdown",
    "Multiselect",
    "Date",
    "DateTime",
}


class DynamicFieldOut(BaseModel):
    id: int
    internal_field: int
    name: str
    label: str
    field_order: int
    field_type: str
    object_type: str
    config: dict[str, Any]
    valid_id: int
    create_time: datetime | None
    change_time: datetime | None


class DynamicFieldCreate(BaseModel):
    name: str
    label: str
    field_order: int
    field_type: str
    object_type: Literal["Ticket", "Article", "CustomerUser", "CustomerCompany"] = "Ticket"
    config: dict[str, Any] = Field(default_factory=dict)
    valid_id: int = 1


class DynamicFieldUpdate(BaseModel):
    label: str | None = None
    field_order: int | None = None
    config: dict[str, Any] | None = None
    valid_id: int | None = None


# ---------------------------------------------------------------------------
# Postmaster filters (editable) + read-only ACL / generic agent jobs
# ---------------------------------------------------------------------------


class PostmasterFilterRuleOut(BaseModel):
    f_name: str
    f_stop: int | None
    f_type: str
    f_key: str
    f_value: str
    f_not: int | None


class PostmasterFilterOut(BaseModel):
    """Grouped by ``f_name`` (Znuny's filter identity is the name, not a single row)."""

    name: str
    rules: list[PostmasterFilterRuleOut]


class PostmasterMatchRuleIn(BaseModel):
    """One Match condition (email header + regex/value, optional negate)."""

    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=200)
    negate: bool = False


class PostmasterSetRuleIn(BaseModel):
    """One Set action (typically an X-OTRS-* header + value)."""

    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=200)


class PostmasterFilterWrite(BaseModel):
    """Create/replace body for a named PostMaster filter.

    Znuny stores one ``postmaster_filter`` row per Match/Set entry under the
    same ``f_name``; ``stop`` maps to ``f_stop`` on every row for that name.
    """

    name: str = Field(min_length=1, max_length=200)
    stop: bool = False
    match: list[PostmasterMatchRuleIn] = Field(min_length=1)
    set: list[PostmasterSetRuleIn] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("name must not be empty")
        return name

    @field_validator("match")
    @classmethod
    def _require_match(cls, v: list[PostmasterMatchRuleIn]) -> list[PostmasterMatchRuleIn]:
        if not v:
            raise ValueError("at least one Match rule is required")
        return v


class AclOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    comments: str | None
    description: str | None
    valid_id: int
    stop_after_match: int | None
    config_match: str | None
    config_change: str | None
    create_time: datetime | None
    change_time: datetime | None


class GenericAgentJobOut(BaseModel):
    """Grouped by ``job_name`` (key/value rows per Znuny ``generic_agent_jobs``)."""

    job_name: str
    settings: dict[str, str | None]


# ---------------------------------------------------------------------------
# Webhooks (Phase 3c) — tiqora_webhook, event-outbox-driven fan-out
# ---------------------------------------------------------------------------


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    events: list[str]
    valid: bool
    created: datetime
    changed: datetime


class WebhookCreate(BaseModel):
    name: str
    url: str
    secret: str
    events: list[str] = Field(default_factory=list)
    valid: bool = True


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    valid: bool | None = None


# ---------------------------------------------------------------------------
# API keys — tiqora_api_key (Bearer auth)
# ---------------------------------------------------------------------------


class ApiKeyOut(BaseModel):
    """Public API-key metadata. Never includes key_hash or plaintext."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    user_id: int
    valid: bool
    created: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    created_by: int | None


class ApiKeyCreate(BaseModel):
    name: str
    user_id: int
    expires_at: datetime | None = None


class ApiKeyUpdate(BaseModel):
    name: str | None = None
    valid: bool | None = None
    expires_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    """Create response: includes the plaintext ``key`` exactly once.

    The plaintext is never stored and cannot be retrieved again — copy it
    immediately after creation.
    """

    key: str


# ---------------------------------------------------------------------------
# Subject-hook config — tiqora_settings overrides over live Znuny SysConfig
# ---------------------------------------------------------------------------


class SubjectHookZnunyOut(BaseModel):
    """Underlying Znuny SysConfig values (never written by Tiqora)."""

    hook: str
    divider: str
    subject_format: str


class SubjectHookOverridesOut(BaseModel):
    """Raw Tiqora overrides; ``None`` means the field is not overridden."""

    enabled: bool | None = None
    hook: str | None = None
    divider: str | None = None
    subject_format: str | None = None


class SubjectConfigOut(BaseModel):
    """Effective subject-hook config plus Znuny baseline and raw overrides."""

    enabled: bool
    hook: str
    divider: str
    subject_format: str
    overrides: SubjectHookOverridesOut
    znuny: SubjectHookZnunyOut


class SubjectConfigUpdate(BaseModel):
    """Upsert Tiqora overrides. Empty string / null clears an override.

    ``enabled`` null clears the override (reverts to default True).
    ``subject_format`` must be Left, Right, or None when set.
    """

    enabled: bool | None = None
    hook: str | None = None
    divider: str | None = None
    subject_format: str | None = None


# ---------------------------------------------------------------------------
# Placeholder variables — tiqora_queue_variable / tiqora_placeholder_field
# ---------------------------------------------------------------------------


class QueueVariableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    queue_id: int | None
    name: str
    value: str | None
    created: datetime
    changed: datetime


class QueueVariableCreate(BaseModel):
    queue_id: int | None = None
    name: str
    value: str | None = None


class QueueVariableUpdate(BaseModel):
    queue_id: int | None = None
    name: str | None = None
    value: str | None = None


class PhysicalQueueVariableOut(BaseModel):
    """A non-standard column on the Znuny ``queue`` table (site-specific patch).

    Surfaced read-only so admins can see physical values that resolve as
    ``<OTRS_QUEUE_{name}>`` even when no ``tiqora_queue_variable`` row exists.
    """

    name: str
    value: str


class PlaceholderFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_table: str
    column_name: str
    tag_name: str
    label: str | None
    enabled: bool
    created: datetime
    changed: datetime


class PlaceholderFieldCreate(BaseModel):
    source_table: str
    column_name: str
    tag_name: str
    label: str | None = None
    enabled: bool = True


class PlaceholderFieldUpdate(BaseModel):
    source_table: str | None = None
    column_name: str | None = None
    tag_name: str | None = None
    label: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Auth config — per-agent SSO eligibility + 2FA enforcement
# ---------------------------------------------------------------------------


class AuthConfigAgentOut(BaseModel):
    """One agent row for the admin auth-config list."""

    user_id: int
    login: str
    full_name: str
    totp_enabled: bool
    sso_eligible: bool
    enforce_2fa: bool


class AuthConfigUpdate(BaseModel):
    sso_eligible: bool | None = None
    enforce_2fa: bool | None = None


class AuthConfigGlobalOut(BaseModel):
    enforce_all: bool
    enforce_group_ids: list[int] = []


class AuthConfigGlobalUpdate(BaseModel):
    enforce_all: bool
    # When omitted (None), the stored enforce_group_ids list is left unchanged.
    enforce_group_ids: list[int] | None = None
