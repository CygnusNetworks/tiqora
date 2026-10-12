"""Import and export of a process in Znuny's file format (plan D13).

Export: ``AdminProcessManagement.pm:215-245`` + ``_GetProcessData`` 2063-2129 (6.5.22).
Import: ``DB/Process.pm:1485-1860`` (``ProcessImport``) and ``_ImportedEntitiesUpdate``
1862-2118. Every entity goes through :class:`ProcessWriter`, so an imported process is a
draft (``not_sync`` rows; Deviation 2 of the plan, Znuny itself writes none). The caller owns
the transaction: any error rolls the session back, so a failed import leaves nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.designer._rows import ENTITY_MODELS, as_mapping, as_text, order_key
from tiqora.process.designer.ids import SUB_ENTITY_TYPES, EntityType, generate_entity_id
from tiqora.process.designer.writer import EntityNotFound, ProcessWriter
from tiqora.process.designer.yaml_codec import dump_znuny_yaml, load_znuny_yaml
from tiqora.process.dump import PROCESS_STATE_NAMES, format_time

MAX_CONTENT_BYTES = 1_000_000  # real Znuny exports are KB-sized (ruling: cap content at 1 MB)
MAX_ENTITIES = 1000

_SECTION = {
    "Activity": "Activities",
    "ActivityDialog": "ActivityDialogs",
    "Transition": "Transitions",
    "TransitionAction": "TransitionActions",
}
# Order of ``ProcessImport``'s "update all entities" phase; the process comes last.
_WRITE_ORDER: tuple[EntityType, ...] = (
    "ActivityDialog",
    "Activity",
    "Transition",
    "TransitionAction",
)
_HEX_ID = {
    t: re.compile(rf"\A{t}-[0-9a-f]{{32}}\Z")
    for t in ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction")
}

UNREADABLE = (
    "Die Prozessdatei konnte nicht gelesen werden. Bitte prüfen Sie, ob die Datei gültig ist."
)
TOO_LARGE = "Datei zu groß."


class ProcessImportIn(BaseModel):
    # No pydantic max_length: import_process_yaml checks the byte size and answers with the
    # German "Datei zu groß." instead of pydantic's generic English 422.
    content: str
    overwrite_existing: bool = False


class ProcessImportOut(BaseModel):
    process_entity_id: str
    name: str
    created: dict[str, int]  # per entity type
    updated: dict[str, int]


class ImportRefused(ValueError):
    """The file cannot be imported (422, Znuny's message text in German)."""


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


# --- export ---------------------------------------------------------------------------------


async def _rows(session: AsyncSession, etype: EntityType, ids: list[str]) -> dict[str, Any]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    model = ENTITY_MODELS[etype]
    found = (await session.execute(select(model).where(model.entity_id.in_(wanted)))).scalars()
    return {r.entity_id: r for r in found if r.entity_id in wanted}  # exact case only


def _entity(row: Any) -> dict[str, Any]:
    return {
        "ID": row.id,
        "EntityID": row.entity_id,
        "Name": row.name,
        "Config": _load_yaml_mapping(row.config),
        "CreateTime": format_time(row.create_time),
        "ChangeTime": format_time(row.change_time),
    }


async def export_process_yaml(session: AsyncSession, process_entity_id: str) -> tuple[str, str]:
    """``(filename, text)``: ``{Process, Activities, ActivityDialogs, Transitions,
    TransitionActions}`` as Znuny's ``ProcessGet``/``*Get`` return them (``ProcessGet``
    derives the entity lists from ``Path``, ``DB/Process.pm:400-545``)."""
    process = (await _rows(session, "Process", [process_entity_id])).get(process_entity_id)
    if process is None:
        raise EntityNotFound(process_entity_id)
    cfg = _load_yaml_mapping(process.config)
    path = as_mapping(cfg.get("Path"))
    activity_ids = sorted(path)
    transition_ids: list[str] = []
    action_ids: list[str] = []
    for act in activity_ids:
        for tid in sorted(as_mapping(path[act])):
            transition_ids.append(tid)
            actions = as_mapping(as_mapping(path[act])[tid]).get("TransitionAction")
            action_ids.extend(sorted(str(a) for a in _list(actions)))
    # The id lists keep repeats, exactly like ``ProcessGet`` (a transition used from two
    # activities is listed twice); the entity sections below are keyed, so they do not.

    activity_rows = await _rows(session, "Activity", activity_ids)
    activities: dict[str, Any] = {}
    dialog_ids: list[str] = []
    for eid in activity_ids:
        row = activity_rows.get(eid)
        if row is None:
            continue
        entry = _entity(row)
        by_order = as_mapping(entry["Config"].get("ActivityDialog"))
        entry["ActivityDialogs"] = [str(by_order[k]) for k in sorted(by_order, key=order_key)]
        dialog_ids.extend(entry["ActivityDialogs"])
        activities[eid] = entry
    dialog_rows = await _rows(session, "ActivityDialog", dialog_ids)
    transition_rows = await _rows(session, "Transition", transition_ids)
    action_rows = await _rows(session, "TransitionAction", action_ids)

    p_entry = {
        **_entity(process),
        "StateEntityID": process.state_entity_id,
        "State": PROCESS_STATE_NAMES.get(process.state_entity_id, ""),
        "Layout": _load_yaml_mapping(process.layout),
        "Activities": activity_ids,
        "Transitions": transition_ids,
        "TransitionActions": action_ids,
    }
    data: dict[str, Any] = {"Process": p_entry}
    for key, ids, rows in (
        ("Activities", activity_ids, None),
        ("ActivityDialogs", list(dict.fromkeys(dialog_ids)), dialog_rows),
        ("Transitions", list(dict.fromkeys(transition_ids)), transition_rows),
        ("TransitionActions", list(dict.fromkeys(action_ids)), action_rows),
    ):
        section = (
            activities if rows is None else {eid: _entity(rows[eid]) for eid in ids if eid in rows}
        )
        if section:  # Znuny leaves the key out when nothing was collected
            data[key] = section
    safe_id = re.sub(r"[^0-9A-Za-z_.-]", "_", process_entity_id)
    return f"Export_ProcessEntityID_{safe_id}.yml", dump_znuny_yaml(data)


# --- import ---------------------------------------------------------------------------------


def _parse(content: str) -> dict[str, Any]:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ImportRefused(TOO_LARGE)
    try:
        data = load_znuny_yaml(content)
    except (yaml.YAMLError, ValueError, RecursionError):
        raise ImportRefused(UNREADABLE) from None
    if not isinstance(data, dict) or not isinstance(data.get("Process"), dict):
        raise ImportRefused(UNREADABLE)
    return data


async def _missing_dynamic_fields(session: AsyncSession, dialogs: Mapping[str, Any]) -> list[str]:
    """``DB/Process.pm:1509-1550``: ``DynamicField_<Name>`` fields of the dialogs that no
    valid dynamic field of that name backs."""
    used: list[str] = []
    for eid in sorted(dialogs):
        fields = as_mapping(as_mapping(as_mapping(dialogs[eid]).get("Config")).get("Fields"))
        for field in sorted(fields):
            found = re.search(r"DynamicField_(\w+)", str(field))
            if found:
                used.append(found.group(1))
    if not used:
        return []
    present = set(
        (await session.execute(select(DynamicField.name).where(DynamicField.valid_id == 1)))
        .scalars()
        .all()
    )
    return [name for name in used if name not in present]


def _check_present(data: dict[str, Any]) -> None:
    """``DB/Process.pm:1552-1612``: everything the process lists must be in the file."""
    process = as_mapping(data["Process"])
    activities = as_mapping(data.get("Activities"))
    dialogs = as_mapping(data.get("ActivityDialogs"))
    used_dialogs: list[str] = []
    for raw in _list(process.get("Activities")):
        eid = as_text(raw)
        activity = activities.get(eid)
        if not isinstance(activity, dict):
            raise ImportRefused(f"Für Activity {eid} fehlen die Daten.")
        used_dialogs.extend(str(d) for d in _list(activity.get("ActivityDialogs")))
    for eid in used_dialogs:
        if not isinstance(dialogs.get(eid), dict):
            raise ImportRefused(f"Für ActivityDialog {eid} fehlen die Daten.")
    for key, section in (
        ("Transitions", "Transitions"),
        ("TransitionActions", "TransitionActions"),
    ):
        for raw in _list(process.get(key)):
            eid = as_text(raw)
            if not isinstance(as_mapping(data.get(section)).get(eid), dict):
                raise ImportRefused(f"Für {key[:-1]} {eid} fehlen die Daten.")


class _Mapping:
    """Old entity id -> new entity id, per type (``DB/Process.pm:1626-1718``).

    Without overwrite every entity gets a new id. With overwrite an id in Znuny's own format
    is kept, so the existing row is updated in place; anything else (a 3.3.x id such as
    ``A12``) gets a new one.
    """

    def __init__(self, overwrite: bool) -> None:
        self._overwrite = overwrite
        self._map: dict[str, dict[str, str]] = {t: {} for t in _HEX_ID}

    def add(self, etype: EntityType, old: str) -> str:
        keep = self._overwrite and _HEX_ID[etype].match(old) is not None
        new = old if keep else generate_entity_id(etype)
        self._map[etype][old] = new
        return new

    def __call__(self, etype: EntityType, old: object) -> str:
        new = self._map[etype].get(as_text(old))
        if not new:
            raise ImportRefused(f"Für {etype}: {as_text(old)} wurde keine Zuordnung gefunden.")
        return new


def _remap_process(
    process: dict[str, Any], mapping: _Mapping
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``_ImportedEntitiesUpdate`` (1862-2056): the process config and layout, remapped."""
    cfg = as_mapping(process.get("Config"))
    layout = {
        mapping("Activity", act): as_mapping(pos)
        for act, pos in sorted(as_mapping(process.get("Layout")).items())
    }
    new_path: dict[str, Any] = {}
    for act, transitions in sorted(as_mapping(cfg.get("Path")).items()):
        new_act = mapping("Activity", act)
        new_path[new_act] = {}
        for tid, spec in sorted(as_mapping(transitions).items()):
            spec_m = as_mapping(spec)
            new_spec: dict[str, Any] = {
                "ActivityEntityID": mapping("Activity", spec_m.get("ActivityEntityID"))
            }
            if "TransitionAction" in spec_m:
                new_spec["TransitionAction"] = [
                    mapping("TransitionAction", a) for a in _list(spec_m["TransitionAction"])
                ]
            new_path[new_act][mapping("Transition", tid)] = new_spec
    config: dict[str, Any] = {
        "Description": as_text(cfg.get("Description")),
        "Path": new_path,
        "StartActivity": "",
        "StartActivityDialog": "",
    }
    if cfg.get("StartActivity"):
        config["StartActivity"] = mapping("Activity", cfg["StartActivity"])
    if cfg.get("StartActivityDialog"):
        config["StartActivityDialog"] = mapping("ActivityDialog", cfg["StartActivityDialog"])
    return config, layout


def _remap_activity(config: dict[str, Any], mapping: _Mapping) -> dict[str, Any]:
    out = dict(config)
    if "ActivityDialog" in out:
        out["ActivityDialog"] = {
            str(k): mapping("ActivityDialog", v)
            for k, v in sorted(
                as_mapping(out["ActivityDialog"]).items(), key=lambda kv: order_key(kv[0])
            )
        }
    return out


async def _import(
    session: AsyncSession, writer: ProcessWriter, content: str, overwrite_existing: bool
) -> ProcessImportOut:
    data = _parse(content)
    process = as_mapping(data["Process"])
    sections = {etype: as_mapping(data.get(_SECTION[etype])) for etype in SUB_ENTITY_TYPES}
    if 1 + sum(len(s) for s in sections.values()) > MAX_ENTITIES:
        raise ImportRefused(TOO_LARGE)
    missing = await _missing_dynamic_fields(session, sections["ActivityDialog"])
    if missing:
        raise ImportRefused(
            f"Die folgenden Dynamischen Felder fehlen: {', '.join(missing)}. "
            "Der Import wurde abgebrochen."
        )
    _check_present(data)

    mapping = _Mapping(overwrite_existing)
    process_id = mapping.add("Process", as_text(process.get("EntityID")))
    for etype in SUB_ENTITY_TYPES:
        for old in sorted(sections[etype]):
            mapping.add(etype, old)

    process_config, process_layout = _remap_process(process, mapping)
    created: dict[str, int] = dict.fromkeys(("Process", *SUB_ENTITY_TYPES), 0)
    updated: dict[str, int] = dict(created)

    for etype in _WRITE_ORDER:
        for old, entry in sorted(sections[etype].items()):
            entry_m = as_mapping(entry)
            config = as_mapping(entry_m.get("Config"))
            if etype == "Activity":
                config = _remap_activity(config, mapping)
            _, was_created = await writer.import_entity(
                etype, mapping(etype, old), name=as_text(entry_m.get("Name")), config=config
            )
            (created if was_created else updated)[etype] += 1
    written, was_created = await writer.import_entity(
        "Process",
        process_id,
        name=as_text(process.get("Name")),
        config=process_config,
        layout=process_layout,
        state_entity_id=as_text(process.get("StateEntityID")),
    )
    (created if was_created else updated)["Process"] += 1
    return ProcessImportOut(
        process_entity_id=written.entity_id, name=written.name, created=created, updated=updated
    )


async def import_process_yaml(
    session: AsyncSession, writer: ProcessWriter, content: str, *, overwrite_existing: bool
) -> ProcessImportOut:
    """Parse, check and write a Znuny process file. Flushes only; the caller commits.

    Any error rolls the session back first, so a half-written import never survives.
    """
    try:
        return await _import(session, writer, content, overwrite_existing)
    except BaseException:
        await session.rollback()
        raise
