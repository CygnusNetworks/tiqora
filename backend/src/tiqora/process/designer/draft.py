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
    PmEntitySync,
    PmProcess,
)
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.designer._rows import ENTITY_MODELS, as_mapping, as_text, order_key
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

_SYNC_STATES = ("not_sync", "deleted")


def _stringify_layout(layout: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        str(act): {str(k): str(v) for k, v in as_mapping(pos).items()}
        for act, pos in layout.items()
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
    config_raw = as_text(row.config)
    layout_raw = as_text(row.layout) if etype == "Process" else ""
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
    model = ENTITY_MODELS[etype]
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
        for tid in sorted(as_mapping(path.get(current)), key=order_key):
            target = str(
                as_mapping(as_mapping(path.get(current)).get(tid)).get("ActivityEntityID") or ""
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
    cfg = _load_yaml_mapping(as_text(process.config))
    path = as_mapping(cfg.get("Path"))

    # Activities: Path keys, the start activity and transition targets, in walk order.
    activity_ids = [str(a) for a in path]
    for transitions in path.values():
        for entry in as_mapping(transitions).values():
            activity_ids.append(str(as_mapping(entry).get("ActivityEntityID") or ""))
    start = str(cfg.get("StartActivity") or "")
    activity_ids = _path_order(start, path, _first_seen([*activity_ids, start]))
    transition_ids: list[str] = []
    action_ids: list[str] = []
    for aid in activity_ids:
        transitions = as_mapping(path.get(aid))
        for tid in sorted(transitions, key=order_key):
            transition_ids.append(str(tid))
            raw_actions = as_mapping(transitions[tid]).get("TransitionAction") or []
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
        dialogs = as_mapping(
            as_mapping(_load_yaml_mapping(as_text(row.config))).get("ActivityDialog")
        )
        dialog_ids.extend(str(dialogs[k]) for k in sorted(dialogs, key=order_key))
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
        layout=_stringify_layout(_load_yaml_mapping(as_text(process.layout))),
        activities=present("Activity", activity_ids, activity_rows),
        activity_dialogs=present("ActivityDialog", dialog_ids, dialog_rows),
        transitions=present("Transition", transition_ids, transition_rows),
        transition_actions=present("TransitionAction", action_ids, action_rows),
        missing_references=missing,
        pending_changes=int(pending),
    )


async def _configs(session: AsyncSession, etype: EntityType) -> dict[str, dict[str, Any]]:
    model = ENTITY_MODELS[etype]
    rows = await session.execute(select(model.entity_id, model.config))
    return {eid: _load_yaml_mapping(as_text(cfg)) for eid, cfg in rows.all()}


async def _usage(session: AsyncSession, etype: EntityType) -> dict[str, list[str]]:
    """``{entity_id: sorted user ids}`` for one entity type (mirrors ``entity_usage``)."""
    used: dict[str, set[str]] = {}
    if etype == "ActivityDialog":
        for aid, acfg in (await _configs(session, "Activity")).items():
            for did in as_mapping(acfg.get("ActivityDialog")).values():
                used.setdefault(str(did), set()).add(aid)
    elif etype != "Process":
        for pid, pcfg in (await _configs(session, "Process")).items():
            path = as_mapping(pcfg.get("Path"))
            if etype == "Activity":
                for aid in path:
                    used.setdefault(str(aid), set()).add(pid)
                continue
            for transitions in path.values():
                for tid, entry in as_mapping(transitions).items():
                    if etype == "Transition":
                        used.setdefault(str(tid), set()).add(pid)
                        continue
                    actions = as_mapping(entry).get("TransitionAction") or []
                    if isinstance(actions, list):
                        for ta in actions:
                            used.setdefault(str(ta), set()).add(pid)
    return {k: sorted(v) for k, v in used.items()}


async def list_entities(session: AsyncSession, entity_type: EntityType) -> list[EntitySummaryOut]:
    """Every entity of one type with the ids that use it, ordered by name."""
    if entity_type != "Process" and entity_type not in SUB_ENTITY_TYPES:
        raise ValueError(f"unknown entity type {entity_type!r}")
    model = ENTITY_MODELS[entity_type]
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


def _process_entity_keys(
    cfg: Mapping[str, Any], activity_configs: Mapping[str, Mapping[str, Any]]
) -> set[tuple[str, str]]:
    """``(entity_type, entity_id)`` of everything one process uses (same walk as the draft)."""
    path = as_mapping(cfg.get("Path"))
    keys: set[tuple[str, str]] = set()
    activity_ids = {str(a) for a in path}
    activity_ids.add(str(cfg.get("StartActivity") or ""))
    keys.add(("ActivityDialog", str(cfg.get("StartActivityDialog") or "")))
    for transitions in path.values():
        for tid, entry in as_mapping(transitions).items():
            keys.add(("Transition", str(tid)))
            activity_ids.add(str(as_mapping(entry).get("ActivityEntityID") or ""))
            actions = as_mapping(entry).get("TransitionAction") or []
            if isinstance(actions, list):
                keys.update(("TransitionAction", str(a)) for a in actions)
    for aid in activity_ids:
        keys.add(("Activity", aid))
        dialogs = as_mapping(as_mapping(activity_configs.get(aid)).get("ActivityDialog"))
        keys.update(("ActivityDialog", str(d)) for d in dialogs.values())
    return keys


async def list_process_summaries(session: AsyncSession) -> list[ProcessDraftSummaryOut]:
    """Processes with state, drafts included (ruling: the designer list shows drafts)."""
    rows = (
        await session.execute(
            select(
                PmProcess.entity_id, PmProcess.name, PmProcess.state_entity_id, PmProcess.config
            ).order_by(PmProcess.name)
        )
    ).all()
    states = await load_sync_states(session)
    activity_configs = await _configs(session, "Activity") if states else {}

    def has_draft_changes(eid: str, raw_config: object) -> bool:
        if not states:
            return False
        if ("Process", eid) in states:
            return True
        cfg = _load_yaml_mapping(as_text(raw_config))
        return any(k in states for k in _process_entity_keys(cfg, activity_configs))

    return [
        ProcessDraftSummaryOut(
            entity_type="Process",
            entity_id=eid,
            name=name,
            used_by=[],
            sync_state=_sync_state(states, "Process", eid),
            state_entity_id=state,
            state_name=PROCESS_STATE_NAMES.get(state, ""),
            has_draft_changes=has_draft_changes(eid, raw_config),
        )
        for eid, name, state, raw_config in rows
    ]
