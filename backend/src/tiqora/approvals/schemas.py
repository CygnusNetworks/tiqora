"""Pydantic API models of the approval engine (A13): agent routes, later MCP and admin.

Pure data shapes, no database. Names are display labels ("Vorname Nachname", customers with
" (Kunde)"); the audit ``detail`` carries ids, keys and statuses only.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tiqora.approvals.contract import ApprovalDefinition
from tiqora.domain.schemas import UtcDateTime

ApprovalStatusName = Literal["pending", "approved", "rejected", "cancelled"]
DecisionName = Literal["approved", "rejected"]
ChannelName = Literal["ui", "email", "portal", "api", "mcp", "chat", "auto"]


class VoteOut(BaseModel):
    decision: str
    comment: str | None = None
    decided_by_label: str
    on_behalf: bool
    channel: str
    decided_at: UtcDateTime


class ApproverOut(BaseModel):
    id: int
    kind: Literal["agent", "customer"]
    display_name: str
    source_kind: str
    source_label: str | None = None
    decisive: bool
    status: str
    vote: VoteOut | None = None


class ApprovalStepOut(BaseModel):
    id: int
    position: int
    stage: int
    name: str
    rule: str
    quorum: int | None = None
    #: approvals the rule needs ("2 von 3"); None while it cannot be told
    required: int | None = None
    approved_count: int
    status: str
    reason: str | None = None
    due_at: UtcDateTime | None = None
    overdue: bool
    approvers: list[ApproverOut]


class ApprovalEventOut(BaseModel):
    at: UtcDateTime
    action: str
    actor_label: str
    channel: str | None = None
    detail: dict[str, Any] | None = None


class MyApproverRowOut(BaseModel):
    approver_id: int
    step_id: int
    #: set when the row is decided in place of a delegator
    on_behalf_of_label: str | None = None
    can_forward: bool


class ApprovalSummaryOut(BaseModel):
    id: int
    ticket_id: int
    subject_type: str
    subject_ref: str | None = None
    approval_key: str
    title: str
    status: str
    reason: str | None = None
    current_stage: int | None = None
    requester_label: str
    created_at: UtcDateTime
    decided_at: UtcDateTime | None = None
    #: the caller can still vote on an open row of this request
    my_pending: bool
    can_cancel: bool


class ApprovalDetailOut(BaseModel):
    id: int
    ticket_id: int
    ticket_number: str | None = None
    ticket_title: str | None = None
    #: False when the caller may see the request but not read the ticket
    ticket_link: bool
    subject_type: str
    subject_ref: str | None = None
    approval_key: str
    title: str
    description: str | None = None
    describe_lines: list[str]
    status: str
    reason: str | None = None
    current_stage: int | None = None
    requester_label: str
    created_at: UtcDateTime
    decided_at: UtcDateTime | None = None
    steps: list[ApprovalStepOut]
    events: list[ApprovalEventOut]
    my_rows: list[MyApproverRowOut]
    can_cancel: bool
    can_refresh: bool


class InboxItemOut(BaseModel):
    approval_id: int
    approver_id: int
    ticket_id: int
    ticket_number: str | None = None
    ticket_title: str | None = None
    title: str
    subject_type: str
    step_name: str
    rule: str
    required: int | None = None
    approved_count: int
    due_at: UtcDateTime | None = None
    overdue: bool
    on_behalf_of_label: str | None = None
    status: str
    decided_at: UtcDateTime | None = None
    my_decision: str | None = None
    can_vote: bool
    #: "already_voted" | "own_request" when the row is open but the caller cannot vote on it
    blocked_reason: str | None = None


class ApprovalInboxPage(BaseModel):
    items: list[InboxItemOut]
    total: int
    limit: int
    offset: int


class InboxCountOut(BaseModel):
    pending: int


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionName
    #: at most 2000 characters (the service answers ``comment_too_long``)
    comment: str | None = None
    #: decide on a specific row (own row or one of a delegator); default = the caller's row
    approver_id: int | None = Field(None, ge=1)


class ForwardIn(BaseModel):
    """Forwarding takes no comment: audit rows never carry free text."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(ge=1)


class ApprovalRequestIn(BaseModel):
    """Ad-hoc approval on a ticket (subject ``ticket``): a template or a definition."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=250)
    description: str | None = Field(None, max_length=20000)
    approval_key: str = Field("default", min_length=1, max_length=100)
    template_key: str | None = Field(None, max_length=100)
    definition: ApprovalDefinition | None = None


class ApprovalTemplateOptionOut(BaseModel):
    key: str
    name: str
    description: str | None = None
    subject_types: list[str]


class ApprovalListOptionOut(BaseModel):
    id: int
    name: str
    member_count: int


class ResolverOptionOut(BaseModel):
    key: str
    title_key: str
    yields_customers: bool


class DelegationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substitute_user_id: int = Field(ge=1)
    #: default = now
    valid_from: UtcDateTime | None = None
    valid_until: UtcDateTime | None = None


class DelegationOut(BaseModel):
    id: int
    user_id: int
    user_label: str
    substitute_user_id: int
    substitute_label: str
    valid_from: UtcDateTime
    valid_until: UtcDateTime | None = None
    created_at: UtcDateTime


class LinkInspectIn(BaseModel):
    """The link's secret travels in the body, never in a URL (no access-log or referrer leak)."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)


class LinkInspectOut(BaseModel):
    """What the confirm page shows. Only the link holder's own view: no requester, no other
    approvers, no comments (F25). ``code``: ``link_used`` (already used), ``link_expired`` (live
    link past its expiry) or ``link_invalid`` (unknown, revoked, closed or not yours any more:
    deliberately one answer)."""

    valid: bool
    code: str | None = None
    approver_name: str | None = None
    title: str | None = None
    description: str | None = None
    ticket_number: str | None = None
    ticket_title: str | None = None
    step_name: str | None = None
    rule: str | None = None
    due_at: UtcDateTime | None = None
    describe_lines: list[str] = Field(default_factory=list)


class LinkDecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)
    decision: DecisionName
    comment: str | None = Field(None, max_length=2000)  # the engine's limit


class LinkDecisionOut(BaseModel):
    decision: DecisionName
    #: the request's status after the vote ("pending" while the step or stage is still open)
    status: str


# --- customer portal (A13 portal rows) -----------------------------------------------------------


class PortalDecisionIn(BaseModel):
    """A customer approver's vote. No ``approver_id``: a customer decides only their own row."""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionName
    comment: str | None = Field(None, max_length=2000)  # the engine's limit, as on the link


class PortalApprovalItemOut(BaseModel):
    id: int
    title: str
    ticket_number: str | None = None
    ticket_title: str | None = None
    status: str
    step_name: str
    due_at: UtcDateTime | None = None
    my_status: str
    my_decision: str | None = None
    decided_at: UtcDateTime | None = None
    can_decide: bool


class PortalApprovalPage(BaseModel):
    items: list[PortalApprovalItemOut]
    total: int
    limit: int
    offset: int


class PortalApprovalOut(BaseModel):
    """One request as its customer approver sees it: no other approvers, no other comments."""

    id: int
    title: str
    description: str | None = None
    describe_lines: list[str] = Field(default_factory=list)
    ticket_number: str | None = None
    ticket_title: str | None = None
    status: str
    step_name: str
    #: i18n key of the step's rule sentence ("approvals.rule.any" ...)
    rule_text_key: str
    required: int | None = None
    approved_count: int
    due_at: UtcDateTime | None = None
    #: status of the caller's own approver row (pending, approved, rejected, forwarded, ...)
    my_status: str
    my_decision: str | None = None
    can_decide: bool


# --- admin (A13 admin rows) ------------------------------------------------------------


class ApprovalAdminItemOut(BaseModel):
    """One row of the admin overview: any request, whoever may see it."""

    id: int
    ticket_id: int
    ticket_number: str | None = None
    ticket_title: str | None = None
    subject_type: str
    subject_ref: str | None = None
    approval_key: str
    title: str
    status: str
    reason: str | None = None
    current_stage: int | None = None
    #: name of the first active step (None once the request is closed)
    step_name: str | None = None
    #: approver rows still waiting for a vote
    open_approvers: int
    #: an active step is past its deadline
    overdue: bool
    requester_label: str
    created_at: UtcDateTime
    decided_at: UtcDateTime | None = None
    can_cancel: bool


class ApprovalAdminPage(BaseModel):
    items: list[ApprovalAdminItemOut]
    total: int
    limit: int
    offset: int


class ApprovalTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=20000)
    #: empty = every subject type
    subject_types: list[str] = Field(default_factory=list, max_length=50)
    definition: ApprovalDefinition
    valid: bool = True


class ApprovalTemplateOut(BaseModel):
    id: int
    template_key: str
    name: str
    description: str | None = None
    subject_types: list[str]
    definition: ApprovalDefinition
    origin: str
    valid: bool
    #: approvers the definition names that no longer exist (they do not block saving)
    warnings: list[str]


class DefinitionCheckIn(BaseModel):
    """The definition is a plain object here: a broken one is answered in ``errors``, not 422."""

    model_config = ConfigDict(extra="forbid")

    definition: dict[str, Any]
    subject_type: str | None = Field(None, max_length=64)


class DefinitionCheckOut(BaseModel):
    errors: list[str]
    warnings: list[str]


class ApprovalListIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=190)
    description: str | None = Field(None, max_length=20000)
    member_ids: list[int] = Field(default_factory=list, max_length=500)
    valid: bool = True


class ApprovalListMemberOut(BaseModel):
    id: int
    label: str


class ApprovalListOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    valid: bool
    member_ids: list[int]
    members: list[ApprovalListMemberOut]


class AdminDelegationIn(DelegationIn):
    """An admin names the delegator as well (``user_id`` = whose decisions are delegated)."""

    user_id: int = Field(ge=1)
