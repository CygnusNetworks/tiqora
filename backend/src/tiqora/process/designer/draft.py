"""Draft reader: the ``pm_*`` rows as the designer sees them (drafts included).

Reads only (the writer owns all writes). Keys are loaded first and the related rows are
fetched with IN-lists; nothing joins ``pm_*`` with another table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmEntitySync,
    PmProcess,
    PmTransition,
    PmTransitionAction,
)
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.designer.ids import SUB_ENTITY_TYPES, EntityType
from tiqora.process.designer.schemas import (
    DesignerEntityOut,
    EntitySummaryOut,
    ProcessDraftOut,
    ProcessDraftSummaryOut,
    SyncState,
)
from tiqora.process.designer.versioning import entity_version
from tiqora.process.designer.writer import EntityNotFound
from tiqora.process.dump import PROCESS_STATE_NAMES

_MODELS: dict[str, Any] = {
    "Process": PmProcess,
    "Activity": PmActivity,
    "ActivityDialog": PmActivityDialog,
    "Transition": PmTransition,
    "TransitionAction": PmTransitionAction,
}
_SYNC_STATES = ("not_sync", "deleted")


def _text(value: object) -> str:
    if isinstance(value, memoryview | bytearray | bytes):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _order_key(key: object) -> tuple[int, int, str]:
    s = str(key)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def _stringify_layout(layout: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        str(act): {str(k): str(v) for k, v in _mapping(pos).items()} for act, pos in layout.items()
    }


async def load_sync_states(session: AsyncSession) -> dict[tuple[str, str], str]:
    """``{(entity_type, entity_id): sync_state}`` of every ``pm_entity_sync`` row."""
    rows = await session.execute(
        select(PmEntitySync.entity_type, PmEntitySync.entity_id, PmEntitySync.sync_state)
    )
    return {(str(t), str(e)): str(s) for t, e, s in rows.all()}


def _sync_state(states: Mapping[tuple[str, str], str], etype: str, eid: str) -> SyncState:
    state = states.get((etype, eid), "synced")
    return state if state in _SYNC_STATES else "synced"  # type: ignore[return-value]


def _entity_out(
    etype: EntityType, row: Any, states: Mapping[tuple[str, str], str]
) -> DesignerEntityOut:
    config_raw = _text(row.config)
    layout_raw = _text(row.layout) if etype == "Process" else ""
    state_entity_id = str(row.state_entity_id) if etype == "Process" else ""
    return DesignerEntityOut(
        entity_type=etype,
        entity_id=row.entity_id,
        id=row.id,
        name=row.name,
        config=_load_yaml_mapping(config_raw),
        version=entity_version(
            name=row.name, config=config_raw, layout=layout_raw, state_entity_id=state_entity_id
        ),
        sync_state=_sync_state(states, etype, row.entity_id),
        changed_at=row.change_time,
        changed_by=row.change_by,
    )


async def _load_rows(
    session: AsyncSession, etype: EntityType, entity_ids: Iterable[str]
) -> dict[str, Any]:
    wanted = sorted(set(entity_ids))
    if not wanted:
        return {}
    model = _MODELS[etype]
    rows = (await session.execute(select(model).where(model.entity_id.in_(wanted)))).scalars()
    # Exact-case match only (the IN above is collation-insensitive).
    return {r.entity_id: r for r in rows if r.entity_id in wanted}


def _first_seen(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def _path_order(start: str, path: Mapping[str, Any], known: list[str]) -> list[str]:
    """Activities in walking order: the start activity, then breadth first along the
    transitions; the stored ``Path`` is key-sorted (YAML dump), so its own order says nothing.
    Whatever the walk does not reach follows in stored order."""
    order: list[str] = []
    queue = [start] if start else []
    while queue:
        current = queue.pop(0)
        if current in order:
            continue
        order.append(current)
        for tid in sorted(_mapping(path.get(current)), key=_order_key):
            target = str(
                _mapping(_mapping(path.get(current)).get(tid)).get("ActivityEntityID") or ""
            )
            if target and target not in order:
                queue.append(target)
    return _first_seen([*order, *known])


async def load_process_draft(session: AsyncSession, process_entity_id: str) -> ProcessDraftOut:
    """One process with every entity it references, from the live ``pm_*`` rows."""
    process = (await _load_rows(session, "Process", [process_entity_id])).get(process_entity_id)
    if process is None:
        raise EntityNotFound(process_entity_id)
    states = await load_sync_states(session)
    cfg = _load_yaml_mapping(_text(process.config))
    path = _mapping(cfg.get("Path"))

    # Activities: Path keys, the start activity and transition targets, in walk order.
    activity_ids = [str(a) for a in path]
    for transitions in path.values():
        for entry in _mapping(transitions).values():
            activity_ids.append(str(_mapping(entry).get("ActivityEntityID") or ""))
    start = str(cfg.get("StartActivity") or "")
    activity_ids = _path_order(start, path, _first_seen([*activity_ids, start]))
    transition_ids: list[str] = []
    action_ids: list[str] = []
    for aid in activity_ids:
        transitions = _mapping(path.get(aid))
        for tid in sorted(transitions, key=_order_key):
            transition_ids.append(str(tid))
            raw_actions = _mapping(transitions[tid]).get("TransitionAction") or []
            if isinstance(raw_actions, list):
                action_ids.extend(str(a) for a in raw_actions)
    transition_ids = _first_seen(transition_ids)
    action_ids = _first_seen(action_ids)

    activity_rows = await _load_rows(session, "Activity", activity_ids)
    dialog_ids: list[str] = []
    for aid in activity_ids:
        row = activity_rows.get(aid)
        if row is None:
            continue
        dialogs = _mapping(_mapping(_load_yaml_mapping(_text(row.config))).get("ActivityDialog"))
        dialog_ids.extend(str(dialogs[k]) for k in sorted(dialogs, key=_order_key))
    dialog_ids.append(str(cfg.get("StartActivityDialog") or ""))
    dialog_ids = _first_seen(dialog_ids)

    dialog_rows = await _load_rows(session, "ActivityDialog", dialog_ids)
    transition_rows = await _load_rows(session, "Transition", transition_ids)
    action_rows = await _load_rows(session, "TransitionAction", action_ids)

    def present(
        etype: EntityType, ids: list[str], rows: Mapping[str, Any]
    ) -> list[DesignerEntityOut]:
        return [_entity_out(etype, rows[i], states) for i in ids if i in rows]

    missing = [
        i
        for ids, rows in (
            (activity_ids, activity_rows),
            (dialog_ids, dialog_rows),
            (transition_ids, transition_rows),
            (action_ids, action_rows),
        )
        for i in ids
        if i not in rows
    ]
    pending = (await session.execute(select(func.count()).select_from(PmEntitySync))).scalar_one()
    return ProcessDraftOut(
        process=_entity_out("Process", process, states),
        state_entity_id=process.state_entity_id,
        state_name=PROCESS_STATE_NAMES.get(process.state_entity_id, ""),
        layout=_stringify_layout(_load_yaml_mapping(_text(process.layout))),
        activities=present("Activity", activity_ids, activity_rows),
        activity_dialogs=present("ActivityDialog", dialog_ids, dialog_rows),
        transitions=present("Transition", transition_ids, transition_rows),
        transition_actions=present("TransitionAction", action_ids, action_rows),
        missing_references=missing,
        pending_changes=int(pending),
    )


async def _configs(session: AsyncSession, etype: EntityType) -> dict[str, dict[str, Any]]:
    model = _MODELS[etype]
    rows = await session.execute(select(model.entity_id, model.config))
    return {eid: _load_yaml_mapping(_text(cfg)) for eid, cfg in rows.all()}


async def _usage(session: AsyncSession, etype: EntityType) -> dict[str, list[str]]:
    """``{entity_id: sorted user ids}`` for one entity type (mirrors ``entity_usage``)."""
    used: dict[str, set[str]] = {}
    if etype == "ActivityDialog":
        for aid, acfg in (await _configs(session, "Activity")).items():
            for did in _mapping(acfg.get("ActivityDialog")).values():
                used.setdefault(str(did), set()).add(aid)
    elif etype != "Process":
        for pid, pcfg in (await _configs(session, "Process")).items():
            path = _mapping(pcfg.get("Path"))
            if etype == "Activity":
                for aid in path:
                    used.setdefault(str(aid), set()).add(pid)
                continue
            for transitions in path.values():
                for tid, entry in _mapping(transitions).items():
                    if etype == "Transition":
                        used.setdefault(str(tid), set()).add(pid)
                        continue
                    actions = _mapping(entry).get("TransitionAction") or []
                    if isinstance(actions, list):
                        for ta in actions:
                            used.setdefault(str(ta), set()).add(pid)
    return {k: sorted(v) for k, v in used.items()}


async def list_entities(session: AsyncSession, entity_type: EntityType) -> list[EntitySummaryOut]:
    """Every entity of one type with the ids that use it, ordered by name."""
    if entity_type != "Process" and entity_type not in SUB_ENTITY_TYPES:
        raise ValueError(f"unknown entity type {entity_type!r}")
    model = _MODELS[entity_type]
    rows = (await session.execute(select(model.entity_id, model.name).order_by(model.name))).all()
    states = await load_sync_states(session)
    used = await _usage(session, entity_type)
    return [
        EntitySummaryOut(
            entity_type=entity_type,
            entity_id=eid,
            name=name,
            used_by=used.get(eid, []),
            sync_state=_sync_state(states, entity_type, eid),
        )
        for eid, name in rows
    ]


async def load_entity(
    session: AsyncSession, entity_type: EntityType, entity_id: str
) -> DesignerEntityOut:
    """One entity as the designer sees it; :class:`EntityNotFound` when absent."""
    rows = await _load_rows(session, entity_type, [entity_id])
    row = rows.get(entity_id)
    if row is None:
        raise EntityNotFound(entity_id)
    return _entity_out(entity_type, row, await load_sync_states(session))


async def list_process_summaries(session: AsyncSession) -> list[ProcessDraftSummaryOut]:
    """Processes with state, drafts included (ruling: the designer list shows drafts)."""
    rows = (
        await session.execute(
            select(PmProcess.entity_id, PmProcess.name, PmProcess.state_entity_id).order_by(
                PmProcess.name
            )
        )
    ).all()
    states = await load_sync_states(session)
    return [
        ProcessDraftSummaryOut(
            entity_type="Process",
            entity_id=eid,
            name=name,
            used_by=[],
            sync_state=_sync_state(states, "Process", eid),
            state_entity_id=state,
            state_name=PROCESS_STATE_NAMES.get(state, ""),
        )
        for eid, name, state in rows
    ]
