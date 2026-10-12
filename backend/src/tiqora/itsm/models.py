"""SQLAlchemy models for the ``tiqora_itsm_*`` tables.

Same declarative base and Alembic-managed metadata as ``tiqora.db.tiqora.models``; registered
in ``tiqora.db.tiqora`` like ``tiqora.kb.models``. ``TiqoraItsmLinkType`` comes from the wave-4
step-0 migration ``20261010_0077``; the ITSM core plan appends its other tables here and the
CMDB plan references ``tiqora_itsm_link_type.name`` (string FK, no import from here).

No FK to Znuny tables: ``*_by`` reference ``users.id`` by value.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
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

#: ``tiqora_itsm_major_incident_update.mode`` values
MAJOR_UPDATE_MODES = frozenset({"internal", "note", "reply"})
#: ``tiqora_itsm_bulk_target.status`` values
BULK_TARGET_STATUSES = frozenset({"done", "skipped", "error"})

#: ``tiqora_itsm_link_type.origin`` values
LINK_TYPE_ORIGINS = frozenset({"znuny", "tiqora"})
#: ``tiqora_itsm_link_type.propagation`` values (Znuny ``ITSM::Core::IncidentLinkTypeDirection``)
LINK_TYPE_PROPAGATIONS = frozenset({"none", "source", "target", "both"})


class TiqoraItsmLinkType(TiqoraBase):
    """A link type Tiqora knows: Znuny's (``origin="znuny"``) or Tiqora-only (``"tiqora"``).

    ``name`` is the stable key (immutable after insert). ``source_label``/``target_label``
    NULL = the i18n or Znuny default. ``pointed`` is Znuny's "pointed" (directed) flag.
    ``propagation``: which end gets the incident warning — ``source`` = the SOURCE end when the
    TARGET is in incident. ``offer_*`` = which object pairs the type is offered for.
    """

    __tablename__ = "tiqora_itsm_link_type"
    __table_args__ = (UniqueConstraint("name", name="uq_tiqora_itsm_link_type_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pointed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    propagation: Mapped[str] = mapped_column(
        String(10), nullable=False, default="none", server_default="none"
    )
    offer_ticket_ticket: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    offer_ticket_ci: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    offer_ci_ci: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    offer_ci_service: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmPriorityMatrix(TiqoraBase):
    """Priority matrix cell: (criticality, impact) -> ``ticket_priority.id``.

    ``criticality``/``impact`` are the keys of the ITSMCriticality / ITSMImpact dynamic fields
    (e.g. ``"3 normal"``). ``priority_id`` references a Znuny row by value.
    """

    __tablename__ = "tiqora_itsm_priority_matrix"
    __table_args__ = (
        UniqueConstraint("criticality", "impact", name="uq_tiqora_itsm_priority_matrix_cell"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    criticality: Mapped[str] = mapped_column(String(200), nullable=False)
    impact: Mapped[str] = mapped_column(String(200), nullable=False)
    priority_id: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmServiceMeta(TiqoraBase):
    """Tiqora-side service attributes. ``service_id`` references ``service.id`` by value.

    ``service_type``/``criticality`` are used when Znuny ITSM is absent; Mode A reads
    ``service.type_id`` / ``service.criticality`` instead.
    """

    __tablename__ = "tiqora_itsm_service_meta"

    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    service_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    criticality: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    portal_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: NULL = settings default; 0 = off
    min_minutes_between_incidents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmProblem(TiqoraBase):
    """Problem record of a problem ticket (``ticket_id`` references ``ticket.id`` by value)."""

    __tablename__ = "tiqora_itsm_problem"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    root_cause: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    workaround: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    known_error: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    known_error_article_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_kb_article.id",
            name="fk_tiqora_itsm_problem_kb_article",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    known_error_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TiqoraItsmMajorIncident(TiqoraBase):
    """Major-incident declaration of a ticket; ``ended_at IS NULL`` = active."""

    __tablename__ = "tiqora_itsm_major_incident"
    __table_args__ = (Index("ix_tiqora_itsm_major_incident_active", "ended_at"),)

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    declared_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    declared_by: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TiqoraItsmMajorIncidentUpdate(TiqoraBase):
    """One status update of a major incident (rows go with their major incident)."""

    __tablename__ = "tiqora_itsm_major_incident_update"
    __table_args__ = (Index("ix_tiqora_itsm_mi_update_ticket", "ticket_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_itsm_major_incident.ticket_id",
            name="fk_tiqora_itsm_mi_update_major",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    #: internal | note | reply
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class TiqoraItsmBulkTarget(TiqoraBase):
    """Durable per-target progress of a linked-articles job (``tiqora_job.id``, ``ticket.id``).

    Written in the same transaction as the article, so a restarted job never sends twice
    (customer e-mails in reply mode). ``status``: done | skipped | error; ``reason`` is the
    skip reason (``no_rw``, ``not_linked`` ...), ``detail`` a short error text.
    """

    __tablename__ = "tiqora_itsm_bulk_target"

    job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "BULK_TARGET_STATUSES",
    "LINK_TYPE_ORIGINS",
    "LINK_TYPE_PROPAGATIONS",
    "MAJOR_UPDATE_MODES",
    "TiqoraItsmBulkTarget",
    "TiqoraItsmLinkType",
    "TiqoraItsmMajorIncident",
    "TiqoraItsmMajorIncidentUpdate",
    "TiqoraItsmPriorityMatrix",
    "TiqoraItsmProblem",
    "TiqoraItsmServiceMeta",
]
