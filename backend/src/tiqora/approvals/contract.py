"""Cross-plan contract of the approval engine.

Consumers (ITSM change, catalogue, process) import names from here and from ``service`` only.
Changes after the first consumer forked are additive only (new keyword arguments / fields with
defaults, new ``ERROR_STATUS`` codes).

Pure module: no database, network, file system or settings access. It imports only the standard
library, pydantic, ``sqlalchemy.ext.asyncio`` (type checking only) and
``tiqora.extensions.registry``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tiqora.extensions.registry import ExtensionPoint

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# users.id writes of customers are attributed to; equals
# tiqora.domain.portal_ticket_service.PORTAL_SYSTEM_USER_ID (pinned by a test; stays pure).
PORTAL_WRITE_USER_ID: Final = 1
SYSTEM_WRITE_USER_ID: Final = 1

PrincipalKind = Literal["agent", "customer"]
# There is no "expired": a timeout ends "rejected"/"approved" with reason="timeout".
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled"]
StepStatus = Literal["waiting", "active", "approved", "rejected", "cancelled"]
Rule = Literal["any", "all", "quorum", "majority"]
Channel = Literal["ui", "email", "portal", "api", "mcp", "chat", "auto"]
Decision = Literal["approved", "rejected"]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: PrincipalKind
    user_id: int | None = None  # agent
    login: str | None = None  # customer user login

    def __post_init__(self) -> None:
        if self.kind == "agent":
            if (
                isinstance(self.user_id, bool)
                or not isinstance(self.user_id, int)
                or self.user_id < 1
                or self.login is not None
            ):
                raise ValueError(
                    f"invalid agent principal: user_id={self.user_id!r}, login={self.login!r}"
                )
        elif self.kind == "customer":
            if not isinstance(self.login, str) or not self.login or self.user_id is not None:
                raise ValueError("invalid customer principal")
        else:
            raise ValueError(f"invalid principal kind: {self.kind!r}")

    @property
    def key(self) -> str:
        if self.kind == "agent":
            return f"agent:{self.user_id}"
        return f"customer:{self.login}"

    @classmethod
    def agent(cls, user_id: int) -> Principal:
        if user_id < 1:
            raise ValueError(f"invalid agent id: {user_id}")
        return cls("agent", user_id=user_id)

    @classmethod
    def customer(cls, login: str) -> Principal:
        if not login:
            raise ValueError("empty customer login")
        return cls("customer", login=login)

    @classmethod
    def parse(cls, key: str) -> Principal:
        """Inverse of :attr:`key`; splits on the first ``:`` only ("system" is no Principal)."""
        kind, sep, ref = key.partition(":")
        if not sep or not ref:
            raise ValueError(f"invalid principal key: {key!r}")
        if kind == "agent":
            # canonical decimal only (no sign, whitespace or leading zeros): parse(k).key == k
            if not ref.isascii() or not ref.isdigit() or str(int(ref)) != ref:
                raise ValueError(f"invalid principal key: {key!r}")
            return cls.agent(int(ref))
        if kind == "customer":
            return cls.customer(ref)
        raise ValueError(f"invalid principal key: {key!r}")


@dataclass(frozen=True, slots=True)
class Actor:
    """Who acts. ``principal`` None = the system (sweeper, consumer automation)."""

    principal: Principal | None
    write_user_id: (
        int  # users.id writes are attributed to (PORTAL_WRITE_USER_ID for customers, 1 for system)
    )

    @property
    def key(self) -> str:
        return "system" if self.principal is None else self.principal.key

    @classmethod
    def agent(cls, user_id: int) -> Actor:
        return cls(Principal.agent(user_id), user_id)

    @classmethod
    def customer(cls, login: str) -> Actor:
        return cls(Principal.customer(login), PORTAL_WRITE_USER_ID)

    @classmethod
    def system(cls) -> Actor:
        return cls(None, SYSTEM_WRITE_USER_ID)


class ApproverEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["user", "group", "role", "list", "customer_user", "resolver"]
    ref: str = Field(
        min_length=1, max_length=190
    )  # user id | group id | role id | list id | login | resolver key


class StepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    stage: int = Field(1, ge=1, le=10)
    rule: Rule = "any"
    quorum: int | None = Field(None, ge=1, le=100)  # required iff rule == "quorum"
    approvers: list[ApproverEntry] = Field(min_length=1, max_length=50)
    due_hours: int | None = Field(None, ge=0, le=8760)  # None = settings default; 0 = no deadline
    on_timeout: Literal["notify", "escalate", "reject", "approve"] = "notify"
    escalation: list[ApproverEntry] = Field(
        default_factory=list, max_length=20
    )  # non-empty iff on_timeout == "escalate"
    on_no_approver: Literal["reject", "approve"] = "reject"
    allow_delegation: bool = True
    allow_email: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.rule == "quorum" and self.quorum is None:
            raise ValueError("quorum is required for rule 'quorum'")
        if self.rule != "quorum" and self.quorum is not None:
            raise ValueError("quorum is only allowed for rule 'quorum'")
        if self.on_timeout == "escalate" and not self.escalation:
            raise ValueError("escalation is required for on_timeout 'escalate'")
        if self.on_timeout != "escalate" and self.escalation:
            raise ValueError("escalation is only allowed for on_timeout 'escalate'")
        return self


class ApprovalDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[StepSpec] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _check_stages(self) -> Self:
        stages = {s.stage for s in self.steps}
        if stages != set(range(1, len(stages) + 1)):
            raise ValueError("stage numbers must be contiguous from 1")
        return self


@dataclass(frozen=True, slots=True)
class ResolveContext:
    ticket_id: int
    subject_type: str
    subject_ref: str | None
    approval_id: int


type ResolveFn = Callable[[AsyncSession, ResolveContext], Awaitable[list[Principal]]]


@dataclass(frozen=True, slots=True)
class ApproverResolver:
    key: str  # "<owner>.<name>", e.g. "ticket.customer_user", "itsm.service_owner"
    title_key: str  # frontend i18n key shown in the approver picker
    resolve: ResolveFn
    yields_customers: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    approval_id: int
    ticket_id: int
    subject_type: str
    subject_ref: str | None
    approval_key: str
    status: ApprovalStatus  # never "pending"
    reason: str | None
    decided_at: datetime
    # Comment of the final deciding vote; None for timeout / auto decision / cancel.
    comment: str | None = None


# (session, approval_id) -> extra plain-text lines for approvers
type DescribeFn = Callable[[AsyncSession, int], Awaitable[list[str]]]
type DecidedHook = Callable[[AsyncSession, ApprovalOutcome], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApprovalSubjectType:
    key: str  # "ticket" (core), "itsm.change", "itsm.request", "process.activity"
    title_key: str  # i18n key for the badge, e.g. "approvals.subject.ticket"
    allow_customer_approvers: bool = False
    agent_requestable: bool = (
        False  # may be created through POST /tickets/{id}/approvals (core "ticket" only)
    )
    describe: DescribeFn | None = None
    # Runs in job ``core.approval_hook`` AFTER the deciding commit (the job row is written in the
    # deciding transaction, max_attempts=5), once per final status approved/rejected/cancelled.
    # Never inside the deciding transaction: a consumer bug cannot roll back a vote. Must be
    # idempotent (the job is retried). A timeout arrives as status rejected/approved with
    # outcome.reason == "timeout" - there is no status "expired".
    on_decided: DecidedHook | None = None


@dataclass(frozen=True, slots=True)
class ApprovalInfo:  # what the service returns to consumers
    approval_id: int
    ticket_id: int
    subject_type: str
    subject_ref: str | None
    approval_key: str
    status: ApprovalStatus
    reason: str | None
    current_stage: int | None
    created_at: datetime
    decided_at: datetime | None


class ApprovalError(Exception):
    def __init__(self, code: str, http_status: int, detail: str | None = None) -> None:
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
        self.http_status = http_status
        self.detail = detail


ERROR_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "approval_not_found": 404,
        "ticket_not_found": 404,
        "template_not_found": 404,
        "list_not_found": 404,
        "forbidden": 403,
        "not_an_approver": 403,
        "self_approval_forbidden": 403,
        "customer_approver_outside_company": 403,
        "delegation_not_active": 403,
        "already_decided": 409,
        "approval_closed": 409,
        "step_not_active": 409,
        "forward_not_allowed": 409,
        "forward_target_invalid": 422,
        "invalid_definition": 422,
        "unknown_subject_type": 422,
        "subject_not_requestable": 422,
        "customer_approvers_not_allowed": 422,
        "unknown_resolver": 422,
        "link_invalid": 410,
        "link_expired": 410,
        "link_used": 410,
    }
)

APPROVAL_SUBJECT_TYPES: ExtensionPoint[ApprovalSubjectType] = ExtensionPoint(
    key="core.approval_subject_types",
    item_key=lambda t: t.key,
    description="Things an approval can be about; on_decided runs after the final decision.",
    sdk_stable=False,
)
APPROVER_RESOLVERS: ExtensionPoint[ApproverResolver] = ExtensionPoint(
    key="core.approver_resolvers",
    item_key=lambda r: r.key,
    description="Dynamic approver sources (e.g. the ticket's customer user).",
    sdk_stable=False,
)

# Outbox event names (webhooks + SSE); never prefixed "Notification", never internal
EVENT_REQUESTED: Final = "TiqoraApprovalRequested"
EVENT_VOTED: Final = "TiqoraApprovalVoted"
EVENT_DECIDED: Final = "TiqoraApprovalDecided"  # payload status approved|rejected
EVENT_CANCELLED: Final = "TiqoraApprovalCancelled"
EVENT_ESCALATED: Final = "TiqoraApprovalEscalated"
APPROVAL_EVENTS: Final = (
    EVENT_REQUESTED,
    EVENT_VOTED,
    EVENT_DECIDED,
    EVENT_CANCELLED,
    EVENT_ESCALATED,
)

JOB_HOOK: Final = "core.approval_hook"
JOB_MAIL: Final = "core.approval_mail"
