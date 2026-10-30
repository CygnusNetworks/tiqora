"""SQLAlchemy models for the ``tiqora_itsm_change*`` / ``tiqora_itsm_cab*`` / legacy tables
(migration ``20261010_0084``).

Same declarative base and Alembic-managed metadata as ``tiqora.db.tiqora.models``; registered in
``tiqora.db.tiqora`` like ``tiqora.itsm.models``. No FK to Znuny tables: ``ticket_id``,
``service_id``, ``*_by`` and ``calendar_appointment_id`` reference Znuny rows by value; FKs only
join ``tiqora_*`` tables. ``approval_id`` references ``tiqora_approval.id`` by value too (no FK:
a GDPR erasure may delete the approval set while the change record stays). Timestamps are naive
UTC written by the application (``tiqora.jobs.service.utc_now``).
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
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import MEDIUM_TEXT_VARIANT

_CHANGE_FK = "tiqora_itsm_change.ticket_id"


class TiqoraItsmChangeTemplate(TiqoraBase):
    """Change template (``defaults`` = TemplateDefaults JSON, ``tasks`` = list of TemplateTask)."""

    __tablename__ = "tiqora_itsm_change_template"
    __table_args__ = (UniqueConstraint("name", name="uq_tiqora_itsm_change_template_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_type: Mapped[str] = mapped_column(String(10), nullable=False)
    is_standard: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    defaults: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    tasks: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmCab(TiqoraBase):
    """A CAB board. Its members speak the approval engine's approver vocabulary."""

    __tablename__ = "tiqora_itsm_cab"
    __table_args__ = (UniqueConstraint("name", name="uq_tiqora_itsm_cab_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``any|all|majority|quorum`` (approval engine rules)
    rule: Mapped[str] = mapped_column(String(10), nullable=False)
    quorum: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmCabMember(TiqoraBase):
    """CAB member as an engine approver entry: ``member_kind`` is an ``ApproverEntry.kind``
    (``user|group|role|list|customer_user|resolver``), ``member_ref`` the id as text (or the
    customer login / resolver key) — never a name."""

    __tablename__ = "tiqora_itsm_cab_member"
    __table_args__ = (
        UniqueConstraint("cab_id", "member_kind", "member_ref", name="uq_tiqora_itsm_cab_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cab_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tiqora_itsm_cab.id", name="fk_tiqora_itsm_cab_member_cab", ondelete="CASCADE"),
        nullable=False,
    )
    member_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    member_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class TiqoraItsmChange(TiqoraBase):
    """Change record of a change ticket (``ticket_id`` references ``ticket.id`` by value)."""

    __tablename__ = "tiqora_itsm_change"
    __table_args__ = (
        Index("ix_tiqora_itsm_change_phase", "phase"),
        Index("ix_tiqora_itsm_change_window", "planned_start", "planned_end"),
    )

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    #: ``standard|normal|emergency``
    change_type: Mapped[str] = mapped_column(String(10), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    #: ``successful|failed|backed_out`` (phase ``closed`` only)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)
    template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_itsm_change_template.id",
            name="fk_tiqora_itsm_change_template",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    cab_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tiqora_itsm_cab.id", name="fk_tiqora_itsm_change_cab", ondelete="SET NULL"),
        nullable=True,
    )
    planned_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    implementation_plan: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    backout_plan: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    test_plan: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    pir_notes: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``low|medium|high|very_high`` (computed)
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    risk_findings: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    risk_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    risk_override_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    risk_override_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    risk_override_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``cab|template|risk|post``
    approval_via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    approval_round: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: ``tiqora_approval.id`` of the current round, by value (no FK; GDPR may erase the set)
    approval_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    impact_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    impact_computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    impact_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    portal_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: ``calendar_appointment.id`` (Znuny), by value
    calendar_appointment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmChangeService(TiqoraBase):
    """Extra affected services (besides ``ticket.service_id``)."""

    __tablename__ = "tiqora_itsm_change_service"

    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_change_service_change", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TiqoraItsmChangeImpact(TiqoraBase):
    """Stored affected set, replaced as a whole by the impact job."""

    __tablename__ = "tiqora_itsm_change_impact"
    __table_args__ = (Index("ix_tiqora_itsm_change_impact_ref", "kind", "ref_key"),)

    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_change_impact_change", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    #: ``ci|service``
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    #: ci: ``tiqora_cmdb_ci.id`` as text; service: ``service.id`` as text
    ref_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    #: linked to the change (True) vs. reached through propagation (False)
    direct: Mapped[bool] = mapped_column(Boolean, nullable=False)


class TiqoraItsmChangeTask(TiqoraBase):
    """Change task = child ticket of type ``change_task_type``."""

    __tablename__ = "tiqora_itsm_change_task"
    __table_args__ = (
        UniqueConstraint(
            "change_ticket_id", "template_task_key", name="uq_tiqora_itsm_change_task_tpl"
        ),
        Index("ix_tiqora_itsm_change_task_change", "change_ticket_id", "position"),
    )

    #: the task ticket
    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    change_ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_change_task_change", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``workorder|backout|decision|pir``
    task_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_effort_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: idempotency key of the template task
    template_task_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmChangeRiskQuestion(TiqoraBase):
    """Risk questionnaire question (``answers`` = JSON list of ``{key,label,points}``)."""

    __tablename__ = "tiqora_itsm_change_risk_question"
    __table_args__ = (UniqueConstraint("question_key", name="uq_tiqora_itsm_change_risk_q_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_key: Mapped[str] = mapped_column(String(50), nullable=False)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    help: Mapped[str | None] = mapped_column(Text, nullable=True)
    answers: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmChangeRiskAnswer(TiqoraBase):
    """A change's answer to a questionnaire question."""

    __tablename__ = "tiqora_itsm_change_risk_answer"

    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_change_risk_answer_change", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    question_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    answer_key: Mapped[str] = mapped_column(String(50), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    answered_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmChangeFreeze(TiqoraBase):
    """Freeze window (Sperrzeitraum), optionally recurring (UTC)."""

    __tablename__ = "tiqora_itsm_change_freeze"
    __table_args__ = (Index("ix_tiqora_itsm_change_freeze_start", "starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: ``block|warn``
    enforcement: Mapped[str] = mapped_column(String(5), nullable=False)
    allow_emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: ``all|services``
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    recur_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    recur_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recur_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recur_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmChangeFreezeService(TiqoraBase):
    """Services a ``scope="services"`` freeze window applies to."""

    __tablename__ = "tiqora_itsm_change_freeze_service"

    freeze_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_itsm_change_freeze.id",
            name="fk_tiqora_itsm_change_freeze_service_freeze",
            ondelete="CASCADE",
        ),
        primary_key=True,
        autoincrement=False,
    )
    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TiqoraItsmCabMeeting(TiqoraBase):
    """CAB meeting; a CAB with meetings cannot be deleted (plain FK: restrict, as MariaDB reflects
    RESTRICT and NO ACTION alike)."""

    __tablename__ = "tiqora_itsm_cab_meeting"
    __table_args__ = (Index("ix_tiqora_itsm_cab_meeting_start", "starts_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cab_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tiqora_itsm_cab.id", name="fk_tiqora_itsm_cab_meeting_cab"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: ``planned|held|cancelled``
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    minutes: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmCabAgendaItem(TiqoraBase):
    """A change on a meeting's agenda."""

    __tablename__ = "tiqora_itsm_cab_agenda_item"
    __table_args__ = (
        UniqueConstraint("meeting_id", "change_ticket_id", name="uq_tiqora_itsm_cab_agenda"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_itsm_cab_meeting.id",
            name="fk_tiqora_itsm_cab_agenda_meeting",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    change_ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_cab_agenda_change", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TiqoraItsmChangeEvent(TiqoraBase):
    """Change timeline. Every phase change writes one ``event="phase"`` row with
    ``from_phase`` / ``to_phase``."""

    __tablename__ = "tiqora_itsm_change_event"
    __table_args__ = (
        Index("ix_tiqora_itsm_change_event_ticket", "ticket_id", "id"),
        Index("ix_tiqora_itsm_change_event_time", "event", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(_CHANGE_FK, name="fk_tiqora_itsm_change_event_change", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    #: created|adopted|phase|updated|risk_override|conflict_override|approval_requested|
    #: approval_outcome|impact|task_added|forced
    event: Mapped[str] = mapped_column(String(30), nullable=False)
    from_phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: JSON
    detail: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)


class TiqoraItsmLegacyChange(TiqoraBase):
    """Read-only archive of a Znuny ITSMChangeManagement change."""

    __tablename__ = "tiqora_itsm_legacy_change"
    __table_args__ = (
        UniqueConstraint("znuny_change_id", name="uq_tiqora_itsm_legacy_change_znuny"),
        Index("ix_tiqora_itsm_legacy_change_number", "change_number"),
        Index("ix_tiqora_itsm_legacy_change_planned", "planned_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    znuny_change_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    change_number: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(250), nullable=True)
    state: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    impact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manager_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    builder_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    znuny_created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    znuny_changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    workorder_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: JSON LegacyChangeData
    data: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    import_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class TiqoraItsmLegacyChangeRef(TiqoraBase):
    """Lookup row of an archived change (linked ticket, CI, service, customer user)."""

    __tablename__ = "tiqora_itsm_legacy_change_ref"
    __table_args__ = (Index("ix_tiqora_itsm_legacy_change_ref", "ref_kind", "ref_key"),)

    legacy_change_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_itsm_legacy_change.id",
            name="fk_tiqora_itsm_legacy_change_ref_change",
            ondelete="CASCADE",
        ),
        primary_key=True,
        autoincrement=False,
    )
    #: ``ticket|ci|service|customer_user``
    ref_kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    #: ticket id | ``tiqora:<id>`` / ``znuny:<id>`` | service id | login
    ref_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    #: 0 = the change itself
    workorder_number: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, default=0, server_default="0"
    )


class TiqoraItsmLegacyErased(TiqoraBase):
    """GDPR tombstone: an erased customer login or address that never re-enters the archive.

    Only a keyed hash (HMAC-SHA256 of the lowercased identifier,
    ``tiqora.itsm.change.legacy.redact.erased_hash``) is stored, never the identifier itself.
    One row per erasure job and identifier: rolling one job back removes its rows only, so an
    identifier another erasure still holds stays erased. One more row of kind ``check`` holds the
    check value of the key the tombstones were written with (``redact.key_check``): a run under
    another key refuses.
    """

    __tablename__ = "tiqora_itsm_legacy_erased"
    __table_args__ = (
        # NULL job ids (the CLI anonymisation, the key check) are distinct for the unique key on
        # both dialects: the writer looks for an existing row first (``archive._add_tombstones``)
        UniqueConstraint("identifier_hash", "job_id", name="uq_tiqora_itsm_legacy_erased_hash_job"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    identifier_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    #: ``login`` | ``email`` | ``check`` (the key check row, not a tombstone)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``tiqora_gdpr_job.id`` (by value); ``NULL`` for the CLI anonymisation, which has no job
    #: and no rollback
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False)
