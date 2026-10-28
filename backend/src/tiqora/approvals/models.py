"""SQLAlchemy models for the ``tiqora_approval*`` tables (migration ``20261010_0083``).

Same declarative base and Alembic-managed metadata as ``tiqora.db.tiqora.models``; registered in
``tiqora.db.tiqora`` like ``tiqora.itsm.models``. No FK to Znuny tables: ``ticket_id``,
``user_id``, ``*_by``, group/role ids and customer logins reference Znuny rows by value.
Timestamps are naive UTC written by the application (``tiqora.jobs.service.utc_now``).

Principal columns are ``VARCHAR(250)``: ``customer:`` plus Znuny's 200-character login.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import MEDIUM_TEXT_VARIANT

#: ``tiqora_approval.status`` values
APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected", "cancelled"})
#: ``tiqora_approval_step.status`` values
STEP_STATUSES = frozenset({"waiting", "active", "approved", "rejected", "cancelled"})
#: ``tiqora_approval_approver.status`` values
APPROVER_STATUSES = frozenset(
    {"pending", "approved", "rejected", "forwarded", "removed", "not_needed"}
)
#: ``tiqora_approval_approver.source_kind`` values
APPROVER_SOURCES = frozenset(
    {"user", "group", "role", "list", "customer_user", "resolver", "escalation", "forward"}
)
#: ``tiqora_approval_vote.channel`` / ``tiqora_approval_event.channel`` values ("chat" is reserved)
CHANNELS = frozenset({"ui", "email", "portal", "api", "mcp", "chat", "auto"})
#: ``tiqora_approval_event.action`` values
AUDIT_ACTIONS = frozenset(
    {
        "requested",
        "step_activated",
        "approver_added",
        "approver_excluded",
        "approver_removed",
        "notified",
        "reminded",
        "link_issued",
        "voted",
        "forwarded",
        "step_decided",
        "overdue",
        "escalated",
        "timeout_decided",
        "refreshed",
        "decided",
        "auto_decided",
        "cancelled",
        "superseded",
        "hook_done",
        "hook_failed",
        "note_skipped",
    }
)

#: Tables keyed by ``ticket_id`` (GDPR erasure): children before parents.
APPROVAL_TICKET_TABLES: tuple[str, ...] = (
    "tiqora_approval_token",
    "tiqora_approval_vote",
    "tiqora_approval_approver",
    "tiqora_approval_event",
    "tiqora_approval_step",
    "tiqora_approval",
)


class TiqoraApprovalTemplate(TiqoraBase):
    """Reusable approval definition (``definition`` = ``ApprovalDefinition`` JSON)."""

    __tablename__ = "tiqora_approval_template"
    __table_args__ = (UniqueConstraint("template_key", name="uq_tiqora_approval_template_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    #: JSON list of subject type keys; ``[]`` = every subject type
    subject_types: Mapped[str] = mapped_column(
        String(500), nullable=False, default="[]", server_default="[]"
    )
    definition: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    #: ``admin`` or the consumer key that registered it (``ensure_template``)
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="admin", server_default="admin"
    )
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraApprovalList(TiqoraBase):
    """Approver list ("Freigabegruppe", e.g. a CAB)."""

    __tablename__ = "tiqora_approval_list"
    __table_args__ = (UniqueConstraint("name", name="uq_tiqora_approval_list_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(190), nullable=False)
    description: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraApprovalListMember(TiqoraBase):
    """Agent member of an approver list (``user_id`` references ``users.id`` by value)."""

    __tablename__ = "tiqora_approval_list_member"

    list_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_approval_list.id",
            name="fk_tiqora_approval_list_member_list",
            ondelete="CASCADE",
        ),
        primary_key=True,
        autoincrement=False,
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TiqoraApprovalDelegation(TiqoraBase):
    """Standing substitute: ``substitute_user_id`` may decide for ``user_id``."""

    __tablename__ = "tiqora_approval_delegation"
    __table_args__ = (
        Index("ix_tiqora_approval_delegation_sub", "substitute_user_id"),
        Index("ix_tiqora_approval_delegation_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    substitute_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraApproval(TiqoraBase):
    """One approval request."""

    __tablename__ = "tiqora_approval"
    __table_args__ = (
        Index("ix_tiqora_approval_ticket_key", "ticket_id", "approval_key", "id"),
        Index("ix_tiqora_approval_status", "status", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String(190), nullable=True)
    approval_key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_approval_template.id",
            name="fk_tiqora_approval_template",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: votes|timeout|no_approvers|auto|cancelled|superseded|ticket_deleted
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_self_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: principal key: ``agent:12`` | ``customer:<login>`` | ``system``
    requester: Mapped[str] = mapped_column(String(250), nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: ``tiqora_approval.id`` of the replacing request (no FK: avoids a cycle)
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TiqoraApprovalStep(TiqoraBase):
    """One step (``stage`` equal = parallel) of a request."""

    __tablename__ = "tiqora_approval_step"
    __table_args__ = (
        UniqueConstraint("approval_id", "position", name="uq_tiqora_approval_step_pos"),
        Index("ix_tiqora_approval_step_due", "status", "due_at"),
        Index("ix_tiqora_approval_step_ticket", "ticket_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval.id", name="fk_tiqora_approval_step_approval", ondelete="CASCADE"
        ),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule: Mapped[str] = mapped_column(String(10), nullable=False)
    quorum: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ``StepSpec`` JSON as requested (audit + re-resolution)
    spec: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: set once when the deadline passed (notify/escalate done)
    overdue_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TiqoraApprovalApprover(TiqoraBase):
    """Resolved individual approver (snapshot at activation)."""

    __tablename__ = "tiqora_approval_approver"
    __table_args__ = (
        UniqueConstraint("step_id", "principal", name="uq_tiqora_approval_approver"),
        Index("ix_tiqora_approval_approver_inbox", "principal", "status"),
        Index("ix_tiqora_approval_approver_user", "user_id", "status"),
        Index("ix_tiqora_approval_approver_ticket", "ticket_id"),
        #: the sweeper's invalid-approver scan (pending rows by request id)
        Index("ix_tiqora_approval_approver_status", "status", "approval_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval.id", name="fk_tiqora_approval_approver_approval", ondelete="CASCADE"
        ),
        nullable=False,
    )
    step_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval_step.id", name="fk_tiqora_approval_approver_step", ondelete="CASCADE"
        ),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ``agent:12`` | ``customer:<login>``
    principal: Mapped[str] = mapped_column(String(250), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: same width as Znuny's ``customer_user.login``
    customer_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(190), nullable=True)
    #: group/role/list name at resolution (display only)
    source_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: escalation approvers: their vote decides the step alone
    decisive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TiqoraApprovalVote(TiqoraBase):
    """A vote; final (one row per approver)."""

    __tablename__ = "tiqora_approval_vote"
    __table_args__ = (
        UniqueConstraint("approver_id", name="uq_tiqora_approval_vote_approver"),
        Index("ix_tiqora_approval_vote_ticket", "ticket_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval.id", name="fk_tiqora_approval_vote_approval", ondelete="CASCADE"
        ),
        nullable=False,
    )
    step_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval_step.id", name="fk_tiqora_approval_vote_step", ondelete="CASCADE"
        ),
        nullable=False,
    )
    approver_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval_approver.id",
            name="fk_tiqora_approval_vote_approver",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    #: at most 2000 characters (API validation)
    comment: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    #: principal who clicked (approver or delegate)
    decided_by: Mapped[str] = mapped_column(String(250), nullable=False)
    on_behalf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraApprovalToken(TiqoraBase):
    """E-mail decision link: only the sha256 of the token is stored."""

    __tablename__ = "tiqora_approval_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_tiqora_approval_token_hash"),
        Index("ix_tiqora_approval_token_approver", "approver_id"),
        Index("ix_tiqora_approval_token_ticket", "ticket_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval.id", name="fk_tiqora_approval_token_approval", ondelete="CASCADE"
        ),
        nullable=False,
    )
    approver_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval_approver.id",
            name="fk_tiqora_approval_token_approver",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: sha256 hex of the token; the token itself is never stored
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: redeemed (the decision of this link)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: dead for another reason (decided elsewhere, closed, cap)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TiqoraApprovalEvent(TiqoraBase):
    """Append-only audit trail (``detail`` JSON carries ids, keys and statuses only)."""

    __tablename__ = "tiqora_approval_event"
    __table_args__ = (
        Index("ix_tiqora_approval_event_approval", "approval_id", "id"),
        Index("ix_tiqora_approval_event_ticket", "ticket_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_approval.id", name="fk_tiqora_approval_event_approval", ondelete="CASCADE"
        ),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    #: principal key or ``system``
    actor: Mapped[str] = mapped_column(String(250), nullable=False)
    step_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: ``tiqora_job.id`` of the mail job (retry dedupe of ``notified`` without reading ``detail``)
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
