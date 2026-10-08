"""Where the process engine reads its definitions from.

The engine never reads ``pm_*`` directly: it asks a :class:`ProcessSource`. The
active source is the newest *deployed* process revision (a snapshot of Znuny's
``ProcessDump`` shape), so drafts edited in the Znuny designer stay invisible
until they are deployed, exactly like Znuny's own ``ZZZProcessManagement.pm``.
Only while no revision exists yet does :class:`LiveProcessSource` read the
``pm_*`` rows (a fresh install, before the worker has recorded the first one).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmProcess,
    PmTransition,
    PmTransitionAction,
)
from tiqora.process.config import _load_yaml_mapping

if TYPE_CHECKING:
    from tiqora.deployed.store import LoadedRevision

logger = logging.getLogger(__name__)

EntityKind = Literal["Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"]

_PmRow = PmProcess | PmActivity | PmActivityDialog | PmTransition | PmTransitionAction

_LEGACY_MODELS: dict[str, type[_PmRow]] = {
    "Process": PmProcess,
    "Activity": PmActivity,
    "ActivityDialog": PmActivityDialog,
    "Transition": PmTransition,
    "TransitionAction": PmTransitionAction,
}

_warned_live = False


@dataclass(frozen=True, slots=True)
class EntityRecord:
    id: int
    entity_id: str
    name: str
    data: Mapping[str, Any]  # parsed config (dump entry or yaml.safe_load of pm_*.config)
    state_entity_id: str = ""  # processes only
    layout: Mapping[str, Any] = field(default_factory=dict)  # processes only, live source only


class ProcessSource(Protocol):
    revision_id: int | None  # None = live pm_* rows

    async def list_processes(self) -> list[EntityRecord]: ...

    async def load(
        self, kind: EntityKind, entity_ids: Iterable[str]
    ) -> dict[str, EntityRecord]: ...


class LiveProcessSource:
    """Reads the live ``pm_*`` rows (drafts included)."""

    revision_id: int | None = None

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_processes(self) -> list[EntityRecord]:
        rows = (
            (await self._session.execute(select(PmProcess).order_by(PmProcess.name)))
            .scalars()
            .all()
        )
        return [self._record(row) for row in rows]

    async def load(self, kind: EntityKind, entity_ids: Iterable[str]) -> dict[str, EntityRecord]:
        wanted = set(entity_ids)
        if not wanted:
            return {}
        model: Any = _LEGACY_MODELS[kind]
        rows = (
            (await self._session.execute(select(model).where(model.entity_id.in_(wanted))))
            .scalars()
            .all()
        )
        return {row.entity_id: self._record(row) for row in rows}

    @staticmethod
    def _record(row: _PmRow) -> EntityRecord:
        if isinstance(row, PmProcess):
            return EntityRecord(
                id=row.id,
                entity_id=row.entity_id,
                name=row.name,
                data=_load_yaml_mapping(row.config),
                state_entity_id=row.state_entity_id,
                layout=_load_yaml_mapping(row.layout),
            )
        return EntityRecord(
            id=row.id,
            entity_id=row.entity_id,
            name=row.name,
            data=_load_yaml_mapping(row.config),
        )


class RevisionProcessSource:
    """Reads one deployed revision. The dump is shared and must not be mutated."""

    def __init__(self, revision: LoadedRevision) -> None:
        self._dump = revision.dump
        self.revision_id: int | None = revision.info.id

    def _record(self, entity_id: str, entry: Mapping[str, Any]) -> EntityRecord:
        return EntityRecord(
            id=int(entry.get("ID") or 0),
            entity_id=entity_id,
            name=str(entry.get("Name") or ""),
            data=entry,
            state_entity_id=str(entry.get("StateEntityID") or ""),
        )

    async def list_processes(self) -> list[EntityRecord]:
        processes = self._dump.get("Process") or {}
        records = [self._record(eid, entry) for eid, entry in processes.items()]
        return sorted(records, key=lambda r: r.name)

    async def load(self, kind: EntityKind, entity_ids: Iterable[str]) -> dict[str, EntityRecord]:
        section = self._dump.get(kind) or {}
        return {eid: self._record(eid, section[eid]) for eid in set(entity_ids) if eid in section}


async def active_process_source(session: AsyncSession) -> ProcessSource:
    """Newest deployed revision, else the live rows (logged once per process)."""
    global _warned_live  # noqa: PLW0603
    # Deferred: tiqora.deployed.store imports tiqora.process.dump, whose package
    # __init__ imports the engine and thus this module.
    from tiqora.deployed.store import load_active_revision

    revision = await load_active_revision(session, "process")
    if revision is not None:
        return RevisionProcessSource(revision)
    if not _warned_live:
        _warned_live = True
        logger.warning(
            "process_definitions_running_live", extra={"reason": "no deployed revision yet"}
        )
    return LiveProcessSource(session)
