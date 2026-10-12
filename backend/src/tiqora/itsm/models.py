"""SQLAlchemy models for the ``tiqora_itsm_*`` tables.

Same declarative base and Alembic-managed metadata as ``tiqora.db.tiqora.models``; registered
in ``tiqora.db.tiqora`` like ``tiqora.kb.models``. ``TiqoraItsmLinkType`` comes from the wave-4
step-0 migration ``20261010_0077``; the ITSM core plan appends its other tables here and the
CMDB plan references ``tiqora_itsm_link_type.name`` (string FK, no import from here).

No FK to Znuny tables: ``*_by`` reference ``users.id`` by value.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase

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


__all__ = ["LINK_TYPE_ORIGINS", "LINK_TYPE_PROPAGATIONS", "TiqoraItsmLinkType"]
