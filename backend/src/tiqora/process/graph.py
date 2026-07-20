"""In-memory, fully-linked ProcessManagement object graph + async loader.

``ProcessRepository`` loads one ``pm_process`` row plus every
``pm_activity``/``pm_activity_dialog``/``pm_transition``/``pm_transition_action``
row it (transitively) references — by ``entity_id`` — into a
``ProcessGraph``: Process -> Activities -> ActivityDialogs, and
Activity -> outgoing Transitions -> target Activity + TransitionActions.

Read-only. No condition evaluation, no action dispatch — that is subtask 2's
``process/engine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmProcess,
    PmTransition,
    PmTransitionAction,
)
from tiqora.process.config import (
    ActivityConfig,
    ActivityDialogConfig,
    ProcessConfig,
    ProcessLayout,
    TransitionActionConfig,
    TransitionConfig,
)

_EntityRow = TypeVar("_EntityRow", PmActivity, PmActivityDialog, PmTransition, PmTransitionAction)


@dataclass(frozen=True)
class ActivityDialogNode:
    id: int
    entity_id: str
    name: str
    config: ActivityDialogConfig


@dataclass(frozen=True)
class TransitionActionNode:
    id: int
    entity_id: str
    name: str
    config: TransitionActionConfig


@dataclass(frozen=True)
class TransitionNode:
    id: int
    entity_id: str
    name: str
    config: TransitionConfig
    target_activity_entity_id: str
    """``ActivityEntityID`` this transition leads to (from the process's ``Path``)."""
    actions: list[TransitionActionNode] = field(default_factory=list)
    """Resolved ``TransitionAction`` nodes, in the order listed in ``Path``."""


@dataclass(frozen=True)
class ActivityNode:
    id: int
    entity_id: str
    name: str
    config: ActivityConfig
    activity_dialogs: list[ActivityDialogNode] = field(default_factory=list)
    """Resolved dialogs, ordered per ``ActivityConfig.activity_dialogs``."""
    outgoing_transitions: list[TransitionNode] = field(default_factory=list)
    """Transitions with this activity as their ``Path`` source key."""


@dataclass(frozen=True)
class ProcessGraph:
    id: int
    entity_id: str
    name: str
    state_entity_id: str
    config: ProcessConfig
    layout: ProcessLayout
    activities: dict[str, ActivityNode] = field(default_factory=dict)
    """Keyed by activity ``entity_id``."""

    @property
    def start_activity(self) -> ActivityNode | None:
        if self.config.start_activity is None:
            return None
        return self.activities.get(self.config.start_activity)


@dataclass(frozen=True)
class ProcessSummary:
    """Lightweight listing row — see ``ProcessRepository.list_processes``."""

    id: int
    entity_id: str
    name: str
    state_entity_id: str


class ProcessRepository:
    """Async, read-only loader for the ProcessManagement object graph.

    Znuny process validity/activation is normally driven by SysConfig
    (``Process::Default::...``) plus a ``general_catalog`` "Process::State"
    entity that ``state_entity_id`` (e.g. ``S1``) references. Tiqora does
    not model ``general_catalog`` lookups here — ``list_processes`` returns
    ALL ``pm_process`` rows with their raw ``state_entity_id`` and leaves
    "is this process active" to the caller (e.g. resolve ``S1`` against
    ``general_catalog`` separately, or compare against a configured active
    id). This is a deliberate simplification, not an oversight.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_processes(self) -> list[ProcessSummary]:
        rows = (
            (await self._session.execute(select(PmProcess).order_by(PmProcess.name)))
            .scalars()
            .all()
        )
        return [
            ProcessSummary(
                id=row.id,
                entity_id=row.entity_id,
                name=row.name,
                state_entity_id=row.state_entity_id,
            )
            for row in rows
        ]

    async def get_process(self, entity_id: str) -> ProcessGraph | None:
        process_row = (
            await self._session.execute(select(PmProcess).where(PmProcess.entity_id == entity_id))
        ).scalar_one_or_none()
        if process_row is None:
            return None

        process_config = ProcessConfig.from_yaml(process_row.config)
        layout = ProcessLayout.from_yaml(process_row.layout)

        activity_entity_ids = set(process_config.path.keys())
        for transitions in process_config.path.values():
            for entry in transitions.values():
                if entry.activity_entity_id:
                    activity_entity_ids.add(entry.activity_entity_id)
        if process_config.start_activity:
            activity_entity_ids.add(process_config.start_activity)

        activity_rows = await self._load_by_entity_id(PmActivity, activity_entity_ids)
        activity_configs = {
            row.entity_id: ActivityConfig.from_yaml(row.config) for row in activity_rows.values()
        }

        dialog_entity_ids: set[str] = set()
        for ac in activity_configs.values():
            dialog_entity_ids.update(ac.ordered_activity_dialog_entity_ids)
        dialog_rows = await self._load_by_entity_id(PmActivityDialog, dialog_entity_ids)
        dialog_nodes = {
            row.entity_id: ActivityDialogNode(
                id=row.id,
                entity_id=row.entity_id,
                name=row.name,
                config=ActivityDialogConfig.from_yaml(row.config),
            )
            for row in dialog_rows.values()
        }

        transition_entity_ids: set[str] = set()
        for transitions in process_config.path.values():
            transition_entity_ids.update(transitions.keys())
        transition_rows = await self._load_by_entity_id(PmTransition, transition_entity_ids)
        transition_configs = {
            row.entity_id: TransitionConfig.from_yaml(row.config)
            for row in transition_rows.values()
        }

        action_entity_ids: set[str] = set()
        for transitions in process_config.path.values():
            for entry in transitions.values():
                action_entity_ids.update(entry.transition_actions)
        action_rows = await self._load_by_entity_id(PmTransitionAction, action_entity_ids)
        action_nodes = {
            row.entity_id: TransitionActionNode(
                id=row.id,
                entity_id=row.entity_id,
                name=row.name,
                config=TransitionActionConfig.from_yaml(row.config),
            )
            for row in action_rows.values()
        }

        activities: dict[str, ActivityNode] = {}
        for entity_id, row in activity_rows.items():
            activity_config = activity_configs[entity_id]
            dialogs = [
                dialog_nodes[d_id]
                for d_id in activity_config.ordered_activity_dialog_entity_ids
                if d_id in dialog_nodes
            ]

            outgoing: list[TransitionNode] = []
            for transition_id, entry in (process_config.path.get(entity_id) or {}).items():
                transition_row = transition_rows.get(transition_id)
                if transition_row is None:
                    continue
                actions = [
                    action_nodes[a_id] for a_id in entry.transition_actions if a_id in action_nodes
                ]
                outgoing.append(
                    TransitionNode(
                        id=transition_row.id,
                        entity_id=transition_row.entity_id,
                        name=transition_row.name,
                        config=transition_configs[transition_id],
                        target_activity_entity_id=entry.activity_entity_id,
                        actions=actions,
                    )
                )

            activities[entity_id] = ActivityNode(
                id=row.id,
                entity_id=row.entity_id,
                name=row.name,
                config=activity_config,
                activity_dialogs=dialogs,
                outgoing_transitions=outgoing,
            )

        return ProcessGraph(
            id=process_row.id,
            entity_id=process_row.entity_id,
            name=process_row.name,
            state_entity_id=process_row.state_entity_id,
            config=process_config,
            layout=layout,
            activities=activities,
        )

    async def get_activity(self, entity_id: str) -> PmActivity | None:
        return (
            await self._session.execute(select(PmActivity).where(PmActivity.entity_id == entity_id))
        ).scalar_one_or_none()

    async def get_activity_dialog(self, entity_id: str) -> PmActivityDialog | None:
        return (
            await self._session.execute(
                select(PmActivityDialog).where(PmActivityDialog.entity_id == entity_id)
            )
        ).scalar_one_or_none()

    async def _load_by_entity_id(
        self, model: type[_EntityRow], entity_ids: set[str]
    ) -> dict[str, _EntityRow]:
        """Fetch *model* rows whose ``entity_id`` is in *entity_ids*, keyed by entity_id."""
        if not entity_ids:
            return {}
        rows = (
            (await self._session.execute(select(model).where(model.entity_id.in_(entity_ids))))
            .scalars()
            .all()
        )
        return {row.entity_id: row for row in rows}
