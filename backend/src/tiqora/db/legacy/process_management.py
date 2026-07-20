"""Znuny tables `pm_process`, `pm_activity`, `pm_activity_dialog`,
`pm_transition`, `pm_transition_action`, `pm_entity_sync`.

Legacy Znuny schema metadata — never managed by Alembic. These map the
existing Znuny 6.5 ProcessManagement (BPM) schema verbatim (see
``schema.xml`` lines ~2110-2213) — no new tables or columns — so rows read
here are byte-for-byte what a running Znuny instance wrote and vice versa
(parallel-operation compatible, same as the rest of ``tiqora.db.legacy``).

All five ``pm_*`` config tables share the same shape: an autoincrement
``id``, a unique ``entity_id`` string (the stable identifier referenced from
other config, e.g. ``Activity-<hash>``), a human-readable ``name``, a YAML
``config`` blob, and the usual create/change time+by audit columns.
``pm_process`` additionally carries ``state_entity_id`` (references a
``general_catalog`` "Process::State" entity — not modeled here, see
:mod:`tiqora.process`) and a cosmetic designer-canvas ``layout`` YAML blob.

``config``/``layout`` are LONGBLOB in Znuny's MySQL DDL but store YAML
*text* (read via Perl ``YAML::Load``). Mapped as :class:`~sqlalchemy.Text`
(not LargeBinary) so both dialects round-trip as Unicode strings — a prior
LargeBinary mapping caused PostgreSQL to implicitly cast the bytea bind
parameter to its ``\\x..``-hex text form on INSERT into a TEXT column,
silently corrupting stored config (see the same note on
:class:`tiqora.db.legacy.dynamic_field.DynamicField.config`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime


class PmProcess(LegacyBase):
    """Znuny table `pm_process`."""

    __tablename__ = "pm_process"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state_entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    layout: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class PmActivity(LegacyBase):
    """Znuny table `pm_activity`."""

    __tablename__ = "pm_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class PmActivityDialog(LegacyBase):
    """Znuny table `pm_activity_dialog`."""

    __tablename__ = "pm_activity_dialog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class PmTransition(LegacyBase):
    """Znuny table `pm_transition`."""

    __tablename__ = "pm_transition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class PmTransitionAction(LegacyBase):
    """Znuny table `pm_transition_action`."""

    __tablename__ = "pm_transition_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class PmEntitySync(LegacyBase):
    """Znuny table `pm_entity_sync` — designer "unsynced changes" tracker.

    Composite-key-only table (no surrogate id in Znuny's schema): a
    ``(entity_type, entity_id)`` tuple is unique. Included for completeness;
    Tiqora's read/execute path (this package) does not consult it — it is
    only relevant to Znuny's own process-designer "sync to database" UI
    workflow, which Tiqora does not reimplement.
    """

    __tablename__ = "pm_entity_sync"

    entity_type: Mapped[str] = mapped_column(String(30), primary_key=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    sync_state: Mapped[str] = mapped_column(String(30), nullable=False)
    create_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
    change_time: Mapped[datetime] = mapped_column(LegacyDateTime, nullable=False)
