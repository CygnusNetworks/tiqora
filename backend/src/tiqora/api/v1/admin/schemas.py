"""Pydantic v2 schemas for the admin CRUD API (`api/v1/admin/`).

Kept separate from :mod:`tiqora.domain.schemas` (the existing read-path
convention) because these are write-capable request/response models scoped
to the admin surface only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
    create_time: datetime
    change_time: datetime


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
# Read-only: postmaster filter / ACL / generic agent jobs
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
    create_time: datetime
    change_time: datetime


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
