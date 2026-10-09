"""In-memory, fully-linked ProcessManagement object graph + async loader.

``ProcessRepository`` loads one process plus every activity, activity dialog,
transition and transition action it (transitively) references — by ``entity_id`` — into a
``ProcessGraph``: Process -> Activities -> ActivityDialogs, and
Activity -> outgoing Transitions -> target Activity + TransitionActions.

Definitions come from a :class:`~tiqora.process.source.ProcessSource` (the newest
deployed revision, or the live rows before the first one), never from ``pm_*``
directly. Read-only. No condition evaluation, no action dispatch — that is subtask 2's
``process/engine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.process.config import (
    ActivityConfig,
    ActivityDialogConfig,
    ProcessConfig,
    ProcessLayout,
    TransitionActionConfig,
    TransitionConfig,
)
from tiqora.process.dump import PROCESS_STATE_NAMES
from tiqora.process.source import EntityRecord, ProcessSource, active_process_source


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
    state_name: str


class ProcessRepository:
    """Async, read-only loader for the ProcessManagement object graph.

    ``state_entity_id`` is one of ``S1`` (Active), ``S2`` (Inactive) or
    ``S3`` (FadeAway). Znuny hardcodes these three in
    ``Kernel/System/ProcessManagement/DB/Process/State.pm`` (lines 48-50,
    67-69), not in ``general_catalog``; the names live in
    :data:`tiqora.process.dump.PROCESS_STATE_NAMES`. ``list_processes``
    returns ALL processes with their raw ``state_entity_id`` and
    leaves the "is this process active" decision to the caller.
    """

    def __init__(self, session: AsyncSession, source: ProcessSource | None = None) -> None:
        self._session = session
        self._source = source

    async def _get_source(self) -> ProcessSource:
        if self._source is None:
            self._source = await active_process_source(self._session)
        return self._source

    async def list_processes(self) -> list[ProcessSummary]:
        source = await self._get_source()
        return [
            ProcessSummary(
                id=rec.id,
                entity_id=rec.entity_id,
                name=rec.name,
                state_entity_id=rec.state_entity_id,
                state_name=PROCESS_STATE_NAMES.get(rec.state_entity_id, ""),
            )
            for rec in await source.list_processes()
        ]

    async def get_process(self, entity_id: str) -> ProcessGraph | None:
        source = await self._get_source()
        process_rec = (await source.load("Process", [entity_id])).get(entity_id)
        if process_rec is None:
            return None

        process_config = ProcessConfig.from_dict(process_rec.data)
        layout = ProcessLayout(positions=dict(process_rec.layout))

        activity_entity_ids = set(process_config.path.keys())
        for transitions in process_config.path.values():
            for entry in transitions.values():
                if entry.activity_entity_id:
                    activity_entity_ids.add(entry.activity_entity_id)
        if process_config.start_activity:
            activity_entity_ids.add(process_config.start_activity)

        activity_recs = await source.load("Activity", activity_entity_ids)
        activity_configs = {
            eid: ActivityConfig.from_dict(rec.data) for eid, rec in activity_recs.items()
        }

        dialog_entity_ids: set[str] = set()
        for ac in activity_configs.values():
            dialog_entity_ids.update(ac.ordered_activity_dialog_entity_ids)
        dialog_nodes = {
            eid: _dialog_node(rec)
            for eid, rec in (await source.load("ActivityDialog", dialog_entity_ids)).items()
        }

        transition_entity_ids: set[str] = set()
        for transitions in process_config.path.values():
            transition_entity_ids.update(transitions.keys())
        transition_recs = await source.load("Transition", transition_entity_ids)
        transition_configs = {
            eid: TransitionConfig.from_dict(rec.data) for eid, rec in transition_recs.items()
        }

        action_entity_ids: set[str] = set()
        for transitions in process_config.path.values():
            for entry in transitions.values():
                action_entity_ids.update(entry.transition_actions)
        action_nodes = {
            eid: TransitionActionNode(
                id=rec.id,
                entity_id=rec.entity_id,
                name=rec.name,
                config=TransitionActionConfig.from_dict(rec.data),
            )
            for eid, rec in (await source.load("TransitionAction", action_entity_ids)).items()
        }

        activities: dict[str, ActivityNode] = {}
        for entity_id_, rec in activity_recs.items():
            activity_config = activity_configs[entity_id_]
            dialogs = [
                dialog_nodes[d_id]
                for d_id in activity_config.ordered_activity_dialog_entity_ids
                if d_id in dialog_nodes
            ]

            outgoing: list[TransitionNode] = []
            for transition_id, entry in (process_config.path.get(entity_id_) or {}).items():
                transition_rec = transition_recs.get(transition_id)
                if transition_rec is None:
                    continue
                actions = [
                    action_nodes[a_id] for a_id in entry.transition_actions if a_id in action_nodes
                ]
                outgoing.append(
                    TransitionNode(
                        id=transition_rec.id,
                        entity_id=transition_rec.entity_id,
                        name=transition_rec.name,
                        config=transition_configs[transition_id],
                        target_activity_entity_id=entry.activity_entity_id,
                        actions=actions,
                    )
                )

            activities[entity_id_] = ActivityNode(
                id=rec.id,
                entity_id=rec.entity_id,
                name=rec.name,
                config=activity_config,
                activity_dialogs=dialogs,
                outgoing_transitions=outgoing,
            )

        return ProcessGraph(
            id=process_rec.id,
            entity_id=process_rec.entity_id,
            name=process_rec.name,
            state_entity_id=process_rec.state_entity_id,
            config=process_config,
            layout=layout,
            activities=activities,
        )

    async def get_activity_dialog(self, entity_id: str) -> ActivityDialogNode | None:
        source = await self._get_source()
        rec = (await source.load("ActivityDialog", [entity_id])).get(entity_id)
        return _dialog_node(rec) if rec is not None else None


def _dialog_node(rec: EntityRecord) -> ActivityDialogNode:
    return ActivityDialogNode(
        id=rec.id,
        entity_id=rec.entity_id,
        name=rec.name,
        config=ActivityDialogConfig.from_dict(rec.data),
    )
